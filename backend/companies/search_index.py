from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Iterable
from uuid import UUID

from django.db import connection, transaction
from django.utils import timezone

from companies.models import (
    Company,
    CompanyEmail,
    CompanyNote,
    CompanyPhone,
    Contact,
    ContactEmail,
    ContactPhone,
    CompanySearchIndex,
)
from tasksapp.models import Task


_TOKEN_RE = re.compile(r"[0-9]+|[A-Za-zА-Яа-яЁё]+", re.UNICODE)
_DIGITS_RE = re.compile(r"\D+")
_WS_RE = re.compile(r"\s+")
# Пунктуация, которую для поискового индекса имеет смысл превращать в пробел
_PUNCT_TO_SPACE_RE = re.compile(r"[\"'«»()[\]{}\-–—_/]+")


def fold_text(s: str) -> str:
    """
    Нормализация для индекса/поиска:
    - lower
    - ё→е
    - схлопываем пробелы
    """
    if not s:
        return ""
    s = str(s)
    s = s.replace("\u00a0", " ")
    s = s.lower().replace("ё", "е")
    s = _WS_RE.sub(" ", s).strip()
    return s


def fold_text_punct_to_space(s: str) -> str:
    """
    Нормализует текст для поиска, дополнительно приводя "разделительную" пунктуацию к пробелу.

    Используется для формирования альтернативного представления названий:
    - "ООО «Сиб-Энерго» (ЮГ)" → "ооо сиб энерго юг"
    """
    if not s:
        return ""
    s = str(s)
    s = _PUNCT_TO_SPACE_RE.sub(" ", s)
    return fold_text(s)


# Организационно-правовые формы (для фильтрации glued-шума)
_ORG_FORMS_SET = frozenset({"ооо", "ип", "зао", "оао", "пао", "ао", "нко", "тк", "ооо."})


def fold_text_glued(s: str) -> str:
    """
    Нормализует текст и склеивает токены без пробелов.

    Пример: "ООО «Сиб-Энерго» (ЮГ)" → "ооосибэнергоюг".
    Используется только для коротких строк (название/юр.название), чтобы не раздувать индекс.

    Ограничения для снижения шума:
    - минимальная длина glued-строки >= 6 символов
    - не добавлять glued, если строка состоит только из ОПФ (ооо, ип и т.д.)
    """
    base = fold_text_punct_to_space(s)
    if not base:
        return ""
    glued = base.replace(" ", "")
    # Минимальная длина для glued-варианта
    if len(glued) < 6:
        return ""
    # Проверка: не состоит ли только из ОПФ (токены из _ORG_FORMS_SET)
    tokens = base.split()
    if tokens and all(tok in _ORG_FORMS_SET for tok in tokens):
        return ""
    return glued


def only_digits(s: str) -> str:
    if not s:
        return ""
    return _DIGITS_RE.sub("", str(s))


# Стоп-токены для текстового поиска: не искать по ним отдельно, чтобы не плодить мусор.
# ОПФ, аббревиатуры адресов, гео/общие слова.
_SEARCH_STOP_TOKENS = frozenset({
    "ооо", "ип", "ао", "зао", "оао", "пао", "нко", "тк",
    "г", "город", "ул", "улица", "д", "дом", "офис", "кв", "корп", "корпус",
    "стр", "строение", "компания", "организация", "обл", "область", "респ", "республика",
    "район", "проспект", "пр", "шоссе", "помещение", "литера", "лит",
})
# Дополнительные стоп-токены только для запросов типа "адрес" (сильнее отсекаем служебные слова).
_ADDRESS_EXTRA_STOP = frozenset({
    "г", "город", "ул", "улица", "д", "дом", "корп", "корпус", "стр", "строение",
    "офис", "кв", "помещение", "обл", "область", "респ", "республика", "район",
    "проспект", "пр", "шоссе", "пер", "переулок", "наб", "набережная", "бул", "бульвар",
})

