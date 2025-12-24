from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import json

from django.db import transaction
from django.utils import timezone

from accounts.models import User
from companies.models import Company, CompanyNote, CompanySphere
from tasksapp.models import Task

from .client import AmoClient


def _norm(s: str) -> str:
    return (s or "").strip().lower()


def _map_amo_user_to_local(amo_user: dict[str, Any]) -> User | None:
    """
    Best-effort сопоставление пользователя amo -> локальный User по имени.
    В amo имя может быть "Иванова Юлия Олеговна", а у нас "Иванова Юлия".
    """
    name = (amo_user.get("name") or "").strip()
    if not name:
        return None
    parts = [p for p in name.split(" ") if p]
    if len(parts) >= 2:
        ln, fn = parts[0], parts[1]
        u = User.objects.filter(last_name__iexact=ln, first_name__iexact=fn, is_active=True).first()
        if u:
            return u
    # fallback: contains
    for u in User.objects.filter(is_active=True):
        if _norm(name) in _norm(str(u)) or _norm(str(u)) in _norm(name):
            return u
    return None


def _extract_custom_values(company: dict[str, Any], field_id: int) -> list[dict[str, Any]]:
    vals = company.get("custom_fields_values") or []
    if not isinstance(vals, list):
        return []
    for cf in vals:
        if int(cf.get("field_id") or 0) == int(field_id):
            v = cf.get("values") or []
            return v if isinstance(v, list) else []
    return []


