from __future__ import annotations

import csv
import datetime as dt
import html
import re
from dataclasses import dataclass
from pathlib import Path

from django.db import transaction

from accounts.models import User
from companies.models import Company, CompanyNote, CompanySphere, CompanyStatus, Contact, ContactEmail, ContactPhone


def _clean_str(v) -> str:
    if v is None:
        return ""
    s = str(v).strip()
    if s.lower() in ("nan", "none"):
        return ""
    return s


def _get(row: dict, *keys: str) -> str:
    for k in keys:
        if k in row:
            v = _clean_str(row.get(k))
            if v:
                return v
    return ""


def _digits_only(s: str) -> str:
    return re.sub(r"\D+", "", s or "")


def _parse_int(v: str) -> int | None:
    try:
        vv = _digits_only(v)
        if not vv:
            return None
        return int(vv)
    except Exception:
        return None


def _parse_worktime(v: str) -> tuple[dt.time | None, dt.time | None]:
    # поддерживаем форматы: "09:00-18:00", "09:00–18:00", "с 9:00 до 18:00"
    s = (v or "").replace("–", "-").strip()
    if not s:
        return (None, None)
    m = re.search(r"(\d{1,2})[:.](\d{2})\s*-\s*(\d{1,2})[:.](\d{2})", s)
    if not m:
        return (None, None)
    try:
        h1, m1, h2, m2 = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
        if 0 <= h1 <= 23 and 0 <= h2 <= 23 and 0 <= m1 <= 59 and 0 <= m2 <= 59:
            return (dt.time(h1, m1), dt.time(h2, m2))
    except Exception:
        pass
    return (None, None)


def _norm_name(s: str) -> str:
    s = _clean_str(s).lower()
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _split_multi(s: str) -> list[str]:
    s = _clean_str(s)
    if not s:
        return []
    # Часто встречается ; или , как разделитель
    parts = re.split(r"[;,]+", s)
    return [p.strip() for p in parts if p.strip()]


def _sphere_key(s: str) -> str:
    s = _clean_str(s).strip().lower()
    s = s.replace("ё", "е")
    s = s.replace(",", " ")
    s = s.replace("-", " ")
    s = s.replace(".", " ")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


# Канонизация сфер (чтобы не плодить дубли при импорте из amoCRM)
_SPHERE_CANON: dict[str, str] = {
    # опечатки/варианты написания
    _sphere_key("Металургия"): "Металлургия",
    _sphere_key("Металлургия"): "Металлургия",
    _sphere_key("Администрации"): "Администрация",
    _sphere_key("Администрация"): "Администрация",
    _sphere_key("ЦЗН"): "Центры занятости",
    _sphere_key("Центры занятости"): "Центры занятости",
    _sphere_key("Медики"): "Медицина",
    _sphere_key("Медицина"): "Медицина",
    _sphere_key("Аэро"): "Авиация",
    _sphere_key("Авиация"): "Авиация",
    _sphere_key("Силовики"): "Силовые структуры",
    _sphere_key("Силовые структуры"): "Силовые структуры",
    _sphere_key("Сельск. хоз"): "Сельхоз отрасль",
    _sphere_key("Сельхоз отрасль"): "Сельхоз отрасль",
    _sphere_key("Отдых"): "Отдых, оздоровление",
    _sphere_key("Отдых, оздоровление"): "Отдых, оздоровление",
}

# Сферы, которые не должны импортироваться (мусор/другие признаки)
_SPHERE_DROP: set[str] = {
    _sphere_key("Новая CRM"),
    _sphere_key("Информация не найдена"),
    _sphere_key("Конкурент"),
    _sphere_key("Постоянный клиент"),
}


def _canon_sphere_name(s: str) -> str | None:
    raw = _clean_str(s)
    if not raw:
        return None
    key = _sphere_key(raw)
    if not key:
        return None
    if key in _SPHERE_DROP:
        return None
    return _SPHERE_CANON.get(key) or _norm_spaces(raw)


def _unescape(s: str) -> str:
    s = _clean_str(s)
    if not s:
        return ""
    try:
        return html.unescape(s)
    except Exception:
        return s


