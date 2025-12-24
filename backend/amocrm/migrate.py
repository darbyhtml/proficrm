from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import json

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime, parse_date
from datetime import datetime, time, timezone as dt_timezone

from accounts.models import User
from companies.models import Company, CompanyNote, CompanySphere, Contact, ContactEmail, ContactPhone
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


def _fmt_duration(seconds: Any) -> str:
    try:
        s = int(seconds or 0)
    except Exception:
        s = 0
    if s <= 0:
        return "0с"
    m, sec = divmod(s, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}ч {m}м"
    if m:
        return f"{m}м {sec}с"
    return f"{sec}с"


def _as_text(v: Any) -> str:
    try:
        return str(v or "").strip()
    except Exception:
        return ""


def _format_call_note(note_type: str, params: Any) -> str:
    p = params if isinstance(params, dict) else {}
    incoming = note_type.lower().endswith("_in") or bool(p.get("incoming"))
    direction = "Входящий" if incoming else "Исходящий"
    src = _as_text(p.get("source"))
    uniq = _as_text(p.get("uniq") or p.get("unique") or p.get("call_id"))
    dur = _fmt_duration(p.get("duration"))
    phone = _as_text(p.get("phone") or p.get("phone_number") or p.get("number") or p.get("to") or p.get("from"))
    result = _as_text(p.get("result") or p.get("status") or p.get("call_status"))
    link = _as_text(p.get("link") or p.get("record_link") or p.get("record_url"))

    lines = []
    lines.append(f"Звонок · {direction}")
    if phone:
        lines.append("Номер: " + phone)
    if dur:
        lines.append("Длительность: " + dur)
    if src:
        lines.append("Источник: " + src)
    if uniq:
        lines.append("ID: " + uniq)
    if result:
        lines.append("Статус: " + result)
    if link:
        lines.append("Запись: " + link)
    return "\n".join(lines) if lines else "Звонок"


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


def _split_multi(s: str) -> list[str]:
    """
    В amo часто телефоны/почты лежат в одной строке через запятую/точку с запятой/переносы.
    """
    if not s:
        return []
    raw = str(s).replace("\r", "\n")
    parts: list[str] = []
    for chunk in raw.split("\n"):
        for p in chunk.replace(";", ",").split(","):
            v = p.strip()
            if v:
                parts.append(v)
    out: list[str] = []
    seen = set()
    for v in parts:
        k = v.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(v)
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

    def list_vals(fid: int | None) -> list[str]:
        if not fid:
            return []
        vals = _custom_values_text(amo_company, fid)
        out: list[str] = []
        for s in vals:
            out.extend(_split_multi(s))
        return out

    fid_inn = _find_field_id(field_meta, codes=["inn"], name_contains=["инн"])
    fid_kpp = _find_field_id(field_meta, codes=["kpp"], name_contains=["кпп"])
    fid_legal = _find_field_id(field_meta, name_contains=["юрид", "юр."])
    fid_addr = _find_field_id(field_meta, codes=["address"], name_contains=["адрес"])
    fid_phone = _find_field_id(field_meta, codes=["phone"], name_contains=["телефон"])
    fid_email = _find_field_id(field_meta, codes=["email"], name_contains=["email", "e-mail", "почта"])
    fid_web = _find_field_id(field_meta, codes=["web"], name_contains=["сайт", "web"])
    fid_director = _find_field_id(field_meta, name_contains=["руководитель", "директор", "генеральный"])

    return {
        "inn": first(fid_inn),
        "kpp": first(fid_kpp),
        "legal_name": first(fid_legal),
        "address": first(fid_addr),
        "phones": list_vals(fid_phone),
        "emails": list_vals(fid_email),
        "website": first(fid_web),
        "director": first(fid_director),
    }