def _build_field_meta(fields: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    for f in fields or []:
        try:
            fid = int(f.get("id") or 0)
        except Exception:
            fid = 0
        if not fid:
            continue
        out[fid] = {"id": fid, "name": str(f.get("name") or ""), "code": str(f.get("code") or ""), "type": f.get("type")}
    return out


def _custom_values_text(company: dict[str, Any], field_id: int) -> list[str]:
    vals = _extract_custom_values(company, field_id)
    out = []
    for v in vals:
        s = str(v.get("value") or "").strip()
        if s:
            out.append(s)
    return out


def _find_field_id(field_meta: dict[int, dict[str, Any]], *, codes: list[str] | None = None, name_contains: list[str] | None = None) -> int | None:
    codes_l = [c.lower() for c in (codes or [])]
    name_l = [n.lower() for n in (name_contains or [])]
    for fid, m in field_meta.items():
        code = str(m.get("code") or "").lower()
        name = str(m.get("name") or "").lower()
        if codes_l and code and any(code == c for c in codes_l):
            return fid
        if name_l and name and any(n in name for n in name_l):
            return fid
    return None


def _extract_company_fields(amo_company: dict[str, Any], field_meta: dict[int, dict[str, Any]]) -> dict[str, str]:
    """
    Best-effort извлечение полей компании из custom_fields_values.
    """
    def first(fid: int | None) -> str:
        if not fid:
            return ""
        vals = _custom_values_text(amo_company, fid)
        return vals[0] if vals else ""

    def join(fid: int | None) -> str:
        if not fid:
            return ""
        vals = _custom_values_text(amo_company, fid)
        return ", ".join(vals)[:500]

    fid_inn = _find_field_id(field_meta, codes=["inn"], name_contains=["инн"])
    fid_kpp = _find_field_id(field_meta, codes=["kpp"], name_contains=["кпп"])
    fid_legal = _find_field_id(field_meta, name_contains=["юрид", "юр."])
    fid_addr = _find_field_id(field_meta, codes=["address"], name_contains=["адрес"])
    fid_phone = _find_field_id(field_meta, codes=["phone"], name_contains=["телефон"])
    fid_email = _find_field_id(field_meta, codes=["email"], name_contains=["email", "e-mail", "почта"])
    fid_web = _find_field_id(field_meta, codes=["web"], name_contains=["сайт", "web"])

    return {
        "inn": first(fid_inn),
        "kpp": first(fid_kpp),
        "legal_name": first(fid_legal),
        "address": first(fid_addr),
        "phone": join(fid_phone),
        "email": join(fid_email),
        "website": first(fid_web),
    }

def _custom_has_value(company: dict[str, Any], field_id: int, *, option_id: int | None = None, label: str | None = None) -> bool:
    values = _extract_custom_values(company, field_id)
    if option_id is not None:
        for v in values:
            if int(v.get("enum_id") or 0) == int(option_id):
                return True
    if label:
        lab = _norm(label)
        for v in values:
            if _norm(str(v.get("value") or "")) == lab:
                return True
    return False


@dataclass
class AmoMigrateResult:
    companies_seen: int = 0
    companies_matched: int = 0  # всего по фильтру
    companies_batch: int = 0  # обработано в этой пачке
    companies_offset: int = 0
    companies_next_offset: int = 0
    companies_has_more: bool = False
    companies_created: int = 0
    companies_updated: int = 0

    tasks_seen: int = 0
    tasks_created: int = 0
    tasks_skipped_existing: int = 0
    tasks_updated: int = 0

    notes_seen: int = 0
    notes_created: int = 0
    notes_skipped_existing: int = 0
    notes_updated: int = 0

    preview: list[dict] | None = None


def fetch_amo_users(client: AmoClient) -> list[dict[str, Any]]:
    return client.get_all_pages("/api/v4/users", embedded_key="users", limit=250)


def fetch_company_custom_fields(client: AmoClient) -> list[dict[str, Any]]:
    data = client.get("/api/v4/companies/custom_fields") or {}
    emb = data.get("_embedded") or {}
    fields = emb.get("custom_fields") or []
    return fields if isinstance(fields, list) else []


def _field_options(field: dict[str, Any]) -> list[dict[str, Any]]:
    # мультиселекты обычно имеют enums
    enums = field.get("enums") or {}
    out = []
    if isinstance(enums, dict):
        for k, v in enums.items():
            try:
                out.append({"id": int(k), "value": str(v)})
            except Exception:
                pass
    return out


def fetch_companies_by_responsible(client: AmoClient, responsible_user_id: int, *, limit_pages: int = 200) -> list[dict[str, Any]]:
    # amo v4: /api/v4/companies?filter[responsible_user_id]=...
    return client.get_all_pages(
        "/api/v4/companies",
        params={f"filter[responsible_user_id]": responsible_user_id, "with": "custom_fields"},
        embedded_key="companies",
        limit=250,
        max_pages=limit_pages,
    )


def fetch_tasks_for_companies(client: AmoClient, company_ids: list[int]) -> list[dict[str, Any]]:
    if not company_ids:
        return []
    # amo v4 tasks: /api/v4/tasks?filter[entity_type]=companies&filter[entity_id][]=...
    # Важно: режем на пачки, иначе URL может стать слишком длинным.
    out: list[dict[str, Any]] = []
    batch = 50
    for i in range(0, len(company_ids), batch):
        ids = company_ids[i : i + batch]
        out.extend(
            client.get_all_pages(
                "/api/v4/tasks",
                params={f"filter[entity_type]": "companies", f"filter[entity_id][]": ids},
                embedded_key="tasks",
                limit=250,
                max_pages=200,
            )
        )
    return out


def fetch_notes_for_companies(client: AmoClient, company_ids: list[int]) -> list[dict[str, Any]]:
    if not company_ids:
        return []
    # В amoCRM заметки обычно берутся не общим /notes, а из сущности:
    # /api/v4/companies/{id}/notes
    out: list[dict[str, Any]] = []
    for cid in company_ids:
        out.extend(
            client.get_all_pages(
                f"/api/v4/companies/{int(cid)}/notes",
                params={},
                embedded_key="notes",
                limit=250,
                max_pages=50,
            )
        )
    return out


def _upsert_company_from_amo(
    *,
    amo_company: dict[str, Any],
    actor: User,
    responsible: User | None,
    dry_run: bool,
) -> tuple[Company, bool]:
    amo_id = int(amo_company.get("id") or 0)
    name = str(amo_company.get("name") or "").strip() or "(без названия)"
    company = Company.objects.filter(amocrm_company_id=amo_id).first()
    created = False
    if company is None:
        company = Company(name=name, created_by=actor, responsible=responsible, amocrm_company_id=amo_id, raw_fields={"source": "amo_api"})
        created = True
    else:
        if name and company.name != name:
            company.name = name
    # сохраняем raw_fields (не ломаем существующие)
    try:
        rf = dict(company.raw_fields or {})
    except Exception:
        rf = {}
    rf["amo_api_last"] = amo_company
    company.raw_fields = rf
    if responsible and company.responsible_id != responsible.id:
        company.responsible = responsible
    if not dry_run:
        company.save()
    return company, created


def _apply_spheres_from_custom(
    *,
    amo_company: dict[str, Any],
    company: Company,
    field_id: int,
    dry_run: bool,
) -> None:
    values = _extract_custom_values(amo_company, field_id)
    labels = []
    for v in values:
        lab = str(v.get("value") or "").strip()
        if lab:
            labels.append(lab)
    if not labels or dry_run:
        return
    objs = []
    for lab in labels:
        obj, _ = CompanySphere.objects.get_or_create(name=lab)
        objs.append(obj)
    if objs:
        company.spheres.set(objs)


def migrate_filtered(
    *,
    client: AmoClient,
    actor: User,
    responsible_user_id: int,
    sphere_field_id: int,
    sphere_option_id: int | None,
    sphere_label: str | None,
    limit_companies: int = 0,  # размер пачки
    offset: int = 0,
    dry_run: bool = True,
    import_tasks: bool = True,
    import_notes: bool = True,
    company_fields_meta: list[dict[str, Any]] | None = None,
) -> AmoMigrateResult:
    res = AmoMigrateResult(preview=[])

    amo_users = fetch_amo_users(client)
    amo_user_by_id = {int(u.get("id") or 0): u for u in amo_users if int(u.get("id") or 0)}
    responsible_local = _map_amo_user_to_local(amo_user_by_id.get(int(responsible_user_id)) or {})
    field_meta = _build_field_meta(company_fields_meta or [])

    companies = fetch_companies_by_responsible(client, responsible_user_id)
    res.companies_seen = len(companies)
    matched_all = []
    for c in companies:
        if _custom_has_value(c, sphere_field_id, option_id=sphere_option_id, label=sphere_label):
            matched_all.append(c)
    res.companies_matched = len(matched_all)

    off = max(int(offset or 0), 0)
    batch_size = int(limit_companies or 0)
    if batch_size <= 0:
        batch_size = 50
    batch = matched_all[off : off + batch_size]
    res.companies_offset = off
    res.companies_batch = len(batch)
    res.companies_next_offset = off + len(batch)
    res.companies_has_more = res.companies_next_offset < len(matched_all)

    @transaction.atomic
    def _run():
        local_companies: list[Company] = []
        for amo_c in batch:
            extra = _extract_company_fields(amo_c, field_meta) if field_meta else {}
            comp, created = _upsert_company_from_amo(amo_company=amo_c, actor=actor, responsible=responsible_local, dry_run=dry_run)
            # заполнение "Данные" (только если поле пустое, чтобы не затереть уже заполненное вручную)
            changed = False
            if extra.get("legal_name") and not (comp.legal_name or "").strip():
                comp.legal_name = extra["legal_name"]
                changed = True
            if extra.get("inn") and not (comp.inn or "").strip():
                comp.inn = extra["inn"]
                changed = True
            if extra.get("kpp") and not (comp.kpp or "").strip():
                comp.kpp = extra["kpp"]
                changed = True
            if extra.get("address") and not (comp.address or "").strip():
                comp.address = extra["address"]
                changed = True
            if extra.get("phone") and not (comp.phone or "").strip():
                comp.phone = extra["phone"][:50]
                changed = True
            if extra.get("email") and not (comp.email or "").strip():
                comp.email = extra["email"][:254]
                changed = True
            if extra.get("website") and not (comp.website or "").strip():
                comp.website = extra["website"][:255]
                changed = True
            if changed and not dry_run:
                comp.save()
            if created:
                res.companies_created += 1
            else:
                res.companies_updated += 1
            _apply_spheres_from_custom(amo_company=amo_c, company=comp, field_id=sphere_field_id, dry_run=dry_run)
            local_companies.append(comp)
            if res.preview is not None and len(res.preview) < 15:
                res.preview.append({"company": comp.name, "amo_id": comp.amocrm_company_id})

        amo_ids = [int(c.get("id") or 0) for c in batch if int(c.get("id") or 0)]

        if import_tasks and amo_ids:
            tasks = fetch_tasks_for_companies(client, amo_ids)
            res.tasks_seen = len(tasks)
            for t in tasks:
                tid = int(t.get("id") or 0)
                existing = Task.objects.filter(external_source="amo_api", external_uid=str(tid)).first() if tid else None
                entity_id = int((t.get("entity_id") or 0) or 0)
                company = Company.objects.filter(amocrm_company_id=entity_id).first() if entity_id else None
                title = str(t.get("text") or t.get("result") or t.get("name") or "Задача (amo)").strip()[:255]
                due_at = None
                ts = t.get("complete_till") or t.get("complete_till_at") or None
                try:
                    if ts is not None and str(ts).strip():
                        ts_int = int(str(ts).strip())
                        if ts_int > 10**12:
                            ts_int = int(ts_int / 1000)
                        if ts_int > 0:
                            due_at = timezone.datetime.fromtimestamp(ts_int, tz=timezone.utc)
                except Exception:
                    due_at = None
                assigned_to = None
                rid = int(t.get("responsible_user_id") or 0)
                if rid:
                    assigned_to = _map_amo_user_to_local(amo_user_by_id.get(rid) or {})
                if existing:
                    # апдейтим то, что у вас сейчас выглядит "криво": дедлайн + убрать мусорный id в описании
                    upd = False
                    if title and (existing.title or "").strip() != title:
                        existing.title = title
                        upd = True
                    if existing.description and "[Amo task id:" in existing.description:
                        existing.description = ""
                        upd = True
                    if due_at and existing.due_at is None:
                        existing.due_at = due_at
                        upd = True
                    if company and existing.company_id is None:
                        existing.company = company
                        upd = True
                    if assigned_to and existing.assigned_to_id != assigned_to.id:
                        existing.assigned_to = assigned_to
                        upd = True
                    if upd and not dry_run:
                        existing.save()
                    res.tasks_updated += 1
                    res.tasks_skipped_existing += 1
                    continue

                task = Task(
                    title=title,
                    description="",
                    due_at=due_at,
                    company=company,
                    created_by=actor,
                    assigned_to=assigned_to or actor,
                    external_source="amo_api",
                    external_uid=str(tid),
                    status=Task.Status.NEW,
                )
                if not dry_run:
                    task.save()
                res.tasks_created += 1

        if import_notes and amo_ids:
            try:
                notes = fetch_notes_for_companies(client, amo_ids)
                res.notes_seen = len(notes)
                for n in notes:
                    nid = int(n.get("id") or 0)
                    existing_note = CompanyNote.objects.filter(external_source="amo_api", external_uid=str(nid)).first() if nid else None

                    # В карточечных notes entity_id часто = id компании в amo
                    entity_id = int((n.get("entity_id") or 0) or 0)
                    company = Company.objects.filter(amocrm_company_id=entity_id).first() if entity_id else None
                    if not company:
                        continue

                    # В разных типах notes текст может лежать по-разному
                    params = n.get("params") or {}
                    note_type = str(n.get("note_type") or n.get("type") or "").strip()
                    text = str(
                        n.get("text")
                        or params.get("text")
                        or params.get("comment")
                        or params.get("note")
                        or n.get("note")
                        or ""
                    ).strip()
                    if not text:
                        try:
                            text = json.dumps(params, ensure_ascii=False)[:1200] if params else ""
                        except Exception:
                            text = ""
                        if not text:
                            text = f"(без текста) note_type={note_type}"

                    # автор заметки (если можем определить)
                    author = None
                    author_amo_name = ""
                    creator_id = int(n.get("created_by") or n.get("created_by_id") or n.get("responsible_user_id") or 0)
                    if creator_id:
                        au = amo_user_by_id.get(creator_id) or {}
                        author_amo_name = str(au.get("name") or "")
                        author = _map_amo_user_to_local(au)

                    created_ts = n.get("created_at") or n.get("created_at_ts") or None
                    created_label = ""
                    try:
                        if created_ts:
                            ct = int(str(created_ts))
                            if ct > 10**12:
                                ct = int(ct / 1000)
                            created_label = timezone.datetime.fromtimestamp(ct, tz=timezone.utc).strftime("%d.%m.%Y %H:%M")
                    except Exception:
                        created_label = ""

                    prefix = "Импорт из amo"
                    meta_bits = []
                    if author_amo_name:
                        meta_bits.append(f"автор: {author_amo_name}")
                    if created_label:
                        meta_bits.append(f"дата: {created_label}")
                    if note_type:
                        meta_bits.append(f"type: {note_type}")
                    if nid:
                        meta_bits.append(f"id: {nid}")
                    if meta_bits:
                        prefix += " (" + ", ".join(meta_bits) + ")"
                    text_full = prefix + "\n" + text

                    if existing_note:
                        # если раньше создали "пустышку" — обновим
                        upd = False
                        if existing_note.company_id != company.id:
                            existing_note.company = company
                            upd = True
                        if existing_note.text.strip().startswith("Импорт из amo (note id") or len(existing_note.text.strip()) < 40:
                            existing_note.text = text_full[:8000]
                            upd = True
                        if existing_note.author_id == actor.id and (author is None or author.id != actor.id):
                            existing_note.author = author  # может быть None
                            upd = True
                        if upd and not dry_run:
                            existing_note.save()
                        res.notes_updated += 1
                        res.notes_skipped_existing += 1
                        continue

                    note = CompanyNote(
                        company=company,
                        author=author,  # НЕ actor, чтобы не выглядело "как будто вы писали"
                        text=text_full[:8000],
                        external_source="amo_api",
                        external_uid=str(nid) if nid else "",
                    )
                    if not dry_run:
                        note.save()
                    res.notes_created += 1
            except Exception:
                # Если заметки недоступны в конкретном аккаунте/тарифе/правах — не валим всю миграцию.
                res.notes_seen = 0
                res.notes_created = 0
                res.notes_skipped_existing = 0

        if dry_run:
            transaction.set_rollback(True)

    _run()
    return res


