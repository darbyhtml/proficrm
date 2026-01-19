from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import json
import logging
import re
import time

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime, parse_date
from datetime import datetime, time as dt_time, timezone as dt_timezone

from accounts.models import User
from companies.models import Company, CompanyNote, CompanySphere, Contact, ContactEmail, ContactPhone
from tasksapp.models import Task

from .client import AmoClient, AmoApiError

logger = logging.getLogger(__name__)


def _norm(s: str) -> str:
    return (s or "").strip().lower()


def _parse_fio(name_str: str, first_name_str: str = "", last_name_str: str = "") -> tuple[str, str]:
    """
    Парсит ФИО из строк amoCRM в (last_name, first_name).
    
    Логика:
    - Если есть и first_name и last_name - используем их как есть
    - Если есть только name - парсим "Фамилия Имя Отчество" -> (Фамилия, Имя Отчество)
    - Если есть name и first_name - парсим name как полное ФИО
    - Если есть name и last_name - парсим name как полное ФИО
    
    Args:
        name_str: Полное имя из поля "name"
        first_name_str: Имя из поля "first_name"
        last_name_str: Фамилия из поля "last_name"
    
    Returns:
        tuple[str, str]: (last_name, first_name)
    """
    first_name = (first_name_str or "").strip()
    last_name = (last_name_str or "").strip()
    name = (name_str or "").strip()
    
    # Если есть и first_name и last_name - используем их
    if first_name and last_name:
        return (last_name[:120], first_name[:120])
    
    # Если есть только name - парсим его
    if name and not first_name and not last_name:
        parts = [p for p in name.split() if p.strip()]
        if len(parts) >= 2:
            # "Фамилия Имя Отчество" -> last_name="Фамилия", first_name="Имя Отчество"
            return (parts[0][:120], " ".join(parts[1:])[:120])
        elif len(parts) == 1:
            # Только одно слово - считаем именем
            return ("", parts[0][:120])
    
    # Если есть name и first_name - парсим name как полное ФИО
    if name and first_name and not last_name:
        parts = [p for p in name.split() if p.strip()]
        if len(parts) >= 2:
            # Если name содержит больше слов, чем first_name - парсим name
            return (parts[0][:120], " ".join(parts[1:])[:120])
        else:
            # Иначе используем first_name
            return ("", first_name[:120])
    
    # Если есть name и last_name - парсим name как полное ФИО
    if name and last_name and not first_name:
        parts = [p for p in name.split() if p.strip()]
        if len(parts) >= 2:
            # Если name содержит больше слов, чем last_name - парсим name
            return (parts[0][:120], " ".join(parts[1:])[:120])
        else:
            # Иначе используем last_name
            return (last_name[:120], "")
    
    # Если есть только first_name
    if first_name and not last_name:
        return ("", first_name[:120])
    
    # Если есть только last_name
    if last_name and not first_name:
        return (last_name[:120], "")
    
    # Если ничего нет
    return ("", "")


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