def _parse_amo_due(ts: Any) -> timezone.datetime | None:
    """
    amo может отдавать дедлайн как:
    - unix seconds int
    - unix ms int
    - строка с цифрами
    - ISO datetime string
    - ISO date string
    """
    if ts is None:
        return None
    UTC = getattr(timezone, "UTC", dt_timezone.utc)
    # dict wrapper
    if isinstance(ts, dict):
        for k in ("timestamp", "ts", "value"):
            if k in ts:
                return _parse_amo_due(ts.get(k))
        return None

    # numeric string / int
    if isinstance(ts, (int, float)) or (isinstance(ts, str) and ts.strip().isdigit()):
        try:
            ts_int = int(str(ts).strip())
        except Exception:
            ts_int = 0
        if ts_int <= 0:
            return None
        if ts_int > 10**12:
            ts_int = int(ts_int / 1000)
        try:
            return timezone.datetime.fromtimestamp(ts_int, tz=UTC)
        except Exception:
            return None

    # datetime string
    if isinstance(ts, str):
        s = ts.strip()
        if not s:
            return None
        dt = parse_datetime(s)
        if dt:
            if timezone.is_naive(dt):
                dt = timezone.make_aware(dt, timezone=UTC)
            return dt
        d = parse_date(s)
        if d:
            dt2 = datetime.combine(d, time(12, 0))
            return timezone.make_aware(dt2, timezone=UTC)
    return None

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
    tasks_preview: list[dict] | None = None

    notes_seen: int = 0
    notes_created: int = 0
    notes_skipped_existing: int = 0
    notes_updated: int = 0
    notes_preview: list[dict] | None = None

    contacts_seen: int = 0
    contacts_created: int = 0
    contacts_preview: list[dict] | None = None  # для dry-run отладки

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