# Тип текстового запроса для поле-зависимого ранжирования.
TEXT_QUERY_WEBSITE = "website"
TEXT_QUERY_PERSON = "person"
TEXT_QUERY_ADDRESS = "address"
TEXT_QUERY_COMPANY_OR_GENERAL = "company_name_or_general"


def filter_stop_tokens(
    text_tokens: tuple[str, ...],
    *,
    for_address: bool = False,
) -> tuple[str, ...]:
    """
    Убирает стоп-токены из списка. Если after фильтрации 0 значимых — вызывающий код
    должен вернуть пустую выдачу (кроме exact-фазы).
    При for_address=True используется расширенный набор адресных стоп-токенов.
    """
    if not text_tokens:
        return ()
    stop = _SEARCH_STOP_TOKENS | _ADDRESS_EXTRA_STOP if for_address else _SEARCH_STOP_TOKENS
    out = [t for t in text_tokens if t and t.lower() not in stop]
    return tuple(out)


def classify_text_query(raw: str, text_tokens: tuple[str, ...]) -> str:
    """
    Простая эвристика типа запроса: website, person, address, company_name_or_general.
    Используется для поле-зависимого буста (сайт → t_other, ФИО → t_contacts, название → t_name).
    """
    r = (raw or "").strip()
    if not r:
        return TEXT_QUERY_COMPANY_OR_GENERAL
    r_lower = r.lower()
    # Похож на домен/сайт: точка, нет пробелов, или начинается с http(s)
    if r.startswith("http://") or r.startswith("https://"):
        return TEXT_QUERY_WEBSITE
    if "." in r and " " not in r and not any(c.isdigit() for c in r[:r.index(".")]):
        return TEXT_QUERY_WEBSITE
    # Похож на адрес: ключевые слова
    addr_keywords = (
        "ул", "улица", "пр-т", "проспект", "пр ", "дом", " д ", "кв", "оф", "корп", "строение",
        "г ", "город", "обл", "область", "респ", "район", "шоссе", "переулок", "наб", "бульвар",
    )
    if any(kw in r_lower for kw in addr_keywords):
        return TEXT_QUERY_ADDRESS
    # Похож на ФИО: 2–3 слова буквами, без цифр, каждое >= 2 символов
    tokens = [t for t in text_tokens if len(t) >= 2 and t.isalpha()]
    if 2 <= len(tokens) <= 3 and not any(only_digits(t) for t in text_tokens):
        return TEXT_QUERY_PERSON
    return TEXT_QUERY_COMPANY_OR_GENERAL


@dataclass(frozen=True)
class ParsedQuery:
    raw: str
    text_tokens: tuple[str, ...]
    strong_digit_tokens: tuple[str, ...]
    weak_digit_tokens: tuple[str, ...]


def parse_query(q: str, *, max_tokens: int = 12) -> ParsedQuery:
    raw = (q or "").strip()
    if not raw:
        return ParsedQuery(raw="", text_tokens=(), strong_digit_tokens=(), weak_digit_tokens=())

    # Один номер телефона (11 цифр с 7/8): считаем весь ввод одним номером для ранжирования и подсветки
    digits_only = only_digits(raw)
    if len(digits_only) == 11 and digits_only[0] in ("7", "8"):
        strong_digits = [digits_only]
        if digits_only.startswith("8"):
            strong_digits.append("7" + digits_only[1:])
        return ParsedQuery(
            raw=raw,
            text_tokens=(),
            strong_digit_tokens=tuple(dict.fromkeys(strong_digits)),
            weak_digit_tokens=(),
        )

    # Поиск по email: не разбивать на токены по @ и точке — ищем как одну строку
    if "@" in raw and "." in raw.split("@")[-1]:
        email_norm = fold_text(raw)
        if len(email_norm) >= 5:
            return ParsedQuery(
                raw=raw,
                text_tokens=(email_norm,),
                strong_digit_tokens=(),
                weak_digit_tokens=(),
            )

    text_tokens: list[str] = []
    strong_digits: list[str] = []
    weak_digits: list[str] = []

    for m in _TOKEN_RE.finditer(raw):
        tok = (m.group(0) or "").strip()
        if not tok:
            continue
        if tok.isdigit():
            if len(tok) >= 4:
                strong_digits.append(tok)
            elif 2 <= len(tok) <= 3:
                weak_digits.append(tok)
        else:
            tt = fold_text(tok)
            if len(tt) >= 2:
                text_tokens.append(tt)

        if len(text_tokens) + len(strong_digits) + len(weak_digits) >= max_tokens:
            break

    # Номер с 8: добавляем вариант 7 для поиска
    for d in list(strong_digits):
        if len(d) == 11 and d.startswith("8"):
            strong_digits.append("7" + d[1:])

    # дедуп + стабильный порядок
    def _dedup(items: list[str]) -> tuple[str, ...]:
        seen: set[str] = set()
        out: list[str] = []
        for x in items:
            if x in seen:
                continue
            seen.add(x)
            out.append(x)
        return tuple(out)

    return ParsedQuery(
        raw=raw,
        text_tokens=_dedup(text_tokens),
        strong_digit_tokens=_dedup(strong_digits),
        weak_digit_tokens=_dedup(weak_digits),
    )