def _norm_spaces(s: str) -> str:
    s = _clean_str(s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _strip_branch_prefix(s: str) -> str:
    # "(ЕКБ) Фамилия Имя ..." -> "Фамилия Имя ..."
    s = _norm_spaces(s)
    s = re.sub(r"^\([^)]+\)\s*", "", s)
    return s.strip()


def _parse_person_name(s: str) -> tuple[str, str]:
    """
    Из строки вида "Фамилия Имя Отчество" берём (last_name, first_name).
    Если формат иной — возвращаем ("", "").
    """
    s = _strip_branch_prefix(s)
    if not s:
        return ("", "")
    parts = [p for p in s.split(" ") if p]
    if len(parts) >= 2:
        return (parts[0], parts[1])
    return ("", "")


def _bool_yes(s: str) -> bool:
    s = _clean_str(s).strip().lower()
    return s in ("да", "yes", "true", "1", "y", "истина")


def _pick_first(*vals: str) -> str:
    for v in vals:
        v2 = _clean_str(v)
        if v2:
            return v2
    return ""


@dataclass
class ImportResult:
    created_companies: int = 0
    updated_companies: int = 0
    company_rows: int = 0
    skipped_rows: int = 0
    preview_companies: list[dict] | None = None
    preview_updates: list[dict] | None = None


def import_amo_csv(
    *,
    csv_path: str | Path,
    encoding: str = "utf-8-sig",
    dry_run: bool = False,
    companies_only: bool = True,
    limit_companies: int = 20,
    actor: User | None = None,
    import_notes: bool = True,
    import_contacts: bool = True,
    set_responsible: bool = True,
) -> ImportResult:
    """
    Импорт из CSV в формате amo/base.csv.
    Для твоего запроса: companies_only=True и limit_companies=20.
    """
    csv_path = Path(csv_path)
    result = ImportResult(preview_companies=[], preview_updates=[])

    # Кеш пользователей
    # Важно: __str__ у нас = "Фамилия Имя", а get_full_name() у Django = "Имя Фамилия".
    # Плюс в CSV часто есть префиксы вида "(ЕКБ)" и отчества — делаем устойчивое сопоставление.
    user_by_name: dict[str, User] = {}
    for u in User.objects.all():
        candidates = [
            _norm_spaces(u.get_full_name()),
            _norm_spaces(str(u)),
            _norm_spaces(f"{u.last_name} {u.first_name}"),
            _norm_spaces(f"{u.first_name} {u.last_name}"),
        ]
        for c in candidates:
            if c:
                user_by_name.setdefault(c, u)
    user_by_username: dict[str, User] = {u.username.strip(): u for u in User.objects.all()}

    # Кеш компаний для дедупа
    company_by_inn: dict[str, Company] = {c.inn: c for c in Company.objects.exclude(inn="")}
    company_by_name_addr: dict[tuple[str, str], Company] = {}

    @transaction.atomic
    def _run():
        with csv_path.open("r", encoding=encoding, newline="") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames:
                raise ValueError("CSV has no header row")

            for row in reader:
                row_id = _get(row, "ID")
                if not row_id:
                    result.skipped_rows += 1
                    continue

                t = _get(row, "Тип").lower()
                is_company_row = "компан" in t
                if companies_only and not is_company_row:
                    continue

                if is_company_row:
                    result.company_rows += 1

                    amo_id = _get(row, "ID")

                    name = _unescape(_get(row, "Название компании", "Компания", "Наименование"))
                    legal_name = _unescape(_get(row, "Юридическое название компании", "Юридическое название компании (компания)"))
                    inn = _digits_only(_get(row, "ИНН", "ИНН (компания)"))
                    kpp = _digits_only(_get(row, "КПП", "КПП (компания)"))
                    region = _unescape(_get(row, "Область"))
                    address = _unescape(_get(row, "Адрес", "Адрес (компания)"))
                    if region and address and region.lower() not in address.lower():
                        address = f"{region}, {address}"

                    website = _unescape(_get(row, "Web", "Web (компания)"))

                    # Статус: в файле встречаются 2 колонки ("Статус" и "Статус из Скайнет") — берём более "живую".
                    status_name = _unescape(_get(row, "Статус", "Статус из Скайнет", "Статус из Скайнет (компания)"))
                    spheres_raw = _get(row, "Сферы деятельности", "Сферы деятельности (компания)")

                    # Доп. поля карточки
                    employees_count_raw = _unescape(_get(row, "Численность сотрудников", "Численность сотрудников (Скайнет)", "Сотрудников"))
                    worktime_raw = _unescape(_get(row, "Рабочее время", "Рабочее время (Скайнет)", "Часы работы"))
                    timezone_raw = _unescape(_get(row, "Часовой пояс", "Часовой пояс (Скайнет)", "Таймзона", "Timezone"))
                    employees_count = _parse_int(employees_count_raw)
                    workday_start, workday_end = _parse_worktime(worktime_raw)

                    # Телефоны/почта компании (основные)
                    phone_work = _unescape(_get(row, "Рабочий телефон"))
                    phone_work_direct = _unescape(_get(row, "Рабочий прямой телефон"))
                    phone_mobile = _unescape(_get(row, "Мобильный телефон"))
                    phone_other = _unescape(_get(row, "Другой телефон"))
                    phone = _pick_first(phone_work, phone_work_direct, phone_mobile, phone_other)

                    email_work = _unescape(_get(row, "Рабочий email"))
                    email_personal = _unescape(_get(row, "Личный email"))
                    email_other = _unescape(_get(row, "Другой email"))
                    email = _pick_first(email_work, email_personal, email_other)

                    # Контакт (из CSV это обычно руководитель организации)
                    contact_person_raw = _unescape(_get(row, "Руководитель"))
                    contact_position = _unescape(_get(row, "Должность"))

                    # Вид деятельности: в CSV встречается очень длинное описание — в поле кладём коротко,
                    # полную версию (если длиннее) сохраняем в заметку/сырые поля.
                    activity_raw = _unescape(_get(row, "Вид деятельности (Скайнет)"))
                    activity_short = (activity_raw or "")[:255]

                    is_cold_flag = _bool_yes(_get(row, "Холодный звонок"))

                    responsible_raw = _unescape(_get(row, "Ответственный"))
                    responsible_key = _strip_branch_prefix(responsible_raw)
                    responsible = None
                    if set_responsible and responsible_key:
                        responsible = (
                            user_by_name.get(_norm_spaces(responsible_key))
                            or user_by_name.get(_norm_spaces(responsible_raw))
                            or user_by_username.get(responsible_key)
                            or user_by_username.get(responsible_raw)
                        )
                        if responsible is None:
                            # fallback: "Фамилия Имя Отчество" -> ищем по "Фамилия Имя"
                            ln, fn = _parse_person_name(responsible_key)
                            if ln and fn:
                                responsible = (
                                    User.objects.filter(last_name__iexact=ln, first_name__iexact=fn, is_active=True).first()
                                )

                    company = None
                    if inn:
                        company = company_by_inn.get(inn)

                    if company is None:
                        key = (_norm_name(name), _norm_name(address))
                        company = company_by_name_addr.get(key)
                        if company is None and key != ("", ""):
                            company = Company.objects.filter(name__iexact=name, address__iexact=address).first()

                    created = False
                    if company is None:
                        # Обрезаем значения до max_length перед созданием
                        company = Company(
                            name=(name or "(без названия)")[:255],
                            legal_name=legal_name[:255] if legal_name else "",
                            inn=inn[:20] if inn else "",
                            kpp=kpp[:20] if kpp else "",
                            address=address[:500] if address else "",
                            website=website[:255] if website else "",
                            responsible=responsible,
                            phone=phone[:50] if phone else "",
                            email=email[:254] if email else "",
                            contact_name=contact_person_raw[:255] if contact_person_raw else "",
                            contact_position=contact_position[:255] if contact_position else "",
                            activity_kind=activity_short[:255] if activity_short else "",
                            employees_count=employees_count,
                            workday_start=workday_start,
                            workday_end=workday_end,
                            work_timezone=(timezone_raw or "")[:64],
                            amocrm_company_id=int(amo_id) if str(amo_id).isdigit() else None,
                            raw_fields={"source": "amo_import", "amo_row": row},
                        )
                        result.created_companies += 1
                        created = True
                    else:
                        changed = False
                        # Мягкий режим "обновить, но не перезаписать": если поле уже меняли руками,
                        # не трогаем. Сравниваем с последним импортированным значением из raw_fields.
                        try:
                            rf = dict(company.raw_fields or {})
                        except Exception:
                            rf = {}
                        prev = rf.get("amo_values") or {}
                        if not isinstance(prev, dict):
                            prev = {}

                        def can_update(field: str) -> bool:
                            cur = getattr(company, field)
                            if cur in ("", None):
                                return True
                            # если ранее импортировали и текущее значение равно "как было импортировано",
                            # значит пользователь не менял — можно обновлять.
                            if field in prev and prev.get(field) == cur:
                                return True
                            return False
                        # Обрезаем значения до max_length перед установкой
                        field_max_lengths = {
                            "name": 255,
                            "legal_name": 255,
                            "inn": 20,
                            "kpp": 20,
                            "address": 500,
                            "website": 255,
                            "phone": 50,
                            "email": 254,
                            "contact_name": 255,
                            "contact_position": 255,
                            "activity_kind": 255,
                        }
                        for field, value in (
                            ("name", name),
                            ("legal_name", legal_name),
                            ("inn", inn),
                            ("kpp", kpp),
                            ("address", address),
                            ("website", website),
                            ("phone", phone),
                            ("email", email),
                            ("contact_name", contact_person_raw),
                            ("contact_position", contact_position),
                            ("activity_kind", activity_short),
                        ):
                            if value and can_update(field):
                                # Обрезаем значение до max_length
                                max_len = field_max_lengths.get(field, 255)
                                value_truncated = str(value).strip()[:max_len]
                                if getattr(company, field) != value_truncated:
                                    setattr(company, field, value_truncated)
                                    changed = True

                        # новые поля (работаем только если можно обновлять)
                        if employees_count is not None and can_update("employees_count") and company.employees_count != employees_count:
                            company.employees_count = employees_count
                            changed = True
                        if workday_start and can_update("workday_start") and company.workday_start != workday_start:
                            company.workday_start = workday_start
                            changed = True
                        if workday_end and can_update("workday_end") and company.workday_end != workday_end:
                            company.workday_end = workday_end
                            changed = True
                        if timezone_raw and can_update("work_timezone"):
                            tzv = (timezone_raw or "").strip()[:64]
                            if company.work_timezone != tzv:
                                company.work_timezone = tzv
                                changed = True
                        if responsible and company.responsible_id != responsible.id and set_responsible:
                            company.responsible = responsible
                            changed = True
                        if str(amo_id).isdigit() and company.amocrm_company_id != int(amo_id):
                            company.amocrm_company_id = int(amo_id)
                            changed = True
                        if changed:
                            result.updated_companies += 1
                            # обновляем снимок импортированных значений
                            prev.update(
                                {
                                    "activity_kind": company.activity_kind,
                                    "employees_count": company.employees_count,
                                    "workday_start": company.workday_start,
                                    "workday_end": company.workday_end,
                                    "work_timezone": company.work_timezone,
                                }
                            )
                            rf["amo_values"] = prev
                            company.raw_fields = rf

                    # статус/сферы (создаём справочники при необходимости)
                    status_obj = None
                    if status_name:
                        status_obj, _ = CompanyStatus.objects.get_or_create(name=status_name)
                        if company.status_id != status_obj.id:
                            company.status = status_obj
                            if not created:
                                result.updated_companies += 0  # уже учли по changed; статус — доп.

                    if not dry_run:
                        company.save()

                        # spheres m2m — только после save
                        spheres: list[CompanySphere] = []
                        seen = set()
                        for sname in _split_multi(spheres_raw):
                            canon = _canon_sphere_name(sname)
                            if not canon:
                                continue
                            if canon.lower() in seen:
                                continue
                            seen.add(canon.lower())
                            obj, _ = CompanySphere.objects.get_or_create(name=canon)
                            spheres.append(obj)
                        if spheres:
                            company.spheres.set(spheres)

                        # Примечания/комментарии -> одна заметка (только при создании, чтобы не плодить дубли при повторном импорте)
                        if created and import_notes:
                            note_parts = []
                            for k in ("Примечание 1", "Примечание 2", "Примечание 3", "Примечание 4", "Примечание 5"):
                                v = _unescape(_get(row, k))
                                if v:
                                    note_parts.append(f"{k}: {v}")
                            last_comment = _unescape(_get(row, "Последний комментарий (Скайнет)"))
                            if last_comment:
                                note_parts.append(f"Последний комментарий (Скайнет): {last_comment}")
                            note2 = _unescape(_get(row, "Примечание"))
                            if note2:
                                note_parts.append(f"Примечание: {note2}")
                            if activity_raw and len(activity_raw) > 255:
                                note_parts.append(f"Вид деятельности (полный текст): {activity_raw}")

                            if note_parts:
                                CompanyNote.objects.create(
                                    company=company,
                                    author=actor or responsible,
                                    text="Импорт из amo:\n" + "\n\n".join(note_parts),
                                )

                        # Доп. контакты/телефоны/email: создаём один контакт на компанию (только при создании)
                        if created and import_contacts:
                            phones = []
                            if phone_work:
                                phones.append((ContactPhone.PhoneType.WORK, phone_work))
                            if phone_work_direct:
                                phones.append((ContactPhone.PhoneType.WORK_DIRECT, phone_work_direct))
                            if phone_mobile:
                                phones.append((ContactPhone.PhoneType.MOBILE, phone_mobile))
                            if phone_other:
                                phones.append((ContactPhone.PhoneType.OTHER, phone_other))
                            # "Список телефонов (Скайнет)" — часто много номеров в одной ячейке
                            phone_list = _unescape(_get(row, "Список телефонов (Скайнет)"))
                            for pval in _split_multi(phone_list):
                                phones.append((ContactPhone.PhoneType.OTHER, pval))

                            emails = []
                            if email_work:
                                emails.append((ContactEmail.EmailType.WORK, email_work))
                            if email_personal:
                                emails.append((ContactEmail.EmailType.PERSONAL, email_personal))
                            if email_other:
                                emails.append((ContactEmail.EmailType.OTHER, email_other))

                            # Создаём контакт только если есть хоть какие-то каналы связи или имя
                            if phones or emails or contact_person_raw:
                                ln, fn = _parse_person_name(contact_person_raw)
                                c = Contact.objects.create(
                                    company=company,
                                    last_name=ln,
                                    first_name=fn,
                                    position=contact_position,
                                    status=_unescape(_get(row, "Статус"))[:120],
                                    note=_unescape(_get(row, "Примечание")),
                                    raw_fields={"source": "amo_import", "amo_company_id": amo_id},
                                )
                                # дедуп на уровне контакта
                                seen_p = set()
                                for tpe, v in phones:
                                    v = _norm_spaces(v)
                                    if not v or v in seen_p:
                                        continue
                                    seen_p.add(v)
                                    ContactPhone.objects.create(contact=c, type=tpe, value=v[:50])
                                seen_e = set()
                                for tpe, v in emails:
                                    v = _norm_spaces(v).lower()
                                    if not v or v in seen_e:
                                        continue
                                    seen_e.add(v)
                                    try:
                                        ContactEmail.objects.create(contact=c, type=tpe, value=v)
                                    except Exception:
                                        # если email невалиден — не роняем импорт
                                        pass

                    # кеш для дедупа
                    if inn:
                        company_by_inn[inn] = company
                    company_by_name_addr[(_norm_name(company.name), _norm_name(company.address))] = company

                    # превью
                    if result.preview_companies is not None and len(result.preview_companies) < max(20, limit_companies):
                        result.preview_companies.append(
                            {"name": company.name, "inn": company.inn, "address": company.address, "status": status_name}
                        )

                    if companies_only and limit_companies and result.created_companies + result.updated_companies >= limit_companies:
                        # Быстрый выход: не читаем дальше файл
                        break

        if dry_run:
            transaction.set_rollback(True)

    _run()
    return result