def fetch_contacts_for_companies(client: AmoClient, company_ids: list[int]) -> list[dict[str, Any]]:
    """
    Получает контакты компаний из amoCRM.
    В amoCRM контакты связаны с компаниями через /api/v4/companies/{id}/links (или через /api/v4/contacts?filter[company_id]=...)
    """
    if not company_ids:
        return []
    out: list[dict[str, Any]] = []
    # amo v4: можно получить контакты через /api/v4/contacts?filter[company_id][]=...
    # Но лучше через /api/v4/companies/{id}/links, если доступно
    # Попробуем оба варианта
    batch = 50
    for i in range(0, len(company_ids), batch):
        ids = company_ids[i : i + batch]
        try:
            # Вариант 1: через filter[company_id]
            contacts = client.get_all_pages(
                "/api/v4/contacts",
                params={f"filter[company_id][]": ids},
                embedded_key="contacts",
                limit=250,
                max_pages=200,
            )
            out.extend(contacts)
        except Exception:
            # Вариант 2: через links для каждой компании
            for cid in ids:
                try:
                    links_data = client.get(f"/api/v4/companies/{int(cid)}/links") or {}
                    embedded = links_data.get("_embedded") or {}
                    contacts = embedded.get("contacts") or []
                    if isinstance(contacts, list):
                        out.extend(contacts)
                except Exception:
                    pass
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
    exclude_label: str | None = None,
) -> None:
    """
    Применяет сферы из кастомного поля amoCRM к компании.
    exclude_label: если указано, исключает эту сферу из импорта (например "Новая CRM").
    """
    values = _extract_custom_values(amo_company, field_id)
    labels = []
    exclude_norm = _norm(exclude_label) if exclude_label else ""
    for v in values:
        lab = str(v.get("value") or "").strip()
        if lab and _norm(lab) != exclude_norm:
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
    import_contacts: bool = False,  # по умолчанию выключено, т.к. может быть медленно
    company_fields_meta: list[dict[str, Any]] | None = None,
) -> AmoMigrateResult:
    res = AmoMigrateResult(preview=[], tasks_preview=[], notes_preview=[], contacts_preview=[])

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
    # Защита от offset за пределами списка
    if off >= len(matched_all):
        batch = []
        res.companies_offset = off
        res.companies_batch = 0
        res.companies_next_offset = off
        res.companies_has_more = False
    else:
        batch = matched_all[off : off + batch_size]
        res.companies_offset = off
        res.companies_batch = len(batch)
        res.companies_next_offset = off + len(batch)
        res.companies_has_more = res.companies_next_offset < len(matched_all)

    @transaction.atomic
    def _run():
        # Защита от пустого batch (когда offset за пределами списка)
        if not batch:
            return res
        
        local_companies: list[Company] = []
        for amo_c in batch:
            extra = _extract_company_fields(amo_c, field_meta) if field_meta else {}
            comp, created = _upsert_company_from_amo(amo_company=amo_c, actor=actor, responsible=responsible_local, dry_run=dry_run)
            # заполнение "Данные" (только если поле пустое, чтобы не затереть уже заполненное вручную)
            changed = False
            if extra.get("legal_name") and not (comp.legal_name or "").strip():
                comp.legal_name = str(extra["legal_name"])[:255]  # max_length=255
                changed = True
            if extra.get("inn") and not (comp.inn or "").strip():
                comp.inn = str(extra["inn"])[:20]  # max_length=20
                changed = True
            if extra.get("kpp") and not (comp.kpp or "").strip():
                comp.kpp = str(extra["kpp"])[:20]  # max_length=20
                changed = True
            if extra.get("address") and not (comp.address or "").strip():
                comp.address = str(extra["address"])[:500]  # max_length=500
                changed = True
            phones = extra.get("phones") or []
            emails = extra.get("emails") or []
            # основной телефон/почта — в "Данные", остальные — в отдельный контакт (даже без ФИО/должности)
            if phones and not (comp.phone or "").strip():
                comp.phone = str(phones[0])[:50]
                changed = True
            if emails and not (comp.email or "").strip():
                comp.email = str(emails[0])[:254]
                changed = True
            if extra.get("website") and not (comp.website or "").strip():
                comp.website = extra["website"][:255]
                changed = True
            # Руководитель (contact_name) — заполняем из amo, если пусто
            if extra.get("director") and not (comp.contact_name or "").strip():
                comp.contact_name = extra["director"][:255]
                changed = True
            if changed and not dry_run:
                comp.save()

            # Нормализация уже заполненных значений (часто там "номер1, номер2"):
            # оставляем в "Данные" только первый, остальные переносим в служебный контакт.
            norm_phone_parts = _split_multi(comp.phone or "")
            norm_email_parts = _split_multi(comp.email or "")
            if len(norm_phone_parts) > 1 and not dry_run:
                comp.phone = norm_phone_parts[0][:50]
                comp.save(update_fields=["phone"])
                # добавим остальные как контактные телефоны
                phones = list(dict.fromkeys([*phones, *norm_phone_parts]))
            if len(norm_email_parts) > 1 and not dry_run:
                comp.email = norm_email_parts[0][:254]
                comp.save(update_fields=["email"])
                emails = list(dict.fromkeys([*emails, *norm_email_parts]))

            # Остальные телефоны/почты — в "Контакты" отдельной записью (stub)
            extra_phones = [p for p in phones[1:] if str(p).strip()]
            extra_emails = [e for e in emails[1:] if str(e).strip()]
            if (extra_phones or extra_emails) and not dry_run:
                # sentinel: amocrm_contact_id = -amocrm_company_id, чтобы не плодить дубли на повторных запусках
                sentinel = -int(comp.amocrm_company_id or 0) if comp.amocrm_company_id else 0
                c = None
                if sentinel:
                    c = Contact.objects.filter(company=comp, amocrm_contact_id=sentinel).first()
                if c is None:
                    c = Contact(company=comp, amocrm_contact_id=sentinel or None, raw_fields={"source": "amo_api_company_channels"})
                    c.save()
                for p in extra_phones:
                    v = str(p).strip()[:50]
                    if not v:
                        continue
                    if not ContactPhone.objects.filter(contact=c, value=v).exists():
                        ContactPhone.objects.create(contact=c, type=ContactPhone.PhoneType.OTHER, value=v)
                for e in extra_emails:
                    v = str(e).strip()[:254]
                    if not v:
                        continue
                    if not ContactEmail.objects.filter(contact=c, value__iexact=v).exists():
                        try:
                            ContactEmail.objects.create(contact=c, type=ContactEmail.EmailType.OTHER, value=v)
                        except Exception:
                            pass
            if created:
                res.companies_created += 1
            else:
                res.companies_updated += 1
            # Сферы: исключаем "Новая CRM" (она только для фильтра), но ставим остальные
            _apply_spheres_from_custom(amo_company=amo_c, company=comp, field_id=sphere_field_id, dry_run=dry_run, exclude_label="Новая CRM")
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
                # важно: не используем "or", потому что 0/"" могут скрыть реальные значения
                ts = t.get("complete_till", None)
                if ts in (None, "", 0, "0"):
                    ts = t.get("complete_till_at", None)
                if ts in (None, "", 0, "0"):
                    ts = t.get("due_at", None)
                due_at = _parse_amo_due(ts)
                if res.tasks_preview is not None and len(res.tasks_preview) < 5:
                    res.tasks_preview.append(
                        {
                            "id": tid,
                            "raw_ts": ts,
                            "parsed_due": str(due_at) if due_at else "",
                            "keys": sorted(list(t.keys()))[:30],
                        }
                    )
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
                    if due_at and (existing.due_at is None or existing.due_at != due_at):
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
                    # amomail_message — это по сути история почты; делаем читабельным, без JSON
                    if note_type.lower().startswith("amomail"):
                        incoming = bool(params.get("income")) if isinstance(params, dict) else False
                        subj = str(params.get("subject") or "").strip()
                        frm = params.get("from") or {}
                        to = params.get("to") or {}
                        frm_s = ""
                        to_s = ""
                        try:
                            frm_s = f"{(frm.get('name') or '').strip()} <{(frm.get('email') or '').strip()}>".strip()
                        except Exception:
                            frm_s = ""
                        try:
                            to_s = f"{(to.get('name') or '').strip()} <{(to.get('email') or '').strip()}>".strip()
                        except Exception:
                            to_s = ""
                        summ = str(params.get("content_summary") or "").strip()
                        attach_cnt = params.get("attach_cnt")
                        lines = []
                        lines.append("Письмо (amoMail) · " + ("Входящее" if incoming else "Исходящее"))
                        if subj:
                            lines.append("Тема: " + subj)
                        if frm_s:
                            lines.append("От: " + frm_s)
                        if to_s:
                            lines.append("Кому: " + to_s)
                        if summ:
                            lines.append("Кратко: " + summ)
                        if attach_cnt not in (None, "", 0, "0"):
                            lines.append("Вложений: " + str(attach_cnt))
                        # для такого типа не подставляем автора как "вы"
                        author = None
                        text = "\n".join(lines) if lines else "Письмо (amoMail)"
                        prefix = "Импорт из amo"
                    elif note_type.lower() in ("call_out", "call_in", "call"):
                        # звонки — тоже форматируем, иначе будет JSON-каша
                        text = _format_call_note(note_type, params)
                        author = None
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
                    if res.notes_preview is not None and len(res.notes_preview) < 5:
                        res.notes_preview.append(
                            {
                                "id": nid,
                                "type": note_type,
                                "text_head": (text_full[:140] + ("…" if len(text_full) > 140 else "")),
                            }
                        )

                    if existing_note:
                        # если раньше создали "пустышку" — обновим
                        upd = False
                        if existing_note.company_id != company.id:
                            existing_note.company = company
                            upd = True
                        old_text = (existing_note.text or "").strip()
                        # Переписываем также любые почтовые записи, которые раньше импортировали как JSON-простыню.
                        should_rewrite = (
                            old_text.startswith("Импорт из amo (note id")
                            or len(old_text) < 40
                            or ("type: amomail" in old_text.lower())
                            or ("\"thread_id\"" in old_text)
                            or note_type.lower().startswith("amomail")
                            or ("\"uniq\"" in old_text)
                            or note_type.lower().startswith("call_")
                        )
                        if should_rewrite:
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

        # Импорт контактов компаний из amoCRM (опционально, т.к. может быть медленно)
        # Важно: импортируем контакты ТОЛЬКО для компаний из текущей пачки (amo_ids)
        print(f"[AMOCRM DEBUG] Contact import check: import_contacts={import_contacts}, amo_ids={bool(amo_ids)}, len={len(amo_ids) if amo_ids else 0}")
        if import_contacts and amo_ids:
            res.contacts_seen = 0
            res.contacts_created = 0
            res._debug_contacts_logged = 0  # счетчик для отладки
            print(f"[AMOCRM DEBUG] ===== STARTING CONTACT IMPORT for {len(amo_ids)} companies =====")
            try:
                # Создаём set для быстрой проверки: контакты должны быть связаны только с компаниями из текущей пачки
                amo_ids_set = set(amo_ids)
                amo_contacts = fetch_contacts_for_companies(client, amo_ids)
                res.contacts_seen = len(amo_contacts)
                print(f"[AMOCRM DEBUG] Fetched {res.contacts_seen} contacts from amoCRM API")
                for ac in amo_contacts:
                    amo_contact_id = int(ac.get("id") or 0)
                    if not amo_contact_id:
                        continue
                    # Связь контакта с компанией: в amo это через links или company_id в самом контакте
                    company_ids_in_contact = []
                    # Вариант 1: company_id в самом контакте
                    cid = int(ac.get("company_id") or 0)
                    if cid and cid in amo_ids_set:  # проверяем, что компания из текущей пачки
                        company_ids_in_contact.append(cid)
                    # Вариант 2: через _embedded/companies
                    embedded = ac.get("_embedded") or {}
                    companies = embedded.get("companies") or []
                    if isinstance(companies, list):
                        for c in companies:
                            cid2 = int(c.get("id") or 0)
                            if cid2 and cid2 in amo_ids_set:  # проверяем, что компания из текущей пачки
                                company_ids_in_contact.append(cid2)
                    if not company_ids_in_contact:
                        continue  # пропускаем контакты, не связанные с компаниями из текущей пачки
                    # Находим локальную компанию (должна быть из текущей пачки)
                    local_company = None
                    for cid3 in company_ids_in_contact:
                        local_company = Company.objects.filter(amocrm_company_id=cid3).first()
                        if local_company:
                            break
                    if not local_company:
                        continue  # пропускаем, если локальная компания не найдена (не должна случиться, но на всякий случай)
                    # Проверяем, не импортировали ли уже этот контакт
                    existing_contact = Contact.objects.filter(amocrm_contact_id=amo_contact_id, company=local_company).first()
                    if existing_contact:
                        continue
                    # Извлекаем данные контакта
                    first_name = str(ac.get("first_name") or "").strip()
                    last_name = str(ac.get("last_name") or "").strip()
                    
                    # В amoCRM телефоны и email могут быть:
                    # 1. В стандартных полях (phone, email) - если они есть
                    # 2. В custom_fields_values с code="PHONE"/"EMAIL" или по field_id
                    # 3. В custom_fields_values по названию поля
                    phones = []
                    emails = []
                    position = ""
                    
                    # Стандартные поля (если есть)
                    if ac.get("phone"):
                        phones.extend(_split_multi(str(ac.get("phone"))))
                    if ac.get("email"):
                        emails.append(str(ac.get("email")).strip())
                    
                    # custom_fields_values для телефонов/почт/должности
                    custom_fields = ac.get("custom_fields_values") or []
                    for cf in custom_fields:
                        if not isinstance(cf, dict):
                            continue
                        field_id = int(cf.get("field_id") or 0)
                        field_code = str(cf.get("code") or "").upper()  # PHONE, EMAIL в верхнем регистре
                        field_name = str(cf.get("name") or "").lower()
                        field_type = str(cf.get("type") or "").lower()
                        values = cf.get("values") or []
                        if not isinstance(values, list):
                            continue
                        for v in values:
                            # Значение может быть как dict (с ключом "value"), так и строкой
                            if isinstance(v, dict):
                                val = str(v.get("value") or "").strip()
                            elif isinstance(v, str):
                                val = v.strip()
                            else:
                                val = str(v).strip() if v else ""
                            if not val:
                                continue
                            # Телефоны: проверяем code="PHONE", type="phone", или название содержит "телефон"
                            if (field_code == "PHONE" or field_type == "phone" or 
                                field_code.lower() in ("phone", "phone_number", "mobile", "work_phone") or 
                                "телефон" in field_name):
                                phones.extend(_split_multi(val))
                            # Email: проверяем code="EMAIL", type="email", или название содержит "email"/"почта"
                            elif (field_code == "EMAIL" or field_type == "email" or
                                  field_code.lower() in ("email", "email_address") or 
                                  "email" in field_name or "почта" in field_name):
                                emails.append(val)
                            # Должность: проверяем code="POSITION" или название содержит "должность"/"позиция"
                            elif (field_code == "POSITION" or
                                  field_code.lower() in ("position", "job_title") or 
                                  "должность" in field_name or "позиция" in field_name):
                                if not position:
                                    position = val
                    
                    # Убираем дубликаты
                    phones = list(dict.fromkeys(phones))  # сохраняет порядок
                    emails = list(dict.fromkeys(emails))
                    
                    # ОТЛАДКА: сохраняем сырые данные для анализа
                    debug_data = {
                        "source": "amo_api",
                        "amo_contact_id": amo_contact_id,
                        "first_name": first_name,
                        "last_name": last_name,
                        "extracted_phones": phones,
                        "extracted_emails": emails,
                        "extracted_position": position,
                        "custom_fields_count": len(custom_fields),
                        "custom_fields_sample": custom_fields[:3] if custom_fields else [],  # первые 3 для отладки
                        "has_phone_field": bool(ac.get("phone")),
                        "has_email_field": bool(ac.get("email")),
                    }
                    
                    # Сохраняем отладочную информацию для dry-run (первые 5 контактов)
                    debug_count = getattr(res, '_debug_contacts_logged', 0)
                    if res.contacts_preview is not None and debug_count < 5:
                        # Собираем информацию о custom_fields для отладки
                        custom_fields_debug = []
                        for cf_idx, cf in enumerate(custom_fields[:3]):  # первые 3 поля
                            if isinstance(cf, dict):
                                first_val = ""
                                if cf.get("values") and len(cf.get("values", [])) > 0:
                                    v = cf.get("values")[0]
                                    if isinstance(v, dict):
                                        first_val = str(v.get("value", ""))[:100]
                                    else:
                                        first_val = str(v)[:100]
                                custom_fields_debug.append({
                                    "index": cf_idx,
                                    "field_id": cf.get("field_id"),
                                    "code": cf.get("code"),
                                    "name": cf.get("name"),
                                    "type": cf.get("type"),
                                    "values_count": len(cf.get("values") or []),
                                    "first_value": first_val,
                                })
                        
                        contact_debug = {
                            "amo_contact_id": amo_contact_id,
                            "first_name": first_name,
                            "last_name": last_name,
                            "phones_found": phones,
                            "emails_found": emails,
                            "position_found": position,
                            "custom_fields_count": len(custom_fields),
                            "custom_fields_sample": custom_fields_debug,
                            "raw_contact_keys": list(ac.keys())[:15],
                            "has_phone_field": bool(ac.get("phone")),
                            "has_email_field": bool(ac.get("email")),
                        }
                        res.contacts_preview.append(contact_debug)
                        res._debug_contacts_logged = debug_count + 1
                        
                        # Также логируем в консоль
                        print(f"[AMOCRM DEBUG] Contact {amo_contact_id}:")
                        print(f"  - first_name: {first_name}")
                        print(f"  - last_name: {last_name}")
                        print(f"  - phones found: {phones}")
                        print(f"  - emails found: {emails}")
                        print(f"  - position found: {position}")
                        print(f"  - custom_fields_values count: {len(custom_fields)}")
                        if custom_fields:
                            print(f"  - custom_fields sample (first): {custom_fields[0]}")
                        print(f"  - raw contact top-level keys: {list(ac.keys())[:15]}")
                    
                    # Создаём контакт
                    contact = Contact(
                        company=local_company,
                        first_name=first_name[:120],
                        last_name=last_name[:120],
                        position=position[:255],
                        amocrm_contact_id=amo_contact_id,
                        raw_fields=debug_data,
                    )
                    if not dry_run:
                        contact.save()
                        res.contacts_created += 1
                        # Добавляем телефоны и почты
                        phones_added = 0
                        for p in phones:
                            pv = str(p).strip()[:50]
                            if pv and not ContactPhone.objects.filter(contact=contact, value=pv).exists():
                                ContactPhone.objects.create(contact=contact, type=ContactPhone.PhoneType.WORK, value=pv)
                                phones_added += 1
                        emails_added = 0
                        for e in emails:
                            ev = str(e).strip()[:254]
                            if ev and not ContactEmail.objects.filter(contact=contact, value__iexact=ev).exists():
                                try:
                                    ContactEmail.objects.create(contact=contact, type=ContactEmail.EmailType.WORK, value=ev)
                                    emails_added += 1
                                except Exception:
                                    pass
                        # Логируем результат сохранения
                        if debug_count < 5:
                            print(f"  - Saved: phones={phones_added}, emails={emails_added}, position={bool(position)}")
                    else:
                        res.contacts_created += 1
            except Exception as e:
                # Если контакты недоступны — не валим всю миграцию
                print(f"[AMOCRM DEBUG] ERROR importing contacts: {type(e).__name__}: {e}")
                import traceback
                print(f"[AMOCRM DEBUG] Traceback:\n{traceback.format_exc()}")
                pass
            finally:
                print(f"[AMOCRM DEBUG] ===== CONTACT IMPORT FINISHED: created={res.contacts_created}, seen={res.contacts_seen} =====")
        else:
            print(f"[AMOCRM DEBUG] Contact import SKIPPED: import_contacts={import_contacts}, amo_ids={bool(amo_ids)}")

        if dry_run:
            transaction.set_rollback(True)

    _run()
    return res


