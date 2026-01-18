from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from django.db import transaction
from django.utils import timezone

from accounts.models import User
from companies.models import Company
from tasksapp.models import Task


def _unfold_ics(text: str) -> str:
    """
    RFC5545 line folding: lines that start with a space/tab continue previous line.
    """
    lines = text.splitlines()
    out: list[str] = []
    for line in lines:
        if line.startswith((" ", "\t")) and out:
            out[-1] += line[1:]
        else:
            out.append(line)
    return "\n".join(out)


def _ics_unescape(s: str) -> str:
    s = s or ""
    # iCal escaping
    s = s.replace("\\n", "\n").replace("\\N", "\n")
    s = s.replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")
    try:
        s = html.unescape(s)
    except Exception:
        pass
    return s.strip()


def _parse_dt(value: str) -> datetime | None:
    v = (value or "").strip()
    if not v:
        return None
    # examples: 20240912T060000Z
    try:
        if v.endswith("Z"):
            dt = datetime.strptime(v, "%Y%m%dT%H%M%SZ")
            return dt.replace(tzinfo=timezone.utc)
        # fallback: naive local
        dt = datetime.strptime(v, "%Y%m%dT%H%M%S")
        return timezone.make_aware(dt, timezone.get_current_timezone())
    except Exception:
        return None


def _norm_company_name(s: str) -> str:
    s = _ics_unescape(s).lower()
    s = s.replace("ё", "е")
    s = re.sub(r"[\"'“”«»]+", " ", s)
    s = re.sub(r"\s+", " ", s)
    s = s.strip()
    return s


def _extract_company(contact: str, description: str, summary: str) -> str:
    """
    Best-effort: amo iCal often contains 'Компания: <name>' in CONTACT and/or DESCRIPTION.
    """
    for src in (contact, description, summary):
        s = _ics_unescape(src)
        m = re.search(r"Компания:\s*(.+)$", s, flags=re.IGNORECASE | re.MULTILINE)
        if m:
            return m.group(1).strip()
    # fallback: first line of description
    desc = _ics_unescape(description)
    first = desc.splitlines()[0].strip() if desc else ""
    if first and len(first) >= 3:
        return first
    return ""


@dataclass
class ImportTasksResult:
    total_events: int = 0
    created_tasks: int = 0
    skipped_existing: int = 0
    skipped_unmatched: int = 0
    skipped_old_tasks: int = 0
    linked_to_company: int = 0
    without_company: int = 0
    created_companies: int = 0
    preview: list[dict] | None = None


def import_amocrm_ics(
    *,
    ics_path: str | Path,
    encoding: str = "utf-8",
    dry_run: bool = False,
    limit_events: int = 200,
    actor: User | None = None,
    unmatched_mode: str = "keep",  # keep|skip|create_company
) -> ImportTasksResult:
    """
    Импорт задач из amoCRM iCal (.ics) в Task.
    Привязка к компании — по названию из CONTACT/описания ("Компания: ...") + best-effort fallback.
    Дедупликация — по (external_source='amo_ics', external_uid=UID).
    """
    ics_path = Path(ics_path)
    res = ImportTasksResult(preview=[])

    # company cache
    companies = list(Company.objects.only("id", "name", "responsible_id"))
    by_norm: dict[str, Company] = {}
    for c in companies:
        key = _norm_company_name(c.name)
        if key and key not in by_norm:
            by_norm[key] = c

    def find_company(name: str) -> Company | None:
        if not name:
            return None
        key = _norm_company_name(name)
        if not key:
            return None
        exact = by_norm.get(key)
        if exact:
            return exact
        # best-effort partial match (choose longest match)
        best = None
        best_len = 0
        for k, c in by_norm.items():
            if key in k or k in key:
                ln = min(len(k), len(key))
                if ln > best_len:
                    best_len = ln
                    best = c
        return best

    @transaction.atomic
    def _run():
        raw = ics_path.read_text(encoding=encoding, errors="replace")
        text = _unfold_ics(raw)
        # split VEVENT blocks
        blocks = text.split("BEGIN:VEVENT")
        for b in blocks[1:]:
            if "END:VEVENT" not in b:
                continue
            body = b.split("END:VEVENT", 1)[0]
            res.total_events += 1
            if limit_events and res.total_events > limit_events:
                break

            # parse properties
            props: dict[str, str] = {}
            for line in body.splitlines():
                if not line or ":" not in line:
                    continue
                k, v = line.split(":", 1)
                # drop parameters: DTSTART;TZID=... -> DTSTART
                k = k.split(";", 1)[0].strip().upper()
                props[k] = v.strip()

            uid = _ics_unescape(props.get("UID", ""))
            if uid and Task.objects.filter(external_source="amo_ics", external_uid=uid).exists():
                res.skipped_existing += 1
                continue

            summary = _ics_unescape(props.get("SUMMARY", "")) or "Задача (amo)"
            desc = _ics_unescape(props.get("DESCRIPTION", ""))
            contact = _ics_unescape(props.get("CONTACT", ""))
            dt_start = _parse_dt(props.get("DTSTART", "")) or _parse_dt(props.get("DUE", ""))

            # Фильтрация: импортируем только задачи с дедлайном на 2026 год и позже
            if dt_start and dt_start.year < 2026:
                res.skipped_old_tasks += 1
                continue
            
            company_name = _extract_company(contact, desc, summary)
            company = find_company(company_name)
            if company:
                res.linked_to_company += 1
            else:
                mode = (unmatched_mode or "keep").strip().lower()
                if mode == "skip":
                    res.skipped_unmatched += 1
                    continue
                if mode == "create_company":
                    # создаём компанию-заглушку, чтобы не потерять задачу (best-effort)
                    guess = company_name or ""
                    if not guess:
                        res.skipped_unmatched += 1
                        continue
                    # защитим от дублей: сначала ещё раз попробуем найти (на случай гонок)
                    company = find_company(guess)
                    if company is None:
                        company = Company(
                            name=guess[:255],
                            created_by=actor,
                            responsible=actor,
                            raw_fields={"source": "amo_ics_stub"},
                        )
                        if not dry_run:
                            company.save()
                        res.created_companies += 1
                        # добавим в кеш для последующих задач
                        key = _norm_company_name(company.name)
                        if key and key not in by_norm:
                            by_norm[key] = company
                    res.linked_to_company += 1
                else:
                    # keep: импортируем без компании
                    res.without_company += 1

            assigned_to = None
            if company and getattr(company, "responsible_id", None):
                assigned_to_id = company.responsible_id
                assigned_to = User.objects.filter(id=assigned_to_id, is_active=True).first()

            task = Task(
                title=(summary[:255] if summary else "Задача (amo)"),
                description=(desc or "")
                + (f"\n\n[Amo UID: {uid}]" if uid else "")
                + (f"\n[Amo Company: {company_name}]" if company_name and not company else ""),
                due_at=dt_start,
                company=company,
                created_by=actor,
                assigned_to=assigned_to or actor,
                external_source="amo_ics",
                external_uid=uid,
                status=Task.Status.NEW,
            )
            if not dry_run:
                task.save()

            res.created_tasks += 1
            if res.preview is not None and len(res.preview) < 20:
                res.preview.append(
                    {
                        "title": task.title,
                        "due_at": str(task.due_at) if task.due_at else "",
                        "company": company.name if company else "",
                        "company_guess": company_name,
                    }
                )

        if dry_run:
            transaction.set_rollback(True)

    _run()
    return res