def _safe_join(parts: Iterable[str], sep: str = "\n") -> str:
    out = [p for p in (p.strip() for p in parts) if p]
    return sep.join(out)


def _normalize_email_for_index(value: str | None) -> str:
    """Нормализует email для индекса: lower().strip()."""
    if not value:
        return ""
    return str(value).strip().lower()


def _parse_inn_list(inn_str: str | None) -> list[str]:
    """
    Парсит Company.inn (может быть списком через запятую/слеш) и возвращает список валидных ИНН (10/12 цифр).
    """
    if not inn_str:
        return []
    raw = str(inn_str).strip()
    if not raw:
        return []
    # Разделяем по запятой/слешу/точке с запятой
    parts: list[str] = []
    tmp = raw.replace(";", ",").replace("/", ",")
    for chunk in tmp.split(","):
        part = (chunk or "").strip()
        if part:
            parts.append(part)
    # Извлекаем только валидные ИНН (10 или 12 цифр)
    valid_inns: list[str] = []
    for part in parts:
        digits = only_digits(part)
        if len(digits) in (10, 12):
            valid_inns.append(digits)
    return valid_inns


def build_company_index_payload(company: Company) -> dict[str, str | list[str]]:
    """
    Строит payload для CompanySearchIndex из “истины” (Company + связанные модели).
    Возвращает текстовые группы, агрегаты и денормализованные массивы для exact-поиска.
    """
    # Основные поля компании
    ident_parts = [
        f"инн: {company.inn}" if (company.inn or "").strip() else "",
        f"кпп: {company.kpp}" if (company.kpp or "").strip() else "",
        f"amo_id: {company.amocrm_company_id}" if company.amocrm_company_id else "",
    ]

    name_parts: list[str] = []

    # Название компании + нормализованные представления (тире/кавычки/склейка)
    if (company.name or "").strip():
        name_parts.append(f"название: {company.name}")
        # "ООО «Сиб-Энерго» (ЮГ)" → "ооо сиб энерго юг"
        folded = fold_text_punct_to_space(company.name)
        if folded:
            name_parts.append(f"название_norm: {folded}")
        # "ООО «Сиб-Энерго» (ЮГ)" → "ооосибэнергоюг" (только если glued не пустой и не слишком длинный)
        glued = fold_text_glued(company.name)
        if glued and len(glued) <= 64:
            name_parts.append(f"название_слито: {glued}")

    # Юридическое название + нормализованные представления
    if (company.legal_name or "").strip():
        name_parts.append(f"юр_название: {company.legal_name}")
        folded_legal = fold_text_punct_to_space(company.legal_name)
        if folded_legal:
            name_parts.append(f"юр_название_norm: {folded_legal}")
        # Юридическое название: glued только если не пустой и не слишком длинный
        glued_legal = fold_text_glued(company.legal_name)
        if glued_legal and len(glued_legal) <= 64:
            name_parts.append(f"юр_название_слито: {glued_legal}")

    # Прочие поля, которые логически относятся к "имени"
    if (company.activity_kind or "").strip():
        name_parts.append(f"вид_деятельности: {company.activity_kind}")
    if (company.contact_name or "").strip():
        name_parts.append(f"контакт_фио_в_карточке: {company.contact_name}")
    if (company.contact_position or "").strip():
        name_parts.append(f"контакт_должность_в_карточке: {company.contact_position}")

    other_parts = [
        f"адрес: {company.address}" if (company.address or "").strip() else "",
        f"сайт: {company.website}" if (company.website or "").strip() else "",
        f"график: {company.work_schedule}" if (company.work_schedule or "").strip() else "",
        f"email_осн: {company.email}" if (company.email or "").strip() else "",
        f"телефон_осн: {company.phone}" if (company.phone or "").strip() else "",
        f"коммент_тел_осн: {company.phone_comment}" if (company.phone_comment or "").strip() else "",
    ]

    # Доп. телефоны/почты компании
    for p in getattr(company, "phones", []).all():
        other_parts.append(f"телефон_компании: {p.value}" if (p.value or "").strip() else "")
        if (p.comment or "").strip():
            other_parts.append(f"коммент_телефона_компании: {p.comment}")

    for e in getattr(company, "emails", []).all():
        other_parts.append(f"email_компании: {e.value}" if (e.value or "").strip() else "")

    # Контакты + их телефоны/почты/заметки
    contact_parts: list[str] = []
    for c in getattr(company, "contacts", []).all():
        full_name = " ".join([c.last_name or "", c.first_name or ""]).strip()
        if full_name:
            contact_parts.append(f"контакт: {full_name}")
        if (c.position or "").strip():
            contact_parts.append(f"должность_контакта: {c.position}")
        if (c.status or "").strip():
            contact_parts.append(f"статус_контакта: {c.status}")
        if (c.note or "").strip():
            contact_parts.append(f"прим_контакта: {c.note}")
        for cp in getattr(c, "phones", []).all():
            if (cp.value or "").strip():
                contact_parts.append(f"телефон_контакта: {cp.value}")
            if (cp.comment or "").strip():
                contact_parts.append(f"коммент_телефона_контакта: {cp.comment}")
        for ce in getattr(c, "emails", []).all():
            if (ce.value or "").strip():
                contact_parts.append(f"email_контакта: {ce.value}")

    # Заметки и задачи
    for n in getattr(company, "notes", []).all():
        txt = (n.text or "").strip()
        if (n.attachment_name or "").strip():
            txt = _safe_join([txt, f"файл: {n.attachment_name}"], " | ")
        if txt:
            other_parts.append(f"заметка: {txt}")

    for t in getattr(company, "tasks", []).all():
        title = (t.title or "").strip()
        desc = (t.description or "").strip()
        if title:
            other_parts.append(f"задача: {title}")
        if desc:
            other_parts.append(f"описание_задачи: {desc}")

    # Сырые поля импорта — как “всё остальное” (ограничим размер, чтобы не раздувать индекс)
    try:
        raw = company.raw_fields or {}
        if raw:
            raw_str = json.dumps(raw, ensure_ascii=False, sort_keys=True)
            if len(raw_str) <= 600 and "http" not in raw_str.lower() and '"href"' not in raw_str:
                other_parts.append(f"raw_fields: {raw_str}")
    except Exception:
        pass

    t_ident = fold_text(_safe_join(ident_parts))
    t_name = fold_text(_safe_join(name_parts))
    t_contacts = fold_text(_safe_join(contact_parts))
    t_other = fold_text(_safe_join(other_parts))

    plain_text = _safe_join([t_ident, t_name, t_contacts, t_other])

    # digits: собираем цифры из всех значимых полей/связей
    digit_sources: list[str] = [
        company.inn,
        company.kpp,
        company.phone,
        company.amocrm_company_id,
    ]  # type: ignore[list-item]
    for p in getattr(company, "phones", []).all():
        digit_sources.append(p.value)
    for c in getattr(company, "contacts", []).all():
        for cp in getattr(c, "phones", []).all():
            digit_sources.append(cp.value)
        if c.amocrm_contact_id:
            digit_sources.append(str(c.amocrm_contact_id))
    digits = " ".join([only_digits(x) for x in digit_sources if x])

    # Денормализованные массивы для быстрого exact-поиска (без JOIN).
    normalized_phones: list[str] = []
    normalized_emails: list[str] = []
    normalized_inns: list[str] = []

    # Телефоны: Company.phone + CompanyPhone + ContactPhone
    from companies.normalizers import normalize_phone

    if company.phone:
        phone_norm = normalize_phone(company.phone)
        if phone_norm and phone_norm.startswith("+"):
            normalized_phones.append(phone_norm)
    for p in getattr(company, "phones", []).all():
        if p.value:
            phone_norm = normalize_phone(p.value)
            if phone_norm and phone_norm.startswith("+") and phone_norm not in normalized_phones:
                normalized_phones.append(phone_norm)
    for c in getattr(company, "contacts", []).all():
        for cp in getattr(c, "phones", []).all():
            if cp.value:
                phone_norm = normalize_phone(cp.value)
                if phone_norm and phone_norm.startswith("+") and phone_norm not in normalized_phones:
                    normalized_phones.append(phone_norm)

    # Email: Company.email + CompanyEmail + ContactEmail
    if company.email:
        email_norm = _normalize_email_for_index(company.email)
        if email_norm:
            normalized_emails.append(email_norm)
    for e in getattr(company, "emails", []).all():
        if e.value:
            email_norm = _normalize_email_for_index(e.value)
            if email_norm and email_norm not in normalized_emails:
                normalized_emails.append(email_norm)
    for c in getattr(company, "contacts", []).all():
        for ce in getattr(c, "emails", []).all():
            if ce.value:
                email_norm = _normalize_email_for_index(ce.value)
                if email_norm and email_norm not in normalized_emails:
                    normalized_emails.append(email_norm)

    # ИНН: парсим Company.inn (может быть списком)
    if company.inn:
        parsed_inns = _parse_inn_list(company.inn)
        normalized_inns.extend(parsed_inns)

    return {
        "t_ident": t_ident,
        "t_name": t_name,
        "t_contacts": t_contacts,
        "t_other": t_other,
        "plain_text": plain_text,
        "digits": digits,
        "normalized_phones": normalized_phones,
        "normalized_emails": normalized_emails,
        "normalized_inns": normalized_inns,
    }