def _analyze_contact_completely(contact: dict[str, Any]) -> dict[str, Any]:
    """
    Полный анализ контакта из AmoCRM API.
    Извлекает ВСЕ возможные поля согласно документации:
    https://www.amocrm.ru/developers/content/crm_platform/api-reference
    
    Возвращает структурированный словарь со всеми найденными данными.
    """
    if not isinstance(contact, dict):
        return {"error": "Contact is not a dict", "raw": str(contact)[:500]}
    
    result = {
        "standard_fields": {},
        "custom_fields": [],
        "embedded_data": {},
        "all_keys": [],
        "extracted_data": {},
    }
    
    # 1. СТАНДАРТНЫЕ ПОЛЯ КОНТАКТА (согласно документации AmoCRM API v4)
    standard_field_names = [
        "id", "name", "first_name", "last_name",
        "responsible_user_id", "group_id", "created_by", "updated_by",
        "created_at", "updated_at", "is_deleted",
        "phone", "email", "company_id",
    ]
    
    for field_name in standard_field_names:
        if field_name in contact:
            value = contact.get(field_name)
            result["standard_fields"][field_name] = value
    
    # Сохраняем все ключи контакта для анализа
    result["all_keys"] = list(contact.keys())
    
    # 2. CUSTOM_FIELDS_VALUES - все кастомные поля
    custom_fields = contact.get("custom_fields_values") or []
    if isinstance(custom_fields, list):
        for cf_idx, cf in enumerate(custom_fields):
            if not isinstance(cf, dict):
                continue
            
            field_info = {
                "index": cf_idx,
                "field_id": cf.get("field_id"),
                "field_name": cf.get("field_name"),
                "field_code": cf.get("field_code"),
                "field_type": cf.get("field_type"),
                "values": [],
                "values_count": 0,
            }
            
            # Извлекаем все значения поля
            values_list = cf.get("values") or []
            if isinstance(values_list, list):
                field_info["values_count"] = len(values_list)
                for v_idx, v in enumerate(values_list):
                    value_info = {
                        "index": v_idx,
                        "raw": v,
                    }
                    
                    if isinstance(v, dict):
                        # Стандартная структура значения
                        value_info["value"] = v.get("value")
                        value_info["enum_id"] = v.get("enum_id")
                        value_info["enum_code"] = v.get("enum_code")
                        value_info["enum"] = v.get("enum")
                        
                        # Для файлов - дополнительная информация
                        if isinstance(v.get("value"), dict) and "file_uuid" in v.get("value", {}):
                            file_info = v.get("value", {})
                            value_info["file_info"] = {
                                "file_uuid": file_info.get("file_uuid"),
                                "file_name": file_info.get("file_name"),
                                "file_size": file_info.get("file_size"),
                            }
                    else:
                        # Простое значение (строка, число и т.д.)
                        value_info["value"] = v
                    
                    field_info["values"].append(value_info)
            
            result["custom_fields"].append(field_info)
    
    # 3. _EMBEDDED - вложенные связи
    embedded = contact.get("_embedded") or {}
    if isinstance(embedded, dict):
        # Tags (теги)
        if "tags" in embedded:
            tags_list = embedded.get("tags") or []
            if isinstance(tags_list, list):
                result["embedded_data"]["tags"] = [
                    {
                        "id": tag.get("id") if isinstance(tag, dict) else None,
                        "name": tag.get("name") if isinstance(tag, dict) else str(tag),
                    }
                    for tag in tags_list
                ]
        
        # Companies (компании)
        if "companies" in embedded:
            companies_list = embedded.get("companies") or []
            if isinstance(companies_list, list):
                result["embedded_data"]["companies"] = [
                    {
                        "id": comp.get("id") if isinstance(comp, dict) else None,
                        "name": comp.get("name") if isinstance(comp, dict) else str(comp),
                    }
                    for comp in companies_list
                ]
        
        # Leads (сделки)
        if "leads" in embedded:
            leads_list = embedded.get("leads") or []
            if isinstance(leads_list, list):
                result["embedded_data"]["leads"] = [
                    {
                        "id": lead.get("id") if isinstance(lead, dict) else None,
                        "name": lead.get("name") if isinstance(lead, dict) else str(lead),
                    }
                    for lead in leads_list
                ]
        
        # Customers (покупатели)
        if "customers" in embedded:
            customers_list = embedded.get("customers") or []
            if isinstance(customers_list, list):
                result["embedded_data"]["customers"] = [
                    {
                        "id": cust.get("id") if isinstance(cust, dict) else None,
                        "name": cust.get("name") if isinstance(cust, dict) else str(cust),
                    }
                    for cust in customers_list
                ]
        
        # Catalog elements (элементы каталога)
        if "catalog_elements" in embedded:
            catalog_elements_list = embedded.get("catalog_elements") or []
            if isinstance(catalog_elements_list, list):
                result["embedded_data"]["catalog_elements"] = [
                    {
                        "id": elem.get("id") if isinstance(elem, dict) else None,
                        "name": elem.get("name") if isinstance(elem, dict) else str(elem),
                    }
                    for elem in catalog_elements_list
                ]
        
        # Notes (заметки)
        if "notes" in embedded:
            notes_list = embedded.get("notes") or []
            if isinstance(notes_list, list):
                result["embedded_data"]["notes"] = [
                    {
                        "id": note.get("id") if isinstance(note, dict) else None,
                        "note_type": note.get("note_type") if isinstance(note, dict) else None,
                        "text": note.get("text") if isinstance(note, dict) else None,
                        "params": note.get("params") if isinstance(note, dict) else None,
                    }
                    for note in notes_list
                ]
    
    # 4. ИЗВЛЕЧЕННЫЕ ДАННЫЕ (телефоны, email, должность, примечания)
    # Это данные, которые мы используем для импорта
    extracted = {
        "phones": [],
        "emails": [],
        "position": None,
        "note_text": None,
        "cold_call_timestamp": None,
    }
    
    # Телефоны из стандартного поля
    if contact.get("phone"):
        phone_str = str(contact.get("phone"))
        for pv in _split_multi(phone_str):
            if pv:
                extracted["phones"].append({
                    "value": pv,
                    "type": "OTHER",
                    "source": "standard_field",
                })
    
    # Email из стандартного поля
    if contact.get("email"):
        email_str = str(contact.get("email")).strip()
        if email_str:
            extracted["emails"].append({
                "value": email_str,
                "type": "OTHER",
                "source": "standard_field",
            })
    
    # Извлекаем данные из custom_fields
    for cf in result["custom_fields"]:
        field_code = str(cf.get("field_code") or "").upper()
        field_name = str(cf.get("field_name") or "").lower()
        field_type = str(cf.get("field_type") or "").lower()
        
        # Телефоны
        is_phone = (field_code == "PHONE" or "телефон" in field_name)
        if is_phone:
            for val_info in cf.get("values", []):
                val = val_info.get("value")
                if val:
                    enum_code = val_info.get("enum_code") or val_info.get("enum") or ""
                    extracted["phones"].append({
                        "value": str(val),
                        "type": str(enum_code).upper() if enum_code else "OTHER",
                        "source": f"custom_field_id={cf.get('field_id')}",
                        "field_name": cf.get("field_name"),
                    })
        
        # Email
        is_email = (field_code == "EMAIL" or "email" in field_name or "почта" in field_name)
        if is_email:
            for val_info in cf.get("values", []):
                val = val_info.get("value")
                if val and "@" in str(val):
                    enum_code = val_info.get("enum_code") or val_info.get("enum") or ""
                    extracted["emails"].append({
                        "value": str(val),
                        "type": str(enum_code).upper() if enum_code else "OTHER",
                        "source": f"custom_field_id={cf.get('field_id')}",
                        "field_name": cf.get("field_name"),
                    })
        
        # Должность
        is_position = (field_code == "POSITION" or "должность" in field_name or "позиция" in field_name)
        if is_position and not extracted["position"]:
            first_val = cf.get("values", [{}])[0].get("value") if cf.get("values") else None
            if first_val:
                extracted["position"] = {
                    "value": str(first_val),
                    "source": f"custom_field_id={cf.get('field_id')}",
                    "field_name": cf.get("field_name"),
                }
        
        # Примечание
        is_note = (
            any(k in field_name for k in ["примеч", "комментар", "коммент", "заметк"]) or
            any(k in field_code for k in ["NOTE", "COMMENT", "REMARK"])
        )
        if is_note and not extracted["note_text"]:
            first_val = cf.get("values", [{}])[0].get("value") if cf.get("values") else None
            if first_val:
                extracted["note_text"] = {
                    "value": str(first_val),
                    "source": f"custom_field_id={cf.get('field_id')}",
                    "field_name": cf.get("field_name"),
                }
        
        # Холодный звонок
        is_cold_call = (field_type == "date" and "холодный" in field_name and "звонок" in field_name)
        if is_cold_call and not extracted["cold_call_timestamp"]:
            first_val = cf.get("values", [{}])[0].get("value") if cf.get("values") else None
            if first_val:
                try:
                    extracted["cold_call_timestamp"] = {
                        "value": int(float(first_val)),
                        "source": f"custom_field_id={cf.get('field_id')}",
                        "field_name": cf.get("field_name"),
                    }
                except (ValueError, TypeError):
                    pass
    
    # Примечания из _embedded.notes
    if not extracted["note_text"] and "notes" in result["embedded_data"]:
        for note in result["embedded_data"]["notes"]:
            note_type = str(note.get("note_type") or "").lower()
            note_text_val = note.get("text") or ""
            
            # Берем заметки типа "common", "text" (не служебные)
            if note_type in ["common", "text", "common_message"] and note_text_val:
                extracted["note_text"] = {
                    "value": str(note_text_val),
                    "source": f"_embedded.notes (note_type={note_type})",
                }
                break
    
    result["extracted_data"] = extracted
    
    return result


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
        return (vals[0] if vals else "")[:500]  # обрезаем до разумного максимума (для дальнейшей обрезки по полям)

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
    # Юридическое название: пытаемся найти по разным вариантам названия поля
    fid_legal = _find_field_id(
        field_meta,
        name_contains=[
            "юрид",
            "юр.",
            "юр ",
            "полное наимен",
            "полное название",
            "Полное название",
            "наименование юр",
            "название юр",
            "юрлицо",
        ],
    )
    fid_addr = _find_field_id(field_meta, codes=["address"], name_contains=["адрес"])
    fid_phone = _find_field_id(field_meta, codes=["phone"], name_contains=["телефон"])
    fid_email = _find_field_id(field_meta, codes=["email"], name_contains=["email", "e-mail", "почта"])
    fid_web = _find_field_id(field_meta, codes=["web"], name_contains=["сайт", "web"])
    fid_director = _find_field_id(field_meta, name_contains=["руководитель", "директор", "генеральный"])
    fid_activity = _find_field_id(field_meta, name_contains=["вид деятельности", "вид деят", "деятельност"])
    fid_employees = _find_field_id(field_meta, name_contains=["численность", "сотрудник", "штат"])
    fid_worktime = _find_field_id(field_meta, name_contains=["рабочее время", "часы работы", "режим работы", "работа с"])
    fid_tz = _find_field_id(field_meta, name_contains=["часовой пояс", "таймзона", "timezone"])
    fid_note = _find_field_id(field_meta, name_contains=["примеч", "комментар", "коммент", "заметк"])

    return {
        "inn": first(fid_inn),
        "kpp": first(fid_kpp),
        "legal_name": first(fid_legal),
        "address": first(fid_addr),
        "phones": list_vals(fid_phone),
        "emails": list_vals(fid_email),
        "website": first(fid_web),
        "director": first(fid_director),
        "activity_kind": first(fid_activity),
        "employees_count": first(fid_employees),
        "worktime": first(fid_worktime),
        "work_timezone": first(fid_tz),
        "note": first(fid_note),
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
    tasks_skipped_old: int = 0
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

    companies_updates_preview: list[dict] | None = None  # diff изменений компаний при dry-run
    contacts_updates_preview: list[dict] | None = None  # diff изменений контактов при dry-run

    preview: list[dict] | None = None
    
    error: str | None = None  # ошибка миграции (если была)
    error_traceback: str | None = None  # полный traceback ошибки


def fetch_amo_users(client: AmoClient) -> list[dict[str, Any]]:
    """
    Получает список пользователей из AmoCRM.
    Если long-lived token не имеет прав на /api/v4/users (403), возвращает пустой список.
    Rate limiting применяется автоматически в AmoClient.
    """
    try:
        return client.get_all_pages("/api/v4/users", embedded_key="users", limit=50, max_pages=20)
    except AmoApiError as e:
        # Если 403 Forbidden - long-lived token не имеет прав на доступ к пользователям
        if "403" in str(e) or "Forbidden" in str(e):
            logger.warning(
                "Long-lived token не имеет прав на доступ к /api/v4/users. "
                "Для доступа к списку пользователей используйте OAuth токен. "
                "Продолжаем без списка пользователей."
            )
            return []
        # Для других ошибок пробрасываем исключение
        raise


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


def fetch_companies_by_responsible(client: AmoClient, responsible_user_id: int, *, limit_pages: int = 100, with_contacts: bool = False) -> list[dict[str, Any]]:
    """
    Получает компании по ответственному пользователю.
    Rate limiting применяется автоматически в AmoClient.
    ВСЕГДА запрашиваем БЕЗ контактов (with_contacts=False) - контакты получаем отдельно.
    """
    params = {f"filter[responsible_user_id]": responsible_user_id, "with": "custom_fields"}
    # НЕ запрашиваем contacts здесь - это создает огромные ответы и вызывает 504
    # Контакты получаем отдельно через filter[company_id][]
    return client.get_all_pages(
        "/api/v4/companies",
        params=params,
        embedded_key="companies",
        limit=25,  # Оптимальный размер: не слишком большой (504), не слишком маленький
        max_pages=limit_pages,
    )


def fetch_tasks_for_companies(client: AmoClient, company_ids: list[int]) -> list[dict[str, Any]]:
    """
    Получает задачи для компаний.
    Rate limiting применяется автоматически в AmoClient.
    Используем батчи по 10 компаний для оптимального баланса.
    """
    if not company_ids:
        return []
    out: list[dict[str, Any]] = []
    batch_size = 10  # Оптимальный размер батча
    for i in range(0, len(company_ids), batch_size):
        ids = company_ids[i : i + batch_size]
        out.extend(
            client.get_all_pages(
                "/api/v4/tasks",
                params={f"filter[entity_type]": "companies", f"filter[entity_id][]": ids},
                embedded_key="tasks",
                limit=50,
                max_pages=20,
            )
        )
    return out


def fetch_notes_for_companies(client: AmoClient, company_ids: list[int]) -> list[dict[str, Any]]:
    """
    Получает заметки для компаний.
    Rate limiting применяется автоматически в AmoClient.
    API не поддерживает батчинг для заметок, поэтому обрабатываем по одной компании.
    """
    if not company_ids:
        return []
    out: list[dict[str, Any]] = []
    for cid in company_ids:
        try:
            notes = client.get_all_pages(
                f"/api/v4/companies/{int(cid)}/notes",
                params={},
                embedded_key="notes",
                limit=50,
                max_pages=10,
            )
            out.extend(notes)
        except Exception as e:
            logger.debug(f"Error fetching notes for company {cid}: {e}", exc_info=True)
            # Продолжаем для следующих компаний
            continue
    return out


def fetch_contacts_for_companies(client: AmoClient, company_ids: list[int]) -> list[dict[str, Any]]:
    """
    Получает контакты компаний из amoCRM.
    Согласно документации AmoCRM API v4:
    1. Можно использовать filter[company_id]=ID для одного ID (не массив!)
    2. Или запрашивать компании с with=contacts и извлекать _embedded.contacts
    
    Используем оба способа для надежности.
    Rate limiting применяется автоматически в AmoClient.
    """
    if not company_ids:
        logger.info("fetch_contacts_for_companies: company_ids пуст, возвращаем []")
        return []
    out: list[dict[str, Any]] = []
    
    logger.info(f"fetch_contacts_for_companies: начинаем поиск контактов для {len(company_ids)} компаний: {company_ids[:5]}...")
    
    # Способ 1: Запрашиваем каждую компанию с with=contacts
    # Это самый надежный способ согласно документации
    method1_contacts_count = 0
    for company_id in company_ids:
        try:
            # Получаем компанию с контактами
            logger.info(f"fetch_contacts_for_companies: запрашиваем компанию {company_id} с with=contacts")
            company_data = client.get(
                f"/api/v4/companies/{company_id}",
                params={"with": "custom_fields,contacts"}  # Только custom_fields и contacts, БЕЗ notes
            )
            
            if isinstance(company_data, dict):
                embedded = company_data.get("_embedded") or {}
                contacts = embedded.get("contacts") or []
                if isinstance(contacts, list) and contacts:
                    logger.info(f"fetch_contacts_for_companies: компания {company_id}: найдено {len(contacts)} контактов через with=contacts")
                    # Добавляем company_id к каждому контакту для удобства
                    for contact in contacts:
                        if isinstance(contact, dict):
                            # Сохраняем связь с компанией
                            if "_embedded" not in contact:
                                contact["_embedded"] = {}
                            if "companies" not in contact["_embedded"]:
                                contact["_embedded"]["companies"] = [{"id": company_id}]
                    out.extend(contacts)
                    method1_contacts_count += len(contacts)
                else:
                    logger.info(f"fetch_contacts_for_companies: компания {company_id}: контакты не найдены в _embedded.contacts (пустой список или отсутствует)")
            else:
                logger.warning(f"fetch_contacts_for_companies: компания {company_id}: неожиданный тип ответа: {type(company_data)}")
        except Exception as e:
            logger.warning(f"fetch_contacts_for_companies: ошибка при получении компании {company_id} с контактами: {e}", exc_info=True)
            # Продолжаем для следующих компаний
            continue
    
    logger.info(f"fetch_contacts_for_companies: способ 1 (with=contacts): найдено {method1_contacts_count} контактов из {len(company_ids)} компаний")
    
    # Если через with=contacts ничего не нашли, пробуем способ 2: filter[company_id] для каждого ID
    if not out:
        logger.info("fetch_contacts_for_companies: через with=contacts контакты не найдены, пробуем filter[company_id] для каждой компании...")
        method2_contacts_count = 0
        for company_id in company_ids:
            try:
                # Согласно документации: filter[company_id]=ID (без [])
                logger.info(f"fetch_contacts_for_companies: запрашиваем контакты через filter[company_id]={company_id}")
                contacts_data = client.get(
                    "/api/v4/contacts",
                    params={
                        "filter[company_id]": company_id,  # БЕЗ [] - для одного ID
                        "with": "custom_fields",
                    }
                )
                
                if isinstance(contacts_data, dict):
                    embedded = contacts_data.get("_embedded") or {}
                    contacts = embedded.get("contacts") or []
                    if isinstance(contacts, list) and contacts:
                        logger.info(f"fetch_contacts_for_companies: компания {company_id}: найдено {len(contacts)} контактов через filter[company_id]")
                        # Добавляем company_id к каждому контакту
                        for contact in contacts:
                            if isinstance(contact, dict):
                                if "_embedded" not in contact:
                                    contact["_embedded"] = {}
                                if "companies" not in contact["_embedded"]:
                                    contact["_embedded"]["companies"] = [{"id": company_id}]
                        out.extend(contacts)
                        method2_contacts_count += len(contacts)
                    else:
                        logger.info(f"fetch_contacts_for_companies: компания {company_id}: контакты не найдены через filter[company_id] (пустой список)")
                else:
                    logger.warning(f"fetch_contacts_for_companies: компания {company_id}: неожиданный тип ответа через filter[company_id]: {type(contacts_data)}")
            except Exception as e:
                logger.warning(f"fetch_contacts_for_companies: ошибка при получении контактов через filter[company_id]={company_id}: {e}", exc_info=True)
                continue
        
        logger.info(f"fetch_contacts_for_companies: способ 2 (filter[company_id]): найдено {method2_contacts_count} контактов из {len(company_ids)} компаний")
    
    logger.info(f"fetch_contacts_for_companies: ИТОГО найдено {len(out)} контактов из {len(company_ids)} компаний")
    return out


def fetch_notes_for_contacts(client: AmoClient, contact_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    """
    Получает заметки контактов из amoCRM.
    Rate limiting применяется автоматически в AmoClient.
    API не поддерживает батчинг для заметок, поэтому обрабатываем по одному контакту.
    Возвращает словарь {contact_id: [notes]}.
    """
    if not contact_ids:
        return {}
    out: dict[int, list[dict[str, Any]]] = {}
    # В amoCRM заметки контактов берутся из /api/v4/contacts/{id}/notes
    # Обрабатываем контакты по одному (API не поддерживает батчинг для заметок)
    for cid in contact_ids:
        try:
            notes = client.get_all_pages(
                f"/api/v4/contacts/{int(cid)}/notes",
                params={},
                embedded_key="notes",
                limit=50,
                max_pages=10,
            )
            if notes:
                out[cid] = notes
        except Exception as e:
            logger.debug(f"Error fetching notes for contact {cid}: {e}", exc_info=True)
            continue
    return out


def _upsert_company_from_amo(
    *,
    amo_company: dict[str, Any],
    actor: User,
    responsible: User | None,
    dry_run: bool,
) -> tuple[Company, bool]:
    amo_id = int(amo_company.get("id") or 0)
    name = str(amo_company.get("name") or "").strip()[:255] or "(без названия)"  # обрезаем name сразу
    company = Company.objects.filter(amocrm_company_id=amo_id).first()
    created = False
    if company is None:
        company = Company(name=name, created_by=actor, responsible=responsible, amocrm_company_id=amo_id, raw_fields={"source": "amo_api"})
        created = True
    else:
        if name and company.name != name:
            company.name = name[:255]  # обрезаем name при обновлении
    # сохраняем raw_fields (не ломаем существующие)
    try:
        rf = dict(company.raw_fields or {})
    except Exception:
        rf = {}
    rf["amo_api_last"] = amo_company
    company.raw_fields = rf
    if responsible and company.responsible_id != responsible.id:
        company.responsible = responsible
    
    # Извлекаем данные о холодном звонке из custom_fields компании
    cold_call_timestamp = None
    custom_fields = amo_company.get("custom_fields_values") or []
    for cf in custom_fields:
        if not isinstance(cf, dict):
            continue
        field_name = str(cf.get("field_name") or "").lower()
        field_type = str(cf.get("field_type") or "").lower()
        # Проверяем поле "Холодный звонок" с типом "date"
        if field_type == "date" and ("холодный" in field_name and "звонок" in field_name):
            values = cf.get("values") or []
            if values and isinstance(values, list):
                for v in values:
                    if isinstance(v, dict):
                        val = v.get("value")
                    else:
                        val = v
                    if val:
                        try:
                            cold_call_timestamp = int(float(val))
                            break  # Берем первое значение
                        except (ValueError, TypeError):
                            pass
    
    # Устанавливаем данные о холодном звонке для компании
    if cold_call_timestamp:
        try:
            UTC = getattr(timezone, "UTC", dt_timezone.utc)
            cold_marked_at_dt = timezone.datetime.fromtimestamp(cold_call_timestamp, tz=UTC)
            company.primary_contact_is_cold_call = True
            company.primary_cold_marked_at = cold_marked_at_dt
            company.primary_cold_marked_by = responsible or company.created_by or actor
            # primary_cold_marked_call оставляем NULL, т.к. в amoCRM нет связи с CallRequest
        except Exception:
            pass  # Если не удалось распарсить timestamp - пропускаем
    
    if not dry_run:
        try:
            company.save()
        except Exception as e:
            # Если ошибка при сохранении - логируем, но не падаем (company уже создан в памяти)
            logger.error(f"Failed to save company in _upsert_company_from_amo (amo_id={amo_id}): {e}", exc_info=True)
            # Продолжаем - company уже создан в памяти, просто не сохранен в БД
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
    skip_field_filter: bool = False,  # если True, мигрируем все компании ответственного без фильтра по полю
) -> AmoMigrateResult:
    res = AmoMigrateResult(
        preview=[],
        tasks_preview=[],
        notes_preview=[],
        contacts_preview=[],
        companies_updates_preview=[] if dry_run else None,
        contacts_updates_preview=[] if dry_run else None,
    )

    amo_users = fetch_amo_users(client)
    amo_user_by_id = {int(u.get("id") or 0): u for u in amo_users if int(u.get("id") or 0)}
    # Если список пользователей пуст (например, из-за 403), используем пустой словарь
    responsible_local = _map_amo_user_to_local(amo_user_by_id.get(int(responsible_user_id)) or {}) if amo_user_by_id else None
    field_meta = _build_field_meta(company_fields_meta or [])

    # КРИТИЧЕСКИ: ВСЕГДА запрашиваем компании БЕЗ контактов
    # Контакты получаем отдельно через filter[company_id][] - это надежнее и легче
    # Запрос компаний с with=contacts создает огромные ответы и вызывает 504
    companies = fetch_companies_by_responsible(client, responsible_user_id, with_contacts=False)
    res.companies_seen = len(companies)
    matched_all = []
    if skip_field_filter:
        # Мигрируем все компании ответственного без фильтра по полю
        matched_all = companies
    else:
        # Фильтруем по кастомному полю (как раньше)
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
            # ВАЖНО: всегда обрезаем значения до max_length, даже если поле уже заполнено (защита от длинных значений)
            changed = False
            
            # Для dry-run: собираем diff изменений
            company_updates_diff = {} if dry_run else None
            
            # Мягкий режим update: если поле уже меняли руками, не перезаписываем.
            try:
                rf = dict(comp.raw_fields or {})
            except Exception:
                rf = {}
            prev = rf.get("amo_values") or {}
            if not isinstance(prev, dict):
                prev = {}
            
            # Сохраняем старые значения для diff (только при dry_run)
            if dry_run:
                old_values = {
                    "legal_name": comp.legal_name or "",
                    "inn": comp.inn or "",
                    "kpp": comp.kpp or "",
                    "address": comp.address or "",
                    "phone": comp.phone or "",
                    "email": comp.email or "",
                    "website": comp.website or "",
                    "contact_name": comp.contact_name or "",
                    "activity_kind": comp.activity_kind or "",
                    "employees_count": comp.employees_count,
                    "workday_start": str(comp.workday_start) if comp.workday_start else "",
                    "workday_end": str(comp.workday_end) if comp.workday_end else "",
                    "work_timezone": comp.work_timezone or "",
                }

            def can_update(field: str) -> bool:
                cur = getattr(comp, field)
                if cur in ("", None):
                    return True
                if field in prev and prev.get(field) == cur:
                    return True
                return False
            if extra.get("legal_name"):
                new_legal = str(extra["legal_name"]).strip()[:255]  # сначала strip, потом обрезка до max_length=255
                old_legal = (comp.legal_name or "").strip()
                if not old_legal:
                    comp.legal_name = new_legal
                    changed = True
                    if dry_run and new_legal:
                        company_updates_diff["legal_name"] = {"old": "", "new": new_legal}
                elif len(comp.legal_name) > 255:  # защита: если уже заполнено, но слишком длинное
                    comp.legal_name = comp.legal_name.strip()[:255]
                    changed = True
                    if dry_run:
                        company_updates_diff["legal_name"] = {"old": old_legal, "new": comp.legal_name}
            if extra.get("inn"):
                new_inn = str(extra["inn"]).strip()[:20]  # сначала strip, потом обрезка до max_length=20
                old_inn = (comp.inn or "").strip()
                if not old_inn:
                    comp.inn = new_inn
                    changed = True
                    if dry_run and new_inn:
                        company_updates_diff["inn"] = {"old": "", "new": new_inn}
                elif len(comp.inn) > 20:  # защита: если уже заполнено, но слишком длинное
                    comp.inn = comp.inn.strip()[:20]
                    changed = True
                    if dry_run:
                        company_updates_diff["inn"] = {"old": old_inn, "new": comp.inn}
            if extra.get("kpp"):
                new_kpp = str(extra["kpp"]).strip()[:20]  # сначала strip, потом обрезка до max_length=20
                old_kpp = (comp.kpp or "").strip()
                if not old_kpp:
                    comp.kpp = new_kpp
                    changed = True
                    if dry_run and new_kpp:
                        company_updates_diff["kpp"] = {"old": "", "new": new_kpp}
                elif len(comp.kpp) > 20:  # защита: если уже заполнено, но слишком длинное
                    comp.kpp = comp.kpp.strip()[:20]
                    changed = True
                    if dry_run:
                        company_updates_diff["kpp"] = {"old": old_kpp, "new": comp.kpp}
            if extra.get("address"):
                new_addr = str(extra["address"]).strip()[:500]  # сначала strip, потом обрезка до max_length=500
                old_addr = (comp.address or "").strip()
                if not old_addr:
                    comp.address = new_addr
                    changed = True
                    if dry_run and new_addr:
                        company_updates_diff["address"] = {"old": "", "new": new_addr}
                elif len(comp.address) > 500:  # защита: если уже заполнено, но слишком длинное
                    comp.address = comp.address.strip()[:500]
                    changed = True
                    if dry_run:
                        company_updates_diff["address"] = {"old": old_addr, "new": comp.address}
            phones = extra.get("phones") or []
            emails = extra.get("emails") or []
            company_note = str(extra.get("note") or "").strip()[:255]
            # основной телефон/почта — в "Данные", остальные — в отдельный контакт (даже без ФИО/должности)
            if phones and not (comp.phone or "").strip():
                new_phone = str(phones[0])[:50]
                comp.phone = new_phone
                changed = True
                if dry_run:
                    company_updates_diff["phone"] = {"old": "", "new": new_phone}
            if emails and not (comp.email or "").strip():
                new_email = str(emails[0])[:254]
                comp.email = new_email
                changed = True
                if dry_run:
                    company_updates_diff["email"] = {"old": "", "new": new_email}
            if extra.get("website") and not (comp.website or "").strip():
                new_website = extra["website"][:255]
                comp.website = new_website
                changed = True
                if dry_run:
                    company_updates_diff["website"] = {"old": "", "new": new_website}
            # Комментарий к основному телефону компании: импортируем "Примечание" из amoCRM
            # Логика: если примечание одно, пишем его к первому телефону (в Company.phone_comment), не затирая ручное.
            if company_note and not (comp.phone_comment or "").strip():
                # Если основной телефон уже есть/будет — сохраняем комментарий
                if (comp.phone or "").strip() or (phones and str(phones[0]).strip()):
                    comp.phone_comment = company_note[:255]
                    changed = True
                    if dry_run:
                        company_updates_diff["phone_comment"] = {"old": "", "new": company_note[:255]}
            if extra.get("activity_kind") and can_update("activity_kind"):
                ak = str(extra.get("activity_kind") or "").strip()[:255]
                old_ak = (comp.activity_kind or "").strip()
                if ak and comp.activity_kind != ak:
                    comp.activity_kind = ak
                    changed = True
                    if dry_run:
                        company_updates_diff["activity_kind"] = {"old": old_ak, "new": ak}
            if extra.get("employees_count") and can_update("employees_count"):
                try:
                    ec = int("".join(ch for ch in str(extra.get("employees_count") or "") if ch.isdigit()) or "0")
                    old_ec = comp.employees_count
                    if ec > 0 and comp.employees_count != ec:
                        comp.employees_count = ec
                        changed = True
                        if dry_run:
                            company_updates_diff["employees_count"] = {"old": str(old_ec) if old_ec else "", "new": str(ec)}
                except Exception:
                    pass
            if extra.get("work_timezone") and can_update("work_timezone"):
                tzv = str(extra.get("work_timezone") or "").strip()[:64]
                old_tz = (comp.work_timezone or "").strip()
                if tzv and comp.work_timezone != tzv:
                    comp.work_timezone = tzv
                    changed = True
                    if dry_run:
                        company_updates_diff["work_timezone"] = {"old": old_tz, "new": tzv}
            if extra.get("worktime"):
                # поддерживаем форматы: "09:00-18:00", "09:00–18:00", "с 9:00 до 18:00"
                import re
                s = str(extra.get("worktime") or "").replace("–", "-").strip()
                m = re.search(r"(\d{1,2})[:.](\d{2})\s*-\s*(\d{1,2})[:.](\d{2})", s)
                if m:
                    try:
                        h1, m1, h2, m2 = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
                        if 0 <= h1 <= 23 and 0 <= h2 <= 23 and 0 <= m1 <= 59 and 0 <= m2 <= 59:
                            old_start = str(comp.workday_start) if comp.workday_start else ""
                            old_end = str(comp.workday_end) if comp.workday_end else ""
                            if can_update("workday_start") and comp.workday_start != time(h1, m1):
                                comp.workday_start = time(h1, m1)
                                changed = True
                                if dry_run:
                                    company_updates_diff["workday_start"] = {"old": old_start, "new": str(time(h1, m1))}
                            if can_update("workday_end") and comp.workday_end != time(h2, m2):
                                comp.workday_end = time(h2, m2)
                                changed = True
                                if dry_run:
                                    company_updates_diff["workday_end"] = {"old": old_end, "new": str(time(h2, m2))}
                    except Exception:
                        pass
            # Руководитель (contact_name) — заполняем из amo, если пусто
            if extra.get("director") and not (comp.contact_name or "").strip():
                new_director = extra["director"][:255]
                comp.contact_name = new_director
                changed = True
                if dry_run:
                    company_updates_diff["contact_name"] = {"old": "", "new": new_director}

            if changed:
                prev.update(
                    {
                        "legal_name": comp.legal_name,
                        "inn": comp.inn,
                        "kpp": comp.kpp,
                        "address": comp.address,
                        "phone": comp.phone,
                        "email": comp.email,
                        "website": comp.website,
                        "director": comp.contact_name,
                        "activity_kind": comp.activity_kind,
                        "employees_count": comp.employees_count,
                        "workday_start": comp.workday_start,
                        "workday_end": comp.workday_end,
                        "work_timezone": comp.work_timezone,
                    }
                )
                rf["amo_values"] = prev
                comp.raw_fields = rf
            
            # Сохраняем diff изменений для dry-run
            if dry_run and company_updates_diff:
                if res.companies_updates_preview is None:
                    res.companies_updates_preview = []
                res.companies_updates_preview.append({
                    "company_name": comp.name,
                    "company_id": comp.id if comp.id else None,
                    "amo_id": comp.amocrm_company_id,
                    "is_new": created,
                    "updates": company_updates_diff,
                })
            
            if changed and not dry_run:
                try:
                    comp.save()
                except Exception as e:
                    # Если ошибка при сохранении - логируем и пропускаем эту компанию
                    logger.error(f"Failed to save company {comp.name} (amo_id={comp.amocrm_company_id}): {e}", exc_info=True)
                    # Пропускаем эту компанию, продолжаем со следующей
                    continue

            # Нормализация уже заполненных значений (часто там "номер1, номер2"):
            # оставляем в "Данные" только первый, остальные переносим в служебный контакт.
            norm_phone_parts = _split_multi(comp.phone or "")
            norm_email_parts = _split_multi(comp.email or "")
            if len(norm_phone_parts) > 1 and not dry_run:
                try:
                    comp.phone = norm_phone_parts[0][:50]
                    comp.save(update_fields=["phone"])
                    # добавим остальные как контактные телефоны
                    phones = list(dict.fromkeys([*phones, *norm_phone_parts]))
                except Exception as e:
                    logger.error(f"Failed to save phone for company {comp.name}: {e}", exc_info=True)
            if len(norm_email_parts) > 1 and not dry_run:
                try:
                    comp.email = norm_email_parts[0][:254]
                    comp.save(update_fields=["email"])
                    emails = list(dict.fromkeys([*emails, *norm_email_parts]))
                except Exception as e:
                    logger.error(f"Failed to save email for company {comp.name}: {e}", exc_info=True)

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

        # Задачи: запрашиваем только если нужно импортировать (не для dry-run без задач)
        if import_tasks and amo_ids and not (dry_run and not import_tasks):
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
                
                # Фильтрация: импортируем только задачи с дедлайном на 2026 год и позже
                if due_at and due_at.year < 2026:
                    res.tasks_skipped_old += 1
                    continue
                
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

        # Заметки: запрашиваем только если нужно импортировать (не для dry-run без заметок)
        if import_notes and amo_ids and not (dry_run and not import_notes):
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
                    # amomail_message — это по сути история почты; пропускаем такие заметки
                    if note_type.lower().startswith("amomail"):
                        # Пропускаем импорт писем из amoCRM
                        continue
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
                        # НО: если это amomail - пропускаем (не обновляем и не создаем)
                        if note_type.lower().startswith("amomail"):
                            continue
                        # Переписываем также любые почтовые записи, которые раньше импортировали как JSON-простыню.
                        should_rewrite = (
                            old_text.startswith("Импорт из amo (note id")
                            or len(old_text) < 40
                            or ("type: amomail" in old_text.lower())
                            or ("\"thread_id\"" in old_text)
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
        # Инициализируем счетчики контактов всегда (даже если импорт выключен)
        res.contacts_seen = 0
        res.contacts_created = 0
        
        # В DRY-RUN всегда показываем контакты (даже если import_contacts=False),
        # чтобы пользователь мог увидеть, что будет импортировано
        # В реальном импорте обрабатываем только если import_contacts=True
        should_process_contacts = (dry_run or import_contacts) and amo_ids
        
        logger.info(f"migrate_filtered: проверка импорта контактов: import_contacts={import_contacts}, dry_run={dry_run}, should_process_contacts={should_process_contacts}, amo_ids={bool(amo_ids)}, len={len(amo_ids) if amo_ids else 0}")
        if should_process_contacts:
            res._debug_contacts_logged = 0  # счетчик для отладки
            contacts_processed = 0  # счетчик обработанных контактов
            contacts_skipped = 0  # счетчик пропущенных контактов
            logger.info(f"migrate_filtered: ===== НАЧАЛО ИМПОРТА КОНТАКТОВ для {len(amo_ids)} компаний =====")
            logger.info(f"migrate_filtered: ID компаний для поиска контактов: {amo_ids[:10]}...")
            try:
                # Получаем контакты через with=contacts при запросе компаний
                # Rate limiting применяется автоматически в AmoClient
                logger.info(f"migrate_filtered: вызываем fetch_contacts_for_companies для {len(amo_ids)} компаний...")
                
                all_contacts = fetch_contacts_for_companies(client, amo_ids)
                logger.info(f"migrate_filtered: получено {len(all_contacts)} контактов из API для {len(amo_ids)} компаний")
                
                # КРИТИЧЕСКИ ВАЖНО: фильтруем контакты - оставляем ТОЛЬКО те, которые связаны с компаниями из текущей пачки
                # Контакт может быть связан с несколькими компаниями, но нам нужны только те, что связаны с нашими
                amo_ids_set = set(amo_ids)
                full_contacts: list[dict[str, Any]] = []
                contact_id_to_company_map: dict[int, int] = {}  # contact_id -> amo_company_id
                
                logger.info(f"migrate_filtered: начинаем фильтрацию {len(all_contacts)} контактов по компаниям из пачки: {list(amo_ids_set)[:10]}...")
                contacts_with_company = 0
                contacts_without_company = 0
                
                for contact in all_contacts:
                    if not isinstance(contact, dict):
                        contacts_without_company += 1
                        continue
                    
                    contact_id = int(contact.get("id") or 0)
                    if not contact_id:
                        contacts_without_company += 1
                        continue
                    
                    # Ищем компанию из текущей пачки, с которой связан контакт
                    found_company_id = None
                    
                    # Проверяем _embedded.companies
                    embedded = contact.get("_embedded") or {}
                    companies_in_contact = embedded.get("companies") or []
                    if isinstance(companies_in_contact, list):
                        for comp_ref in companies_in_contact:
                            if isinstance(comp_ref, dict):
                                comp_id = int(comp_ref.get("id") or 0)
                                if comp_id in amo_ids_set:
                                    found_company_id = comp_id
                                    break
                    
                    # Если не нашли через _embedded, проверяем company_id напрямую
                    if not found_company_id:
                        comp_id_direct = int(contact.get("company_id") or 0)
                        if comp_id_direct in amo_ids_set:
                            found_company_id = comp_id_direct
                    
                    # Добавляем контакт ТОЛЬКО если он связан с компанией из текущей пачки
                    if found_company_id:
                        full_contacts.append(contact)
                        contact_id_to_company_map[contact_id] = found_company_id
                        contacts_with_company += 1
                    else:
                        contacts_without_company += 1
                
                res.contacts_seen = len(full_contacts)
                logger.info(f"migrate_filtered: фильтрация завершена: {res.contacts_seen} контактов принадлежат {len(amo_ids)} компаниям из текущей пачки (пропущено: {contacts_without_company})")
                
                # Если контактов не найдено, сохраняем информацию об ошибке
                if res.contacts_seen == 0:
                    logger.warning(f"migrate_filtered: ⚠️ КОНТАКТЫ НЕ НАЙДЕНЫ для компаний {list(amo_ids)[:10]}. Всего получено из API: {len(all_contacts)}, отфильтровано: {res.contacts_seen}")
                    if res.contacts_preview is None:
                        res.contacts_preview = []
                    debug_info = {
                        "status": "NO_CONTACTS_FOUND",
                        "companies_checked": len(amo_ids),
                        "company_ids": list(amo_ids)[:5],  # первые 5 для отладки
                        "message": f"Контакты не найдены для компаний {list(amo_ids)[:5]}. Проверьте, что у компаний есть связанные контакты в AmoCRM. Использовались методы: 1) GET /api/v4/companies/{{id}}?with=contacts, 2) GET /api/v4/contacts?filter[company_id]={{id}}. Всего получено из API: {len(all_contacts)} контактов, но ни один не связан с компаниями из текущей пачки.",
                    }
                    res.contacts_preview.append(debug_info)
                
                # Заметки контактов: НЕ запрашиваем для dry-run (слишком тяжело)
                # Заметки нужны только при реальном импорте, и то можно запросить отдельно
                contact_notes_map: dict[int, list[dict[str, Any]]] = {}
                if not dry_run and full_contacts:
                    # Заметки запрашиваем только при реальном импорте, и то очень аккуратно
                    contact_ids_for_notes = [int(c.get("id") or 0) for c in full_contacts if isinstance(c, dict) and c.get("id")]
                    if contact_ids_for_notes and len(contact_ids_for_notes) <= 50:  # Только для небольшого количества
                        logger.debug(f"Fetching notes for {len(contact_ids_for_notes)} contacts (real import only)...")
                        try:
                            contact_notes_map = fetch_notes_for_contacts(client, contact_ids_for_notes)
                        except Exception as e:
                            logger.debug(f"Error fetching contact notes: {e}", exc_info=True)
                
                # Отдельный счетчик для логирования структуры (не зависит от preview)
                structure_logged_count = 0
                
                # Создаем словарь для быстрого поиска компаний по amo_id
                # В dry-run используем local_companies (которые созданы в памяти, но не сохранены в БД)
                # В реальном импорте используем БД
                local_companies_by_amo_id: dict[int, Company] = {}
                if dry_run:
                    # В dry-run используем компании из local_companies (созданные в памяти)
                    for comp in local_companies:
                        if comp.amocrm_company_id:
                            local_companies_by_amo_id[int(comp.amocrm_company_id)] = comp
                    logger.info(f"migrate_filtered: создан словарь local_companies_by_amo_id для dry-run: {len(local_companies_by_amo_id)} компаний")
                else:
                    # В реальном импорте загружаем из БД
                    for comp in local_companies:
                        if comp.amocrm_company_id:
                            local_companies_by_amo_id[int(comp.amocrm_company_id)] = comp
                    # Также загружаем существующие компании из БД (на случай, если они уже были импортированы ранее)
                    existing_companies = Company.objects.filter(amocrm_company_id__in=amo_ids).all()
                    for comp in existing_companies:
                        if comp.amocrm_company_id:
                            local_companies_by_amo_id[int(comp.amocrm_company_id)] = comp
                
                # Теперь обрабатываем полные данные контактов
                logger.info(f"migrate_filtered: ===== НАЧАЛО ОБРАБОТКИ {len(full_contacts)} КОНТАКТОВ =====")
                contacts_processed = 0
                contacts_skipped = 0
                contacts_errors = 0
                for ac_idx, ac in enumerate(full_contacts):
                    contacts_processed += 1
                    if ac_idx < 5 or contacts_processed % 10 == 0:
                        logger.info(f"migrate_filtered: обработка контакта {ac_idx + 1}/{len(full_contacts)} (processed: {contacts_processed}, skipped: {contacts_skipped}, errors: {contacts_errors})")
                    
                    try:
                        # ОТЛАДКА: логируем сырую структуру контакта для первых 3
                        if structure_logged_count < 3:
                            logger.debug(f"===== RAW CONTACT STRUCTURE ({structure_logged_count + 1}) [index {ac_idx}] =====")
                            logger.debug(f"  - Type: {type(ac)}")
                            logger.debug(f"  - ac is None: {ac is None}")
                            if ac is None:
                                logger.debug(f"  - ⚠️ Contact is None!")
                            elif isinstance(ac, dict):
                                logger.debug(f"  - Keys: {list(ac.keys())}")
                            logger.debug(f"  - Has 'id': {'id' in ac}, id value: {ac.get('id')}")
                            logger.debug(f"  - Has 'first_name': {'first_name' in ac}, value: {ac.get('first_name')}")
                            logger.debug(f"  - Has 'last_name': {'last_name' in ac}, value: {ac.get('last_name')}")
                            logger.debug(f"  - Has 'custom_fields_values': {'custom_fields_values' in ac}")
                            if 'custom_fields_values' in ac:
                                cfv = ac.get('custom_fields_values')
                                logger.debug(f"  - custom_fields_values type: {type(cfv)}, length: {len(cfv) if isinstance(cfv, list) else 'not_list'}")
                                if isinstance(cfv, list) and len(cfv) > 0:
                                    logger.debug(f"  - First custom_field: {cfv[0]}")
                            logger.debug(f"  - Has 'phone': {'phone' in ac}, value: {ac.get('phone')}")
                            logger.debug(f"  - Has 'email': {'email' in ac}, value: {ac.get('email')}")
                            # Полная JSON-структура - ВАЖНО для поиска примечаний!
                            import json
                            try:
                                json_str = json.dumps(ac, ensure_ascii=False, indent=2)
                                # Увеличиваем размер для поиска примечаний
                                logger.debug(f"  - Full JSON (first 5000 chars):\n{json_str[:5000]}")
                                # Также проверяем наличие ключевых полей для примечаний
                                note_related_keys = [k for k in ac.keys() if any(word in k.lower() for word in ["note", "comment", "remark", "примеч", "коммент"])]
                                if note_related_keys:
                                    logger.debug(f"  - ⚠️ Found note-related keys: {note_related_keys}")
                                    for key in note_related_keys:
                                        logger.debug(f"    - {key}: {str(ac.get(key))[:200]}")
                            except Exception as e:
                                logger.debug(f"  - JSON dump error: {e}")
                                import traceback
                                logger.debug(f"  - Traceback: {traceback.format_exc()}")
                                logger.debug(f"  - Full contact (first 500 chars): {str(ac)[:500]}")
                        else:
                            logger.debug(f"  - Contact is not a dict: {ac}, type: {type(ac)}")
                        logger.debug(f"===== END RAW STRUCTURE =====")
                        structure_logged_count += 1
                        
                        amo_contact_id = int(ac.get("id") or 0) if isinstance(ac, dict) else 0
                        
                        # Добавляем заметки из contact_notes_map, если их нет в _embedded
                        if amo_contact_id and amo_contact_id in contact_notes_map:
                            notes_from_map = contact_notes_map[amo_contact_id]
                            if notes_from_map and isinstance(ac, dict):
                                # Добавляем заметки в _embedded, если их там нет
                                if "_embedded" not in ac:
                                    ac["_embedded"] = {}
                                if not isinstance(ac["_embedded"], dict):
                                    ac["_embedded"] = {}
                                if "notes" not in ac["_embedded"] or not ac["_embedded"]["notes"]:
                                    ac["_embedded"]["notes"] = notes_from_map
                                    if structure_logged_count < 3:
                                        logger.debug(f"  -> Added {len(notes_from_map)} notes from contact_notes_map to contact {amo_contact_id}")
                        
                        if not amo_contact_id:
                            # ОТЛАДКА: контакт без ID
                            contacts_skipped += 1
                            debug_count = getattr(res, '_debug_contacts_logged', 0)
                            if res.contacts_preview is None:
                                res.contacts_preview = []
                            preview_limit_skip = 50 if dry_run else 10
                            if debug_count < preview_limit_skip:
                                res._debug_contacts_logged = debug_count + 1
                                res.contacts_preview.append({
                                    "status": "SKIPPED_NO_ID",
                                    "raw_contact_keys": list(ac.keys())[:10] if isinstance(ac, dict) else "not_dict",
                                })
                            continue
                        
                        # Находим компанию для этого контакта через contact_id_to_company_map
                        # ВАЖНО: в dry-run используем local_companies_by_amo_id (компании в памяти)
                        # В реальном импорте используем БД или local_companies_by_amo_id
                        local_company = None
                        amo_company_id_for_contact = None
                        
                        contact_id = int(ac.get("id") or 0)
                        if contact_id in contact_id_to_company_map:
                            amo_company_id_for_contact = contact_id_to_company_map[contact_id]
                            # Сначала ищем в словаре (работает и для dry-run, и для реального импорта)
                            local_company = local_companies_by_amo_id.get(amo_company_id_for_contact)
                            # Если не нашли в словаре и это не dry-run, ищем в БД
                            if not local_company and not dry_run:
                                local_company = Company.objects.filter(amocrm_company_id=amo_company_id_for_contact).first()
                        
                        # Fallback: если не нашли через map, пробуем через company_id в самом контакте
                        if not local_company:
                            cid = int(ac.get("company_id") or 0)
                            if cid and cid in amo_ids_set:
                                # Сначала ищем в словаре
                                local_company = local_companies_by_amo_id.get(cid)
                                # Если не нашли в словаре и это не dry-run, ищем в БД
                                if not local_company and not dry_run:
                                    local_company = Company.objects.filter(amocrm_company_id=cid).first()
                                if local_company:
                                    amo_company_id_for_contact = cid
                        
                        if not local_company:
                            # ОТЛАДКА: контакт не связан с компанией из текущей пачки
                            # В dry-run показываем ВСЕ такие контакты
                            debug_count = getattr(res, '_debug_contacts_logged', 0)
                        if res.contacts_preview is None:
                            res.contacts_preview = []
                        preview_limit_skip = 1000 if dry_run else 10
                        if debug_count < preview_limit_skip:
                            # Полный анализ контакта даже если компания не найдена
                            full_analysis_skipped = _analyze_contact_completely(ac)
                            name_str = str(ac.get("name") or "").strip()
                            first_name_raw = str(ac.get("first_name") or "").strip()
                            last_name_raw = str(ac.get("last_name") or "").strip()
                            last_name_skipped, first_name_skipped = _parse_fio(name_str, first_name_raw, last_name_raw)
                            
                            debug_data = {
                                "status": "SKIPPED_NO_LOCAL_COMPANY",
                                "amo_contact_id": amo_contact_id,
                                "last_name": last_name_skipped,
                                "first_name": first_name_skipped,
                                "amo_company_id_for_contact": amo_company_id_for_contact,
                                "standard_fields": full_analysis_skipped.get("standard_fields", {}),
                                "all_custom_fields": [
                                    {
                                        "field_id": cf.get("field_id"),
                                        "field_name": cf.get("field_name"),
                                        "field_code": cf.get("field_code"),
                                        "field_type": cf.get("field_type"),
                                        "values_count": cf.get("values_count", 0),
                                        "values": [
                                            {
                                                "value": str(v.get("value", "")),
                                                "enum_code": v.get("enum_code"),
                                                "enum_id": v.get("enum_id"),
                                            }
                                            for v in cf.get("values", [])
                                        ],
                                    }
                                    for cf in full_analysis_skipped.get("custom_fields", [])
                                ],
                                "custom_fields_count": len(full_analysis_skipped.get("custom_fields", [])),
                            }
                            res.contacts_preview.append(debug_data)
                            res._debug_contacts_logged = debug_count + 1
                        continue
                        # Извлекаем данные контакта (делаем это ДО проверки на existing, чтобы всегда было в preview)
                        # Парсим ФИО с помощью функции _parse_fio
                        name_str = str(ac.get("name") or "").strip()
                        first_name_raw = str(ac.get("first_name") or "").strip()
                        last_name_raw = str(ac.get("last_name") or "").strip()
                        last_name, first_name = _parse_fio(name_str, first_name_raw, last_name_raw)
                    
                        # ОТЛАДКА: логируем начало обработки контакта
                        preview_count_before = len(res.contacts_preview) if res.contacts_preview else 0
                        if preview_count_before < 3:
                            logger.debug(f"Processing contact {amo_contact_id} (parsed: last_name={last_name}, first_name={first_name})")
                            logger.debug(f"  - raw: name={name_str}, first_name={first_name_raw}, last_name={last_name_raw}")
                        logger.debug(f"  - local_company: {local_company.id if local_company else None}")
                        logger.debug(f"  - has custom_fields_values: {'custom_fields_values' in ac if isinstance(ac, dict) else False}")
                        if isinstance(ac, dict) and 'custom_fields_values' in ac:
                            cfv = ac.get('custom_fields_values')
                            logger.debug(f"  - custom_fields_values: type={type(cfv)}, length={len(cfv) if isinstance(cfv, list) else 'not_list'}")
                        
                        # Проверяем, не импортировали ли уже этот контакт
                        existing_contact = Contact.objects.filter(amocrm_contact_id=amo_contact_id, company=local_company).first()
                    
                        # В amoCRM телефоны и email могут быть:
                        # 1. В стандартных полях (phone, email) - если они есть
                        # 2. В custom_fields_values с field_code="PHONE"/"EMAIL" или по field_name
                        # 3. В custom_fields_values по названию поля
                        # phones/emails: сохраняем тип и комментарий (enum_code) для корректного отображения
                        phones: list[tuple[str, str, str]] = []  # (type, value, comment)
                        emails: list[tuple[str, str]] = []  # (type, value)
                        position = ""
                        cold_call_timestamp = None  # Timestamp холодного звонка из amoCRM
                        note_text = ""  # "Примечание"/"Комментарий" контакта (одно на все номера)
                        birthday_timestamp = None  # Timestamp дня рождения из amoCRM (если есть)
                    
                        # ОТЛАДКА: определяем счетчик для логирования (ДО использования)
                        debug_count_for_extraction = len(res.contacts_preview) if res.contacts_preview else 0
                    
                        # ВАЖНО: сначала проверяем custom_fields (там хранится поле "Примечание"),
                        # потом заметки (там могут быть служебные заметки типа call_out)
                    
                        # custom_fields_values для телефонов/почт/должности/примечаний
                        custom_fields = ac.get("custom_fields_values") or []
                        # ОТЛАДКА: логируем структуру custom_fields для первых контактов
                        if debug_count_for_extraction < 3:
                        logger.debug(f"Extracting data from custom_fields for contact {amo_contact_id}:")
                        logger.debug(f"  - custom_fields type: {type(custom_fields)}, length: {len(custom_fields) if isinstance(custom_fields, list) else 'not_list'}")
                        logger.debug(f"  - ac.get('custom_fields_values'): {ac.get('custom_fields_values')}")
                        # Логируем ВСЕ поля для отладки (чтобы увидеть, какие поля есть)
                        if isinstance(custom_fields, list):
                            logger.debug(f"  - ALL custom_fields ({len(custom_fields)} fields):")
                            for cf_idx, cf in enumerate(custom_fields):
                                if isinstance(cf, dict):
                                    field_name = str(cf.get('field_name') or '').strip()
                                    field_code = str(cf.get('field_code') or '').strip()
                                    values = cf.get('values') or []
                                    first_val = ""
                                    if values and isinstance(values, list) and len(values) > 0:
                                        v = values[0]
                                        if isinstance(v, dict):
                                            first_val = str(v.get('value', ''))[:100]
                                        else:
                                            first_val = str(v)[:100]
                                    logger.debug(f"    [{cf_idx}] id={cf.get('field_id')}, code='{field_code}', name='{field_name}', type={cf.get('field_type')}, first_value='{first_val}'")
                        else:
                            logger.debug(f"  - ⚠️ custom_fields is not a list: {type(custom_fields)}")
                    
                        # ПРОВЕРЯЕМ ВСЕ ВОЗМОЖНЫЕ МЕСТА ДЛЯ ПРИМЕЧАНИЙ:
                        # 1. Прямые поля контакта - проверяем ВСЕ возможные варианты
                        # В amoCRM примечание может быть в разных полях
                        direct_note_keys = ["note", "notes", "comment", "comments", "remark", "remarks", "text", "description", "description_text"]
                        for note_key in direct_note_keys:
                        note_val_raw = ac.get(note_key)
                        if note_val_raw:
                            # Может быть строка или список
                            if isinstance(note_val_raw, list):
                                note_val = " ".join([str(v) for v in note_val_raw if v]).strip()
                            else:
                                note_val = str(note_val_raw).strip()
                            # Пропускаем ID и очень короткие значения
                            if note_val and len(note_val) > 3 and not note_val.isdigit():
                                if not note_text:
                                    note_text = note_val[:255]
                                    if debug_count_for_extraction < 3:
                                        logger.debug(f"  -> ✅ Found note_text in direct field '{note_key}': {note_text[:100]}")
                                else:
                                    # Объединяем, если уже есть
                                    combined = f"{note_text}; {note_val[:100]}"
                                    note_text = combined[:255]
                                    if debug_count_for_extraction < 3:
                                        logger.debug(f"  -> Appended note_text from direct field '{note_key}': {note_val[:100]}")
                    
                        # 2. В custom_fields_values - ПРИОРИТЕТ! Здесь хранится поле "Примечание"
                        # (обработка будет ниже в цикле по custom_fields)
                    
                        # 3. В _embedded.notes (если есть) - это заметки контакта из amoCRM (служебные, не примечания)
                        if isinstance(ac, dict) and "_embedded" in ac:
                        embedded = ac.get("_embedded") or {}
                        if isinstance(embedded, dict) and "notes" in embedded:
                            notes_list = embedded.get("notes") or []
                            if isinstance(notes_list, list) and notes_list:
                                if debug_count_for_extraction < 3:
                                    logger.debug(f"  -> Found {len(notes_list)} notes in _embedded.notes")
                                # Ищем примечание в заметках (обычно это текстовые заметки)
                                for note_idx, note_item in enumerate(notes_list):
                                    if isinstance(note_item, dict):
                                        # В заметках текст может быть в разных полях
                                        note_val = (
                                            str(note_item.get("text") or "").strip() or
                                            str(note_item.get("note") or "").strip() or
                                            str(note_item.get("comment") or "").strip() or
                                            str(note_item.get("note_type") or "").strip()  # иногда тип заметки содержит текст
                                        )
                                        # Также проверяем параметры заметки
                                        if not note_val and "params" in note_item:
                                            params = note_item.get("params") or {}
                                            if isinstance(params, dict):
                                                note_val = (
                                                    str(params.get("text") or "").strip() or
                                                    str(params.get("comment") or "").strip() or
                                                    str(params.get("note") or "").strip()
                                                )
                                        
                                        # ВАЖНО: не берем служебные заметки (call_out, call_in и т.д.) как примечание
                                        # Но берем заметки типа "common", "text", "common_message" - это могут быть примечания!
                                        note_type_val = str(note_item.get("note_type") or "").strip().lower()
                                        is_service_note = note_type_val in ["call_out", "call_in", "call", "amomail", "sms", "task"]
                                        is_note_type = note_type_val in ["common", "text", "common_message", "message", "note"]
                                        
                                        # Берем заметки типа "common"/"text" (это примечания) или любые заметки с текстом, если нет служебных
                                        if note_val and len(note_val) > 5:
                                            # ВАЖНО: заметки типа "common" или "text" - это ПРИОРИТЕТНЫЕ примечания
                                            # Они должны заменять служебные заметки (call_out и т.д.)
                                            if is_note_type:
                                                # Заменяем, если нет примечания ИЛИ если текущее примечание - служебная заметка
                                                current_is_service = note_text and (
                                                    "call_" in str(note_text).lower() or 
                                                    str(note_text).lower() in ["call_out", "call_in", "call", "amomail", "sms", "task"] or
                                                    len(str(note_text).strip()) < 10
                                                )
                                                if not note_text or current_is_service:
                                                    note_text = note_val[:255]
                                                    if debug_count_for_extraction < 3:
                                                        logger.debug(f"  -> ✅ Found note_text in _embedded.notes[{note_idx}] (type={note_type_val}): {note_text[:100]}")
                                                else:
                                                    combined = f"{note_text}; {note_val[:100]}"
                                                    note_text = combined[:255]
                                                    if debug_count_for_extraction < 3:
                                                        logger.debug(f"  -> Appended note_text from _embedded.notes[{note_idx}] (type={note_type_val}): {note_val[:100]}")
                                            # Если это не служебная заметка и у нас еще нет примечания - берем её
                                            elif not is_service_note and not note_text:
                                                note_text = note_val[:255]
                                                if debug_count_for_extraction < 3:
                                                    logger.debug(f"  -> Found note_text in _embedded.notes[{note_idx}] (type={note_type_val}, not service): {note_text[:100]}")
                                            # Берем первые 5 заметок (чтобы найти примечание)
                                            if note_idx >= 4:
                                                break
                                        elif is_service_note and debug_count_for_extraction < 3:
                                            logger.debug(f"  -> Skipped service note type '{note_type_val}' (not a real note)")
                    
                        # Стандартные поля (если есть)
                        if ac.get("phone"):
                        for pv in _split_multi(str(ac.get("phone"))):
                            phones.append((ContactPhone.PhoneType.OTHER, pv, ""))
                        if ac.get("email"):
                        ev = str(ac.get("email")).strip()
                        if ev:
                            emails.append((ContactEmail.EmailType.OTHER, ev))
                    
                        # custom_fields_values для телефонов/почт/должности/примечаний
                        custom_fields = ac.get("custom_fields_values") or []
                        # ОТЛАДКА: логируем структуру custom_fields для первых контактов
                        if debug_count_for_extraction < 3:
                        logger.debug(f"Extracting data from custom_fields for contact {amo_contact_id}:")
                        logger.debug(f"  - custom_fields type: {type(custom_fields)}, length: {len(custom_fields) if isinstance(custom_fields, list) else 'not_list'}")
                        logger.debug(f"  - ac.get('custom_fields_values'): {ac.get('custom_fields_values')}")
                        # Логируем ВСЕ ключи контакта для поиска примечаний
                        if isinstance(ac, dict):
                            all_keys = list(ac.keys())
                            logger.debug(f"  - ALL contact keys: {all_keys}")
                            # Проверяем наличие полей, которые могут содержать примечания
                            for key in ["note", "notes", "comment", "comments", "remark", "remarks", "_embedded"]:
                                if key in ac:
                                    logger.debug(f"  - Found key '{key}': {str(ac.get(key))[:200]}")
                        # Логируем уже найденное примечание (если есть)
                        if note_text:
                            logger.debug(f"  - Already found note_text from direct fields: {note_text[:100]}")
                    
                        for cf_idx, cf in enumerate(custom_fields):
                        if not isinstance(cf, dict):
                            if debug_count_for_extraction < 3:
                                logger.debug(f"  - [field {cf_idx}] Skipped: not a dict, type={type(cf)}")
                            continue
                        field_id = int(cf.get("field_id") or 0)
                        # ВАЖНО: в amoCRM используется field_code (не code) и field_name (не name)
                        field_code = str(cf.get("field_code") or "").upper()  # PHONE, EMAIL в верхнем регистре
                        field_name = str(cf.get("field_name") or "").lower()  # "телефон", "должность"
                        field_type = str(cf.get("field_type") or "").lower()  # "multitext", "text", "date"
                        values = cf.get("values") or []
                        if not isinstance(values, list):
                            if debug_count_for_extraction < 3:
                                logger.debug(f"  - [field {cf_idx}] Skipped: values not a list, type={type(values)}")
                            continue
                        
                        if debug_count_for_extraction < 3:
                            logger.debug(f"  - [field {cf_idx}] field_id={field_id}, field_code={field_code}, field_name={field_name}, field_type={field_type}, values_count={len(values)}")
                        
                        for v_idx, v in enumerate(values):
                            # Согласно документации AmoCRM API v4:
                            # Значение может быть dict с полями: value, enum_id, enum_code
                            # Также может быть поле "enum" (строка) для обратной совместимости
                            if isinstance(v, dict):
                                # value может быть строкой, числом или объектом (для сложных типов)
                                value_raw = v.get("value")
                                if value_raw is None:
                                    continue
                                # Преобразуем value в строку (для телефонов/email это всегда строка)
                                if isinstance(value_raw, (str, int, float)):
                                    val = str(value_raw).strip()
                                elif isinstance(value_raw, dict):
                                    # Для сложных типов (например, связь с другими сущностями)
                                    # Пытаемся извлечь текстовое представление
                                    val = str(value_raw.get("value") or value_raw.get("name") or str(value_raw)).strip()
                                else:
                                    val = str(value_raw).strip()
                                
                                # enum_id - числовой идентификатор enum
                                enum_id = v.get("enum_id")
                                
                                # enum_code - строковый код enum (WORK, MOBILE и т.д.)
                                # Также проверяем поле "enum" для обратной совместимости
                                enum_code = v.get("enum_code") or v.get("enum")
                                if enum_code and not isinstance(enum_code, str):
                                    enum_code = str(enum_code)
                            elif isinstance(v, str):
                                val = v.strip()
                                enum_id = None
                                enum_code = None
                            else:
                                val = str(v).strip() if v else ""
                                enum_id = None
                                enum_code = None
                            
                            if not val:
                                continue
                            
                            # Телефоны: проверяем field_code="PHONE" или field_name содержит "телефон"
                            # В amoCRM field_type для телефонов обычно "multitext"
                            is_phone = (field_code == "PHONE" or 
                                       "телефон" in field_name)
                            # Email: проверяем field_code="EMAIL" или field_name содержит "email"/"почта"
                            is_email = (field_code == "EMAIL" or
                                       "email" in field_name or "почта" in field_name or "e-mail" in field_name)
                            # Должность: проверяем field_code="POSITION" или field_name содержит "должность"/"позиция"
                            is_position = (field_code == "POSITION" or
                                          "должность" in field_name or "позиция" in field_name)
                            # Холодный звонок: проверяем field_id=448321 (из примера), field_name и field_type="date"
                            is_cold_call_date = (
                                field_id == 448321 or  # Известный ID поля "Холодный звонок" из примера
                                (field_type == "date" and ("холодный" in field_name and "звонок" in field_name))
                            )
                            # День рождения: проверяем field_type="birthday" или field_name содержит "день рождения"/"birthday"
                            is_birthday = (
                                field_type == "birthday" or
                                ("день" in field_name and "рождени" in field_name) or
                                "birthday" in field_name.lower()
                            )
                            # Примечание/Комментарий (текстовое поле)
                            # Проверяем field_id=366537 (из примера), field_name, и field_code для большей надежности
                            is_note = (
                                field_id == 366537 or  # Известный ID поля "Примечание" из примера
                                any(k in field_name for k in ["примеч", "комментар", "коммент", "заметк"]) or
                                any(k in str(field_code or "").upper() for k in ["NOTE", "COMMENT", "REMARK"])
                            )
                            
                            if debug_count_for_extraction < 3:
                                logger.debug(f"    [value {v_idx}] val={val[:50]}, is_phone={is_phone}, is_email={is_email}, is_position={is_position}, is_cold_call_date={is_cold_call_date}, is_birthday={is_birthday}, is_note={is_note}")
                            
                            if is_phone:
                                # Определяем тип телефона:
                                # 1. По enum_code (WORK/MOBILE/...)
                                # 2. По названию поля (если содержит "раб" - WORK, "моб" - MOBILE)
                                t = str(enum_code or "").upper()
                                field_name_lower = field_name.lower()
                                
                                if t in ("WORK", "WORKDD", "WORK_DIRECT") or "раб" in field_name_lower:
                                    ptype = ContactPhone.PhoneType.WORK
                                elif t in ("MOBILE", "CELL") or "моб" in field_name_lower:
                                    ptype = ContactPhone.PhoneType.MOBILE
                                elif t == "HOME" or "дом" in field_name_lower:
                                    ptype = ContactPhone.PhoneType.HOME
                                elif t == "FAX" or "факс" in field_name_lower:
                                    ptype = ContactPhone.PhoneType.FAX
                                else:
                                    ptype = ContactPhone.PhoneType.OTHER
                                
                                # Парсим значение: может быть многострочным с комментарием
                                # Формат: "номер\nкомментарий" или "номер\nвремя - комментарий"
                                val_lines = [line.strip() for line in str(val).split("\n") if line.strip()]
                                if val_lines:
                                    # Первая строка - номер телефона
                                    phone_number = val_lines[0]
                                    # Остальные строки - комментарий (регион/город)
                                    phone_comment_parts = []
                                    for line in val_lines[1:]:
                                        # Убираем временные метки типа "22:05 - " или "20:05 - "
                                        line_clean = line
                                        # Паттерн: "время - текст" -> "текст"
                                        time_pattern = r'^\d{1,2}:\d{2}\s*-\s*'
                                        line_clean = re.sub(time_pattern, '', line_clean, flags=re.IGNORECASE)
                                        if line_clean.strip():
                                            phone_comment_parts.append(line_clean.strip())
                                    
                                    phone_comment = " | ".join(phone_comment_parts) if phone_comment_parts else ""
                                    
                                    # Если комментарий пустой, используем enum_code как fallback
                                    if not phone_comment and enum_code:
                                        phone_comment = str(enum_code)
                                    
                                    # Разбиваем номер на несколько, если есть запятые/точки с запятой
                                    for pv in _split_multi(phone_number):
                                        if pv:  # Проверяем, что номер не пустой
                                            phones.append((ptype, pv, phone_comment))
                                    
                                    if debug_count_for_extraction < 3:
                                        logger.debug(f"      -> Added phone: {phone_number} (type={ptype}, comment='{phone_comment}')")
                                else:
                                    # Fallback: если нет строк, используем старое поведение
                                    for pv in _split_multi(val):
                                        if pv:
                                            comment = str(enum_code or "")
                                            phones.append((ptype, pv, comment))
                                            if debug_count_for_extraction < 3:
                                                logger.debug(f"      -> Added phone (fallback): {pv} (type={ptype}, comment='{comment}')")
                            elif is_email:
                                # Определяем тип email:
                                # 1. По enum_code (WORK/PRIV/...)
                                # 2. По названию поля (если содержит "раб" - WORK, "личн" - PERSONAL)
                                t = str(enum_code or "").upper()
                                field_name_lower = field_name.lower()
                                
                                if t in ("WORK",) or "раб" in field_name_lower:
                                    etype = ContactEmail.EmailType.WORK
                                elif t in ("PRIV", "PERSONAL", "HOME") or "личн" in field_name_lower or "персон" in field_name_lower:
                                    etype = ContactEmail.EmailType.PERSONAL
                                else:
                                    etype = ContactEmail.EmailType.OTHER
                                
                                # Email обычно в одной строке, но может быть несколько через запятую
                                for ev in _split_multi(val):
                                    if ev and "@" in ev:  # Проверяем, что это похоже на email
                                        emails.append((etype, ev))
                                        if debug_count_for_extraction < 3:
                                            logger.debug(f"      -> Added email: {ev} (type={etype})")
                            elif is_position:
                                if not position:
                                    position = val
                                    if debug_count_for_extraction < 3:
                                        logger.debug(f"      -> Set position: {val}")
                            elif is_note:
                                # ВАЖНО: примечание из custom_fields имеет ПРИОРИТЕТ над заметками
                                # Если уже есть note_text из заметок - проверяем, не служебная ли это заметка
                                is_current_note_service = (
                                    not note_text or 
                                    "call_" in str(note_text).lower() or 
                                    note_text.lower() in ["call_out", "call_in", "call", "amomail", "sms", "task"] or
                                    len(str(note_text).strip()) < 10  # Очень короткие значения тоже подозрительны
                                )
                                
                                if is_current_note_service or not note_text:
                                    # Заменяем служебные заметки на реальное примечание из custom_fields
                                    note_text = val[:255]
                                    if debug_count_for_extraction < 3:
                                        logger.debug(f"      -> Found note_text in custom_field (field_name='{field_name}', field_code='{field_code}'): {note_text[:100]}")
                                        if is_current_note_service:
                                            logger.debug(f"      -> Replaced service note '{note_text[:50]}' with real note from custom_field")
                                else:
                                    # Если уже есть нормальное примечание, добавляем через точку с запятой
                                    combined = f"{note_text}; {val[:100]}"
                                    note_text = combined[:255]
                                    if debug_count_for_extraction < 3:
                                        logger.debug(f"      -> Appended note_text from custom_field: {val[:100]}")
                            elif is_cold_call_date:
                                # Холодный звонок: val может быть timestamp (Unix timestamp) или числом
                                # Сохраняем для последующей обработки (берем первое значение, если их несколько)
                                if cold_call_timestamp is None:
                                    try:
                                        # Если val - это строка, пытаемся преобразовать в число
                                        if isinstance(val, str):
                                            cold_call_timestamp = int(float(val))
                                        else:
                                            cold_call_timestamp = int(float(val))
                                        # Будем использовать это значение при создании/обновлении контакта
                                        if debug_count_for_extraction < 3:
                                            logger.debug(f"      -> Found cold call date: {cold_call_timestamp} (from field_id={field_id})")
                                    except (ValueError, TypeError):
                                        if debug_count_for_extraction < 3:
                                            logger.debug(f"      -> Invalid cold call timestamp: {val}")
                                        cold_call_timestamp = None
                            elif is_birthday:
                                # День рождения: val может быть timestamp (Unix timestamp) или числом
                                # Сохраняем для последующей обработки (берем первое значение, если их несколько)
                                if birthday_timestamp is None:
                                    try:
                                        # Если val - это строка, пытаемся преобразовать в число
                                        if isinstance(val, str):
                                            birthday_timestamp = int(float(val))
                                        else:
                                            birthday_timestamp = int(float(val))
                                        # Сохраняем в raw_fields (пока нет поля в модели)
                                        if debug_count_for_extraction < 3:
                                            logger.debug(f"      -> Found birthday: {birthday_timestamp} (from field_id={field_id})")
                                    except (ValueError, TypeError):
                                        if debug_count_for_extraction < 3:
                                            logger.debug(f"      -> Invalid birthday timestamp: {val}")
                                        birthday_timestamp = None
                    
                        # Убираем дубликаты
                        # Дедуп
                        dedup_phones: list[tuple[str, str, str]] = []
                        seen_p = set()
                        for pt, pv, pc in phones:
                        pv2 = str(pv or "").strip()
                        if not pv2:
                            continue
                        if pv2 in seen_p:
                            continue
                        seen_p.add(pv2)
                        dedup_phones.append((pt, pv2, str(pc or "")))
                        phones = dedup_phones

                        # Если есть одно общее примечание, а номеров несколько — пишем его в comment первого номера
                        if note_text and phones:
                        pt0, pv0, pc0 = phones[0]
                        if not (pc0 or "").strip():
                            phones[0] = (pt0, pv0, note_text[:255])
                            if debug_count_for_extraction < 3:
                                logger.debug(f"  -> Applied note_text to first phone: {note_text[:100]}")
                        elif debug_count_for_extraction < 3 and not note_text:
                        logger.debug(f"  -> ⚠️ No note_text found for contact {amo_contact_id} (checked direct fields and custom_fields)")

                        dedup_emails: list[tuple[str, str]] = []
                        seen_e = set()
                        for et, ev in emails:
                        ev2 = str(ev or "").strip().lower()
                        if not ev2:
                            continue
                        if ev2 in seen_e:
                            continue
                        seen_e.add(ev2)
                        dedup_emails.append((et, ev2))
                        emails = dedup_emails
                    
                        # ОТЛАДКА: сохраняем сырые данные для анализа
                        # Собираем информацию о том, где искали примечания
                        note_search_info = []
                        if isinstance(ac, dict):
                        # Проверяем прямые поля
                        for note_key in ["note", "notes", "comment", "comments", "remark", "remarks"]:
                            if note_key in ac:
                                note_search_info.append(f"direct:{note_key}={bool(ac.get(note_key))}")
                        # Проверяем _embedded
                        if "_embedded" in ac:
                            embedded = ac.get("_embedded") or {}
                            if isinstance(embedded, dict) and "notes" in embedded:
                                notes_list = embedded.get("notes") or []
                                notes_count = len(notes_list) if isinstance(notes_list, list) else 0
                                if notes_count > 0:
                                    note_search_info.append(f"_embedded.notes={notes_count}")
                                    # Показываем типы заметок для отладки
                                    note_types = []
                                    for note_item in notes_list[:3]:  # первые 3
                                        if isinstance(note_item, dict):
                                            note_type = str(note_item.get("note_type") or "").strip()
                                            if note_type:
                                                note_types.append(note_type)
                                    if note_types:
                                        note_search_info.append(f"note_types:{','.join(note_types)}")
                                    # Показываем, есть ли текст в заметках
                                    has_text = False
                                    for note_item in notes_list[:3]:
                                        if isinstance(note_item, dict):
                                            if note_item.get("text") or note_item.get("params", {}).get("text"):
                                                has_text = True
                                                break
                                    if has_text:
                                        note_search_info.append("notes_has_text=True")
                                    else:
                                        note_search_info.append("notes_has_text=False")
                        # Проверяем custom_fields на наличие полей с примечаниями
                        note_fields_in_custom = []
                        all_custom_field_names = []  # Для отладки - показываем ВСЕ поля
                        all_custom_fields_with_values = []  # Для отладки - показываем ВСЕ поля с их значениями
                        for cf in custom_fields:
                            if isinstance(cf, dict):
                                field_id = cf.get("field_id")  # ВАЖНО: field_id может быть числом (366537)
                                field_name = str(cf.get("field_name") or "").strip()
                                field_code = str(cf.get("field_code") or "").strip()
                                field_name_lower = field_name.lower()
                                field_code_upper = field_code.upper()
                                
                                # Сохраняем все поля для отладки (включая field_id)
                                all_custom_field_names.append(f"id={field_id} name={field_name} code={field_code}")
                                
                                # Сохраняем все поля с их значениями для отладки
                                values = cf.get("values") or []
                                if values and isinstance(values, list) and len(values) > 0:
                                    first_val = values[0]
                                    if isinstance(first_val, dict):
                                        val_text = str(first_val.get("value", ""))[:100]
                                    else:
                                        val_text = str(first_val)[:100]
                                    if val_text:
                                        all_custom_fields_with_values.append(f"id={field_id} name={field_name} code={field_code} value={val_text[:50]}")
                                
                                # Проверяем на примечания (расширенный список ключевых слов)
                                # Также проверяем field_id - возможно, примечание имеет конкретный ID (например, 366537)
                                is_note_field = (
                                    any(k in field_name_lower for k in ["примеч", "комментар", "коммент", "заметк", "note", "comment", "remark"]) or
                                    any(k in field_code_upper for k in ["NOTE", "COMMENT", "REMARK", "NOTE_TEXT", "COMMENT_TEXT"]) or
                                    (field_id and str(field_id) in ["366537"])  # Известные ID полей примечаний
                                )
                                
                                if is_note_field:
                                    note_fields_in_custom.append(f"id={field_id} name={field_name}({field_code})")
                                    # Логируем значение этого поля
                                    if values and isinstance(values, list) and len(values) > 0:
                                        first_val = values[0]
                                        if isinstance(first_val, dict):
                                            val_text = str(first_val.get("value", ""))[:100]
                                        else:
                                            val_text = str(first_val)[:100]
                                        if val_text:
                                            note_text = val_text[:255]  # Устанавливаем примечание!
                                            note_search_info.append(f"found_note_value:{val_text[:50]}")
                                            if debug_count_for_extraction < 3:
                                                logger.debug(f"  -> ✅ Found note_text in custom_field id={field_id} name={field_name}: {note_text[:100]}")
                        
                        # Добавляем информацию о всех полях для отладки
                        if all_custom_field_names:
                            note_search_info.append(f"all_fields:{','.join(all_custom_field_names)}")
                        if note_fields_in_custom:
                            note_search_info.append(f"note_fields:{','.join(note_fields_in_custom)}")
                        elif debug_count_for_extraction < 3:
                            # Если не нашли поля с примечаниями, логируем все поля
                            logger.debug(f"  -> ⚠️ No note fields found in custom_fields. All fields: {all_custom_field_names}")
                    
                        # Обрабатываем данные о холодном звонке из amoCRM (ДО использования в contact_debug)
                        cold_marked_at_dt = None
                        if cold_call_timestamp:
                        try:
                            UTC = getattr(timezone, "UTC", dt_timezone.utc)
                            cold_marked_at_dt = timezone.datetime.fromtimestamp(cold_call_timestamp, tz=UTC)
                        except Exception:
                            cold_marked_at_dt = None
                    
                        debug_data = {
                        "source": "amo_api",
                        "amo_contact_id": amo_contact_id,
                        "first_name": first_name,
                        "last_name": last_name,
                        "extracted_phones": phones,
                        "extracted_emails": emails,
                        "extracted_position": position,
                        "extracted_note_text": note_text,  # Добавляем note_text для отладки
                        "extracted_cold_call_timestamp": cold_call_timestamp,  # Timestamp холодного звонка
                        "extracted_birthday_timestamp": birthday_timestamp,  # Timestamp дня рождения
                        "note_search_info": note_search_info,  # Где искали примечания
                        "custom_fields_count": len(custom_fields),
                        "custom_fields_sample": custom_fields if dry_run else (custom_fields[:3] if custom_fields else []),  # В dry-run показываем все поля
                        "has_phone_field": bool(ac.get("phone")),
                        "has_email_field": bool(ac.get("email")),
                        }
                    
                        # ПОЛНЫЙ АНАЛИЗ КОНТАКТА для dry-run
                        # Используем новую функцию для извлечения ВСЕХ полей
                        debug_count = getattr(res, '_debug_contacts_logged', 0)
                        if res.contacts_preview is None:
                        res.contacts_preview = []
                    
                        # В dry-run показываем ВСЕ контакты (до 1000), чтобы видеть все проблемы
                        preview_limit = 1000 if dry_run else 10
                        logger.info(f"migrate_filtered: обработка контакта {amo_contact_id}: debug_count={debug_count}, preview_limit={preview_limit}, local_company={'найдена' if local_company else 'не найдена'}")
                        if debug_count < preview_limit:
                        # Полный анализ контакта
                        full_analysis = _analyze_contact_completely(ac)
                        
                        # Формируем понятный отчет для dry-run
                        contact_debug = {
                            "status": "UPDATED" if existing_contact else "CREATED",
                            "amo_contact_id": amo_contact_id,
                            "company_name": local_company.name if local_company else None,
                            "company_id": local_company.id if local_company else None,
                            
                            # Стандартные поля
                            "standard_fields": full_analysis.get("standard_fields", {}),
                            "first_name": first_name,
                            "last_name": last_name,
                            
                            # Извлеченные данные (что будет импортировано)
                            "extracted_phones": [
                                {
                                    "value": p[1],
                                    "type": str(p[0]),
                                    "comment": p[2],
                                }
                                for p in phones
                            ],
                            "extracted_emails": [
                                {
                                    "value": e[1],
                                    "type": str(e[0]),
                                }
                                for e in emails
                            ],
                            "extracted_position": position,
                            "extracted_note_text": note_text,
                            "extracted_cold_call": cold_marked_at_dt.isoformat() if cold_marked_at_dt else None,
                            "extracted_birthday": birthday_timestamp,  # Timestamp дня рождения (если есть)
                            
                            # ВСЕ кастомные поля (полная информация)
                            "all_custom_fields": [
                                {
                                    "field_id": cf.get("field_id"),
                                    "field_name": cf.get("field_name"),
                                    "field_code": cf.get("field_code"),
                                    "field_type": cf.get("field_type"),
                                    "values_count": cf.get("values_count", 0),
                                    "values": [
                                        {
                                            "value": str(v.get("value", "")),
                                            "enum_code": v.get("enum_code"),
                                            "enum_id": v.get("enum_id"),
                                            "enum": v.get("enum"),
                                        }
                                        for v in cf.get("values", [])
                                    ],
                                    "is_used": (
                                        cf.get("field_code", "").upper() in ["PHONE", "EMAIL", "POSITION"] or
                                        any(k in (cf.get("field_name") or "").lower() for k in ["телефон", "почта", "email", "должность", "позиция", "примеч", "комментар", "холодный"])
                                    ),
                                }
                                for cf in full_analysis.get("custom_fields", [])
                            ],
                            "custom_fields_count": len(full_analysis.get("custom_fields", [])),
                            
                            # Вложенные данные (_embedded)
                            "embedded_tags": full_analysis.get("embedded_data", {}).get("tags", []),
                            "embedded_companies": full_analysis.get("embedded_data", {}).get("companies", []),
                            "embedded_leads": full_analysis.get("embedded_data", {}).get("leads", []),
                            "embedded_customers": full_analysis.get("embedded_data", {}).get("customers", []),
                            "embedded_notes": full_analysis.get("embedded_data", {}).get("notes", []),
                            "embedded_notes_count": len(full_analysis.get("embedded_data", {}).get("notes", [])),
                            
                            # Метаинформация
                            "all_contact_keys": full_analysis.get("all_keys", []),
                            "note_search_info": note_search_info,
                            
                            # Полная структура для первых 3 контактов (для глубокой отладки)
                            "full_structure": None,
                        }
                        
                        # Сохраняем полную структуру для первых 3 контактов
                        preview_count = len(res.contacts_preview) if res.contacts_preview else 0
                        if preview_count < 3 and isinstance(ac, dict):
                            import json
                            try:
                                # Сохраняем полную структуру (ограничиваем размер для UI)
                                contact_debug["full_structure"] = json.dumps(ac, ensure_ascii=False, indent=2)[:5000]
                            except Exception as e:
                                contact_debug["full_structure"] = f"JSON error: {e}\n{str(ac)[:2000]}"
                        
                        res.contacts_preview.append(contact_debug)
                        res._debug_contacts_logged = debug_count + 1
                        logger.info(f"migrate_filtered: ✅ контакт {amo_contact_id} добавлен в preview (всего в preview: {len(res.contacts_preview)})")
                        
                        # ОТЛАДКА: логируем, что добавили в preview
                        if preview_count < 3:
                            logger.debug(f"Added contact {amo_contact_id} to preview (count: {debug_count + 1}):")
                            logger.debug(f"  - phones_found: {phones}")
                            logger.debug(f"  - emails_found: {emails}")
                            logger.debug(f"  - position_found: {position}")
                            logger.debug(f"  - note_text_found: {note_text}")
                            logger.debug(f"  - custom_fields_count: {len(full_analysis.get('custom_fields', []))}")
                            logger.debug(f"  - all_custom_fields: {len(contact_debug.get('all_custom_fields', []))}")
                        else:
                        logger.info(f"migrate_filtered: ⚠️ контакт {amo_contact_id} НЕ добавлен в preview (превышен лимит: {debug_count} >= {preview_limit})")
                    
                        # Также логируем в консоль для первых контактов
                        if contacts_processed <= 3:
                        logger.debug(f"Contact {amo_contact_id}:")
                        logger.debug(f"  - first_name: {first_name}")
                        logger.debug(f"  - last_name: {last_name}")
                        logger.debug(f"  - phones found: {phones}")
                        logger.debug(f"  - emails found: {emails}")
                        logger.debug(f"  - position found: {position}")
                        logger.debug(f"  - note_text found: {note_text}")
                        logger.debug(f"  - custom_fields_values count: {len(custom_fields)}")
                        if custom_fields:
                            logger.debug(f"  - custom_fields sample (first 3):")
                            for idx, cf in enumerate(custom_fields[:3]):
                                logger.debug(f"    [{idx}] field_id={cf.get('field_id')}, code={cf.get('code')}, name={cf.get('name')}, type={cf.get('type')}, values={cf.get('values')}")
                        else:
                            logger.debug(f"  - ⚠️ custom_fields_values пуст или отсутствует")
                        logger.debug(f"  - raw contact top-level keys: {list(ac.keys())[:15] if isinstance(ac, dict) else 'not_dict'}")
                        logger.debug(f"  - has phone field: {bool(ac.get('phone')) if isinstance(ac, dict) else False}")
                        logger.debug(f"  - has email field: {bool(ac.get('email')) if isinstance(ac, dict) else False}")
                    
                        except Exception as e:
                        contacts_errors += 1
                        amo_contact_id_for_error = int(ac.get("id") or 0) if isinstance(ac, dict) else 0
                        logger.error(f"migrate_filtered: ❌ ОШИБКА при обработке контакта {ac_idx + 1}/{len(full_contacts)} (amo_id: {amo_contact_id_for_error}): {e}", exc_info=True)
                        # Добавляем информацию об ошибке в preview
                        if res.contacts_preview is None:
                            res.contacts_preview = []
                        if len(res.contacts_preview) < 100:  # Ограничиваем количество ошибок в preview
                            res.contacts_preview.append({
                                "status": "ERROR",
                                "amo_contact_id": amo_contact_id_for_error,
                                "error": str(e),
                                "message": f"Ошибка при обработке контакта: {e}",
                            })
                        continue
                    
                    # Обновляем или создаём контакт
                    # DRY-RUN: собираем понятный diff "что будет обновлено" по контакту (поля + что добавится в телефоны/почты)
                    if dry_run:
                        if res.contacts_updates_preview is None:
                            res.contacts_updates_preview = []

                        planned_field_changes: dict[str, dict[str, str]] = {}
                        planned_phones_add: list[dict[str, str]] = []
                        planned_emails_add: list[dict[str, str]] = []

                        # Снимок текущих данных контакта (если он уже есть в CRM)
                        old_position = ""
                        old_is_cold_call = False
                        old_phones: list[dict[str, str]] = []
                        old_emails: list[str] = []
                        if existing_contact:
                            old_position = str(existing_contact.position or "")
                            old_is_cold_call = bool(getattr(existing_contact, "is_cold_call", False))
                            try:
                                old_phones = [
                                    {"value": p.value, "type": str(p.type), "comment": str(p.comment or "")}
                                    for p in existing_contact.phones.all()
                                ]
                            except Exception:
                                old_phones = []
                            try:
                                old_emails = [str(e.value or "") for e in existing_contact.emails.all()]
                            except Exception:
                                old_emails = []

                        # Позиция: показываем только если "мягкий режим" позволил бы обновить
                        if existing_contact:
                            try:
                                crf_preview = dict(existing_contact.raw_fields or {})
                            except Exception:
                                crf_preview = {}
                            cprev_preview = crf_preview.get("amo_values") or {}
                            if not isinstance(cprev_preview, dict):
                                cprev_preview = {}

                            def _c_can_update_preview(field: str) -> bool:
                                cur = getattr(existing_contact, field)
                                if cur in ("", None):
                                    return True
                                if field in cprev_preview and cprev_preview.get(field) == cur:
                                    return True
                                return False

                            if position and _c_can_update_preview("position") and (existing_contact.position or "") != position[:255]:
                                planned_field_changes["position"] = {"old": old_position, "new": position[:255]}
                        else:
                            if position:
                                planned_field_changes["position"] = {"old": "", "new": position[:255]}

                        # Холодный звонок
                        if cold_marked_at_dt:
                            planned_field_changes["cold_call"] = {
                                "old": "Да" if old_is_cold_call else "Нет",
                                "new": "Да",
                            }

                        # Телефоны/почты: покажем только добавления (мы не удаляем/не затираем)
                        old_phone_values = set([p.get("value") for p in (old_phones or []) if p.get("value")])
                        for pt, pv, pc in phones:
                            pv_db = str(pv).strip()[:50]
                            if pv_db and pv_db not in old_phone_values:
                                planned_phones_add.append(
                                    {
                                        "value": pv_db,
                                        "type": str(pt),
                                        "comment": str(pc or "")[:255],
                                    }
                                )

                        old_email_values = set([str(e or "").strip().lower() for e in (old_emails or []) if e])
                        for et, ev in emails:
                            ev_db = str(ev).strip()[:254].lower()
                            if ev_db and ev_db not in old_email_values:
                                planned_emails_add.append({"value": ev_db, "type": str(et)})

                        # Комментарий к первому телефону, если note_text
                        if note_text and phones:
                            first_phone_val = str(phones[0][1]).strip()[:50]
                            first_phone_comment_from_phones = str(phones[0][2] or "").strip()
                            if first_phone_val:
                                existing_first = None
                                for p in (old_phones or []):
                                    if p.get("value") == first_phone_val:
                                        existing_first = p
                                        break
                                
                                # Если телефон существует и у него пустой комментарий, показываем обновление
                                if existing_first and not (existing_first.get("comment") or "").strip():
                                    planned_field_changes["first_phone_comment"] = {"old": "", "new": note_text[:255]}
                                # Если телефон новый и у него есть комментарий из note_text, он уже будет в planned_phones_add
                                # Но для ясности также показываем отдельно, если note_text не пустой
                                elif not existing_first and first_phone_comment_from_phones:
                                    # Комментарий уже будет в planned_phones_add, но для наглядности можно добавить отдельное поле
                                    # Проверяем, что комментарий действительно из note_text (не из enum_code)
                                    if first_phone_comment_from_phones == note_text[:255]:
                                        # Это уже будет видно в planned_phones_add, но можно добавить отдельное поле для ясности
                                        pass

                        # Используем полный анализ для формирования информации о кастомных полях
                        full_analysis = _analyze_contact_completely(ac)
                        all_custom_fields_info = []
                        for cf in full_analysis.get("custom_fields", []):
                            field_id = cf.get("field_id")
                            field_code = cf.get("field_code")
                            field_name = cf.get("field_name")
                            field_type = cf.get("field_type")
                            
                            # Собираем все значения в читаемом виде
                            field_values = []
                            for val_info in cf.get("values", []):
                                val_str = str(val_info.get("value", ""))
                                enum_code = val_info.get("enum_code") or val_info.get("enum")
                                if val_str:
                                    if enum_code:
                                        field_values.append(f"{val_str} ({enum_code})")
                                    else:
                                        field_values.append(val_str)
                            
                            # Определяем, было ли поле использовано (извлечено)
                            is_used = False
                            usage_info = []
                            field_code_upper = (field_code or "").upper()
                            field_name_lower = (field_name or "").lower()
                            
                            if field_code_upper == "PHONE" or "телефон" in field_name_lower:
                                is_used = True
                                usage_info.append("Телефон")
                            elif field_code_upper == "EMAIL" or "email" in field_name_lower or "почта" in field_name_lower:
                                is_used = True
                                usage_info.append("Email")
                            elif field_code_upper == "POSITION" or "должность" in field_name_lower or "позиция" in field_name_lower:
                                is_used = True
                                usage_info.append("Должность")
                            elif any(k in field_name_lower for k in ["примеч", "комментар", "коммент", "заметк"]):
                                is_used = True
                                usage_info.append("Примечание")
                            elif field_type == "date" and "холодный" in field_name_lower and "звонок" in field_name_lower:
                                is_used = True
                                usage_info.append("Холодный звонок")
                            
                            all_custom_fields_info.append({
                                "field_id": field_id,
                                "code": field_code,
                                "name": field_name,
                                "type": field_type,
                                "values": field_values,
                                "values_count": cf.get("values_count", 0),
                                "is_used": is_used,
                                "usage_info": usage_info,
                            })
                        
                        if planned_field_changes or planned_phones_add or planned_emails_add or all_custom_fields_info:
                            res.contacts_updates_preview.append(
                                {
                                    "company_name": local_company.name if local_company else "",
                                    "company_id": local_company.id if local_company else None,
                                    "contact_name": f"{last_name} {first_name}".strip() or "(без имени)",
                                    "amo_contact_id": amo_contact_id,
                                    "is_new": existing_contact is None,
                                    "field_changes": planned_field_changes,
                                    "phones_add": planned_phones_add,
                                    "emails_add": planned_emails_add,
                                    "all_custom_fields": all_custom_fields_info,  # Все найденные кастомные поля
                                }
                            )

                    # Обрабатываем данные о холодном звонке из amoCRM
                    cold_marked_at_dt = None
                    if cold_call_timestamp:
                        try:
                            UTC = getattr(timezone, "UTC", dt_timezone.utc)
                            cold_marked_at_dt = timezone.datetime.fromtimestamp(cold_call_timestamp, tz=UTC)
                        except Exception:
                            cold_marked_at_dt = None
                    
                    # Определяем, кто отметил холодный звонок (используем ответственного или создателя компании)
                    cold_marked_by_user = None
                    if local_company:
                        cold_marked_by_user = local_company.responsible or local_company.created_by or actor
                    else:
                        cold_marked_by_user = actor
                    
                    if existing_contact:
                        # ОБНОВЛЯЕМ существующий контакт с мягким обновлением
                        contact = existing_contact
                        
                        # Мягкий апдейт: не затираем данные, измененные вручную
                        try:
                            crf = dict(contact.raw_fields or {})
                        except Exception:
                            crf = {}
                        cprev = crf.get("amo_values") or {}
                        if not isinstance(cprev, dict):
                            cprev = {}

                        def c_can_update(field: str) -> bool:
                            """
                            Проверяет, можно ли обновить поле.
                            Поле можно обновить, если:
                            1. Оно пустое
                            2. Оно было импортировано из AmoCRM (есть в cprev и значение совпадает)
                            """
                            cur = getattr(contact, field)
                            if cur in ("", None):
                                return True
                            if field in cprev and cprev.get(field) == cur:
                                return True
                            return False

                        # Обновляем ФИО только если можно
                        if first_name and c_can_update("first_name"):
                            contact.first_name = first_name[:120]
                        if last_name and c_can_update("last_name"):
                            contact.last_name = last_name[:120]
                        
                        # Обновляем должность только если можно
                        if position and c_can_update("position"):
                            contact.position = position[:255]
                        # Обновляем данные о холодном звонке из amoCRM
                        if cold_marked_at_dt:
                            contact.is_cold_call = True
                            contact.cold_marked_at = cold_marked_at_dt
                            contact.cold_marked_by = cold_marked_by_user
                            # cold_marked_call оставляем NULL, т.к. в amoCRM нет связи с CallRequest
                        # Обновляем raw_fields + снимок импортированных значений
                        crf.update(debug_data)
                        # Сохраняем день рождения в raw_fields (пока нет поля в модели)
                        if birthday_timestamp:
                            crf["birthday_timestamp"] = birthday_timestamp
                        # Сохраняем снимок импортированных значений для мягкого обновления
                        cprev.update({
                            "first_name": contact.first_name,
                            "last_name": contact.last_name,
                            "position": contact.position,
                        })
                        crf["amo_values"] = cprev
                        contact.raw_fields = crf
                        
                        if not dry_run:
                            contact.save()
                            res.contacts_created += 1  # Используем тот же счётчик для обновлённых
                            
                            # Телефоны: мягкий upsert (не удаляем вручную добавленные)
                            # Примечание добавляется в comment первого телефона
                            phones_added = 0
                            phones_updated = 0
                            for idx, (pt, pv, pc) in enumerate(phones):
                                pv_db = str(pv).strip()[:50]
                                if not pv_db:
                                    continue
                                
                                # Для первого телефона добавляем примечание в comment, если его нет
                                phone_comment = str(pc or "").strip()
                                if idx == 0 and note_text and not phone_comment:
                                    phone_comment = note_text[:255]
                                
                                obj = ContactPhone.objects.filter(contact=contact, value=pv_db).first()
                                if obj is None:
                                    # Создаем новый телефон
                                    ContactPhone.objects.create(
                                        contact=contact,
                                        type=pt,
                                        value=pv_db,
                                        comment=phone_comment[:255]
                                    )
                                    phones_added += 1
                                else:
                                    # Обновляем существующий телефон (мягко)
                                    upd = False
                                    # Обновляем comment только если он пустой или совпадает с импортированным
                                    if not obj.comment and phone_comment:
                                        obj.comment = phone_comment[:255]
                                        upd = True
                                    # Обновляем type только если comment пустой или совпадает
                                    if obj.type != pt and (not obj.comment or obj.comment == phone_comment[:255]):
                                        obj.type = pt
                                        upd = True
                                    if upd:
                                        obj.save(update_fields=["type", "comment"])
                                        phones_updated += 1
                            
                            # Email: мягкий upsert
                            emails_added = 0
                            for et, ev in emails:
                                ev_db = str(ev).strip()[:254]
                                if not ev_db:
                                    continue
                                if not ContactEmail.objects.filter(contact=contact, value__iexact=ev_db).exists():
                                    try:
                                        ContactEmail.objects.create(contact=contact, type=et, value=ev_db)
                                        emails_added += 1
                                    except Exception:
                                        pass
                            
                            # Логируем результат обновления
                            debug_count_after = getattr(res, '_debug_contacts_logged', 0)
                            if debug_count_after < 10:
                                logger.debug(f"  - Updated: phones={phones_added}, emails={emails_added}, position={bool(position)}")
                        else:
                            res.contacts_created += 1
                    else:
                        # СОЗДАЁМ новый контакт
                        # Сохраняем день рождения в raw_fields (пока нет поля в модели)
                        if birthday_timestamp:
                            debug_data["birthday_timestamp"] = birthday_timestamp
                        
                        contact = Contact(
                            company=local_company,
                            first_name=first_name[:120],
                            last_name=last_name[:120],
                            position=position[:255],
                            amocrm_contact_id=amo_contact_id,
                            raw_fields=debug_data,
                        )
                        # Устанавливаем данные о холодном звонке из amoCRM
                        if cold_marked_at_dt:
                            contact.is_cold_call = True
                            contact.cold_marked_at = cold_marked_at_dt
                            contact.cold_marked_by = cold_marked_by_user
                            # cold_marked_call оставляем NULL, т.к. в amoCRM нет связи с CallRequest
                        if not dry_run:
                            contact.save()
                            res.contacts_created += 1
                            # Добавляем телефоны и почты
                            phones_added = 0
                            for idx, (pt, pv, pc) in enumerate(phones):
                                pv_db = str(pv).strip()[:50]
                                if pv_db and not ContactPhone.objects.filter(contact=contact, value=pv_db).exists():
                                    # Если это первый телефон и есть примечание - добавляем в comment
                                    phone_comment = str(pc or "").strip()
                                    if idx == 0 and note_text and not phone_comment:
                                        phone_comment = note_text[:255]
                                    ContactPhone.objects.create(contact=contact, type=pt, value=pv_db, comment=phone_comment[:255])
                                    phones_added += 1
                            emails_added = 0
                            for et, ev in emails:
                                ev_db = str(ev).strip()[:254]
                                if ev_db and not ContactEmail.objects.filter(contact=contact, value__iexact=ev_db).exists():
                                    try:
                                        ContactEmail.objects.create(contact=contact, type=et, value=ev_db)
                                        emails_added += 1
                                    except Exception:
                                        pass
                            # Логируем результат сохранения
                            debug_count_after = getattr(res, '_debug_contacts_logged', 0)
                            if debug_count_after < 10:
                                logger.debug(f"  - Saved: phones={phones_added}, emails={emails_added}, position={bool(position)}")
                        else:
                            res.contacts_created += 1
            except Exception as e:
                # Если контакты недоступны — не валим всю миграцию
                logger.debug(f"ERROR importing contacts: {type(e).__name__}: {e}")
                import traceback
                logger.debug("Contact import error", exc_info=True)
                pass
            finally:
                logger.debug(f"===== CONTACT IMPORT FINISHED: created={res.contacts_created}, seen={res.contacts_seen}, processed={contacts_processed}, skipped={contacts_skipped} =====")
        else:
            logger.debug(f"Contact import SKIPPED: import_contacts={import_contacts}, dry_run={dry_run}, amo_ids={bool(amo_ids)}")
            # В dry-run все равно показываем информацию, что контакты не будут импортированы
            if dry_run and not import_contacts and amo_ids:
                if res.contacts_preview is None:
                    res.contacts_preview = []
                res.contacts_preview.append({
                    "status": "INFO",
                    "message": "⚠️ Импорт контактов выключен. Включите опцию 'Импортировать контакты' для импорта.",
                    "companies_count": len(amo_ids),
                })

        if dry_run:
            transaction.set_rollback(True)

    try:
        _run()
    except Exception as e:
        # Логируем ошибку, но не падаем - возвращаем частичный результат
        import traceback
        error_details = traceback.format_exc()
        logger.error(f"Migration failed: {type(e).__name__}: {e}")
        logger.error(f"Traceback:\n{error_details}")
        # Устанавливаем флаг ошибки в результате
        res.error = str(e)
        res.error_traceback = error_details
    return res