def rebuild_company_search_index(company_id: UUID) -> None:
    """
    Перестраивает индекс для одной компании.
    Делает ограниченное число запросов и не создаёт N+1 в списках (там rebuild не вызываем).
    """
    if connection.vendor != "postgresql":
        return

    company = (
        Company.objects.filter(id=company_id)
        .prefetch_related(
            "phones",
            "emails",
            "contacts__phones",
            "contacts__emails",
            "notes",
            "tasks",
        )
        .first()
    )
    if not company:
        CompanySearchIndex.objects.filter(company_id=company_id).delete()
        return

    payload = build_company_index_payload(company)
    with transaction.atomic():
        obj, _created = CompanySearchIndex.objects.select_for_update().get_or_create(company=company)
        obj.t_ident = payload["t_ident"]
        obj.t_name = payload["t_name"]
        obj.t_contacts = payload["t_contacts"]
        obj.t_other = payload["t_other"]
        obj.plain_text = payload["plain_text"]
        obj.digits = payload["digits"]
        obj.normalized_phones = payload.get("normalized_phones", [])
        obj.normalized_emails = payload.get("normalized_emails", [])
        obj.normalized_inns = payload.get("normalized_inns", [])
        obj.updated_at = timezone.now()
        obj.save(
            update_fields=[
                "t_ident",
                "t_name",
                "t_contacts",
                "t_other",
                "plain_text",
                "digits",
                "normalized_phones",
                "normalized_emails",
                "normalized_inns",
                "updated_at",
            ]
        )

