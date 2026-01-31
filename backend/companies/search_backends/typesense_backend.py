"""
Backend поиска компаний через Typesense.
Совместим с интерфейсом CompanySearchService: apply(qs, query), explain(companies, query).
Typesense v30+: все вызовы через HTTP (requests), без пакета typesense — нет deprecation-предупреждений.
"""
from __future__ import annotations

import logging
from uuid import UUID

import requests
from django.conf import settings
from django.db.models import Case, IntegerField, Value, When

from companies.models import Company

logger = logging.getLogger(__name__)

# Имя коллекции и схема
COLLECTION_NAME = "companies"

# Стоп-слова для поиска (орг. формы и частые слова) — применяются при поиске через параметр stopwords
STOPWORDS_SET_ID = "ru_org"
RUSSIAN_ORG_STOPWORDS = [
    "ооо", "зао", "оао", "пао", "ао", "ип", "нко", "тк",
    "филиал", "федеральное", "государственное", "казенное", "учреждение",
    "общество", "ограниченной", "ответственностью",
]

# Синонимы для поиска по адресам и названиям (многовариантные группы). Typesense v30: synonym_sets.
SYNONYM_SET_NAME = "ru_address_synonyms"
RUSSIAN_SYNONYMS = [
    ["ул", "улица"],
    ["пр-т", "проспект", "пр"],
    ["наб", "набережная"],
    ["пер", "переулок"],
    ["ш", "шоссе"],
]

SCHEMA = {
    "name": COLLECTION_NAME,
    "fields": [
        {"name": "id", "type": "string", "facet": False},
        {"name": "name", "type": "string", "facet": False, "locale": "ru", "stem": True},
        {"name": "legal_name", "type": "string", "facet": False, "locale": "ru", "stem": True},
        {"name": "contacts", "type": "string", "facet": False, "locale": "ru", "stem": True},
        {"name": "emails", "type": "string[]", "facet": False},
        {"name": "phones", "type": "string[]", "facet": False},
        {"name": "inn", "type": "string", "facet": False},
        {"name": "kpp", "type": "string", "facet": False},
        {"name": "address", "type": "string", "facet": False, "locale": "ru", "stem": True},
        {"name": "website", "type": "string", "facet": False},
        {"name": "notes", "type": "string", "facet": False, "locale": "ru", "stem": True},
        {"name": "updated_at", "type": "int64", "facet": False},
    ],
    "default_sorting_field": "updated_at",
}

_TIMEOUT = 10


def _typesense_base_url() -> str:
    port = str(getattr(settings, "TYPESENSE_PORT", 8108))
    protocol = (getattr(settings, "TYPESENSE_PROTOCOL", "http") or "http").rstrip("://")
    host = getattr(settings, "TYPESENSE_HOST", "localhost")
    return f"{protocol}://{host}:{port}"


def _typesense_headers() -> dict:
    api_key = getattr(settings, "TYPESENSE_API_KEY", "") or ""
    return {"X-TYPESENSE-API-KEY": api_key, "Content-Type": "application/json"}


def _typesense_available() -> bool:
    """Проверка доступности Typesense (GET /collections)."""
    try:
        r = requests.get(
            f"{_typesense_base_url()}/collections",
            headers=_typesense_headers(),
            timeout=5,
        )
        return r.status_code == 200
    except Exception:
        return False


def build_company_document(company: Company) -> dict:
    """
    Строит документ для индекса Typesense из модели Company.
    Ожидается, что company уже загружен с prefetch_related("phones", "emails", "contacts__phones", "contacts__emails", "notes", "tasks").
    """
    from companies.search_index import fold_text, only_digits

    def safe(s: str) -> str:
        return (s or "").strip()

    name = fold_text(safe(company.name))
    legal_name = fold_text(safe(company.legal_name))

    contact_parts = []
    for c in getattr(company, "contacts", []).all():
        full = " ".join([c.last_name or "", c.first_name or ""]).strip()
        if full:
            contact_parts.append(full)
    contacts = " ".join(contact_parts)
    contacts = fold_text(contacts)

    emails = []
    if company.email:
        emails.append(safe(company.email))
    for e in getattr(company, "emails", []).all():
        if e.value:
            emails.append(safe(e.value))
    for c in getattr(company, "contacts", []).all():
        for ce in getattr(c, "emails", []).all():
            if ce.value:
                emails.append(safe(ce.value))

    phones = []
    if company.phone:
        phones.append(safe(company.phone))
    for p in getattr(company, "phones", []).all():
        if p.value:
            phones.append(safe(p.value))
    for c in getattr(company, "contacts", []).all():
        for cp in getattr(c, "phones", []).all():
            if cp.value:
                phones.append(safe(cp.value))

    inn = safe(company.inn or "")
    kpp = safe(company.kpp or "")
    address = fold_text(safe(company.address or ""))
    website = fold_text(safe(company.website or ""))

    note_parts = []
    for n in getattr(company, "notes", []).all():
        if (n.text or "").strip():
            note_parts.append((n.text or "").strip()[:2000])
    for t in getattr(company, "tasks", []).all():
        if (t.title or "").strip():
            note_parts.append((t.title or "").strip()[:500])
        if (t.description or "").strip():
            note_parts.append((t.description or "").strip()[:1000])
    notes = " ".join(note_parts)
    notes = fold_text(notes)[:10000]

    updated_at = int(company.updated_at.timestamp()) if company.updated_at else 0

    return {
        "id": str(company.id),
        "name": name or " ",
        "legal_name": legal_name or " ",
        "contacts": contacts or " ",
        "emails": list(dict.fromkeys(emails)) if emails else [" "],
        "phones": list(dict.fromkeys(phones)) if phones else [" "],
        "inn": inn or " ",
        "kpp": kpp or " ",
        "address": address or " ",
        "website": website or " ",
        "notes": notes or " ",
        "updated_at": updated_at,
    }


def ensure_collection() -> None:
    """Создаёт коллекцию, если её нет (HTTP API)."""
    base = _typesense_base_url()
    headers = _typesense_headers()
    try:
        r = requests.get(f"{base}/collections/{COLLECTION_NAME}", headers=headers, timeout=_TIMEOUT)
        if r.status_code == 200:
            ensure_stopwords()
            return
    except Exception:
        pass
    try:
        r = requests.post(f"{base}/collections", json=SCHEMA, headers=headers, timeout=_TIMEOUT)
        if r.status_code in (200, 201):
            ensure_stopwords()
        else:
            logger.warning("Typesense: не удалось создать коллекцию %s: %s %s", COLLECTION_NAME, r.status_code, r.text[:200])
    except Exception as e:
        logger.warning("Typesense: не удалось создать коллекцию %s: %s", COLLECTION_NAME, e)


def ensure_stopwords() -> None:
    """Создаёт или обновляет набор стоп-слов (HTTP API v30)."""
    body = {"stopwords": RUSSIAN_ORG_STOPWORDS, "locale": "ru"}
    try:
        r = requests.put(
            f"{_typesense_base_url()}/stopwords/{STOPWORDS_SET_ID}",
            json=body,
            headers=_typesense_headers(),
            timeout=_TIMEOUT,
        )
        if r.status_code not in (200, 201):
            logger.debug("Typesense: стоп-слова %s — %s %s", STOPWORDS_SET_ID, r.status_code, r.text[:200])
    except Exception as e:
        logger.debug("Typesense: стоп-слова не заданы (%s): %s", STOPWORDS_SET_ID, e)


def ensure_synonyms(collection: str | None = None) -> int:
    """
    Создаёт или обновляет синонимы для коллекции компаний (Typesense v30: synonym_sets).
    PUT /synonym_sets/:name с items, затем PATCH коллекции — synonym_sets: [name].
    Возвращает количество групп в наборе при успехе, иначе 0.
    """
    coll = collection or getattr(settings, "TYPESENSE_COLLECTION_COMPANIES", COLLECTION_NAME)
    base = _typesense_base_url()
    headers = _typesense_headers()
    items = []
    for i, group in enumerate(RUSSIAN_SYNONYMS):
        if not group:
            continue
        syn_id = "ru_syn_%d" % (i + 1)
        items.append({"id": syn_id, "synonyms": group})
    if not items:
        return 0
    try:
        r = requests.put(
            f"{base}/synonym_sets/{SYNONYM_SET_NAME}",
            json={"items": items},
            headers=headers,
            timeout=10,
        )
        if r.status_code not in (200, 201):
            logger.debug("Typesense: synonym_sets %s — %s %s", SYNONYM_SET_NAME, r.status_code, r.text[:200])
            return 0
        # Привязать набор синонимов к коллекции (v30)
        r2 = requests.patch(
            f"{base}/collections/{coll}",
            json={"synonym_sets": [SYNONYM_SET_NAME]},
            headers=headers,
            timeout=10,
        )
        if r2.status_code not in (200, 201):
            logger.debug("Typesense: PATCH collection %s synonym_sets — %s %s", coll, r2.status_code, r2.text[:200])
        return len(items)
    except Exception as e:
        logger.debug("Typesense: ensure_synonyms failed: %s", e)
        return 0


def _typesense_import_documents(collection: str, docs: list[dict]) -> tuple[int, list[str]]:
    """
    POST /collections/{name}/documents/import (NDJSON, action=upsert).
    Возвращает (количество успешных, список ошибок).
    """
    if not docs:
        return 0, []
    import json as _json
    ndjson = "\n".join(_json.dumps(d, ensure_ascii=False) for d in docs)
    headers = _typesense_headers()
    headers["Content-Type"] = "application/x-ndjson"
    try:
        r = requests.post(
            f"{_typesense_base_url()}/collections/{collection}/documents/import",
            params={"action": "upsert"},
            data=ndjson.encode("utf-8"),
            headers=headers,
            timeout=60,
        )
        if r.status_code != 200:
            return 0, [r.text[:500]]
        count = 0
        errors = []
        for line in (r.text or "").strip().split("\n"):
            if not line:
                continue
            try:
                item = _json.loads(line)
                if item.get("success") is True:
                    count += 1
                elif item.get("error"):
                    errors.append(item.get("error", "")[:200])
            except Exception:
                pass
        return count, errors
    except Exception as e:
        return 0, [str(e)]


def _typesense_search(collection: str, params: dict) -> dict | None:
    """GET /collections/{name}/documents/search. Возвращает JSON ответа или None при ошибке."""
    try:
        r = requests.get(
            f"{_typesense_base_url()}/collections/{collection}/documents/search",
            params=params,
            headers=_typesense_headers(),
            timeout=_TIMEOUT,
        )
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


class TypesenseSearchBackend:
    """
    Поиск компаний через Typesense (HTTP API, без пакета typesense).
    Интерфейс совместим с CompanySearchService: apply(qs, query), explain(companies, query).
    """

    def __init__(self, *, max_results_cap: int = 5000):
        self.max_results_cap = max_results_cap

    def _fallback_apply(self, qs, query: str):
        """При недоступности Typesense — поиск через Postgres."""
        from companies.search_service import CompanySearchService
        return CompanySearchService(max_results_cap=self.max_results_cap).apply(qs=qs, query=query)

    def apply(self, *, qs, query: str):
        """
        Возвращает queryset компаний по результатам Typesense.
        При недоступности Typesense: если TYPESENSE_FALLBACK_TO_POSTGRES — поиск через Postgres, иначе qs.none().
        """
        q = (query or "").strip()
        if not q:
            return qs
        if len(q) < 2:
            if getattr(settings, "TYPESENSE_FALLBACK_TO_POSTGRES", True):
                return self._fallback_apply(qs, q)
            return qs.none()

        if not _typesense_available():
            if getattr(settings, "TYPESENSE_FALLBACK_TO_POSTGRES", True):
                logger.info("Typesense недоступен — fallback на Postgres.")
                return self._fallback_apply(qs, q)
            return qs.none()

        collection = getattr(settings, "TYPESENSE_COLLECTION_COMPANIES", COLLECTION_NAME)
        search_params = {
            "q": q,
            "query_by": "name,legal_name,contacts,emails,phones,inn,kpp,address,website,notes",
            "query_by_weights": "5,5,3,2,2,5,5,1,1,0.5",
            "prefix": "true",
            "num_typos": 2,
            "max_facet_values": 0,
            "per_page": self.max_results_cap,
            "sort_by": "_text_match:desc,updated_at:desc",
            "highlight_full_fields": "name,legal_name,contacts,emails,phones,address,website,notes",
            "synonym_sets": SYNONYM_SET_NAME,
            "stopwords": STOPWORDS_SET_ID,
        }
        res = _typesense_search(collection, search_params)
        if not res:
            if getattr(settings, "TYPESENSE_FALLBACK_TO_POSTGRES", True):
                return self._fallback_apply(qs, q)
            return qs.none()

        hits = res.get("hits") or []
        if not hits:
            return qs.none()

        ids_ordered = []
        for h in hits:
            doc = h.get("document") or {}
            sid = doc.get("id")
            if not sid:
                continue
            try:
                uid = UUID(sid)
                ids_ordered.append(uid)
            except (ValueError, TypeError):
                continue

        if not ids_ordered:
            return qs.none()

        # Сохраняем порядок Typesense: фильтруем qs и сортируем по позиции в ids_ordered
        order_case = Case(
            *[When(id=uid, then=Value(i)) for i, uid in enumerate(ids_ordered)],
            output_field=IntegerField(),
        )
        return (
            qs.filter(id__in=ids_ordered)
            .annotate(_search_order=order_case)
            .order_by("_search_order")
        )

    def explain(self, *, companies: list[Company], query: str, max_reasons_per_company: int = 50):
        """
        Формирует match_reasons из подсветки Typesense для переданных компаний.
        """
        from companies.search_service import SearchExplain, SearchReason

        q = (query or "").strip()
        if not q or not companies:
            return {}

        if not _typesense_available():
            return _fallback_explain(companies, query, max_reasons_per_company)

        collection = getattr(settings, "TYPESENSE_COLLECTION_COMPANIES", COLLECTION_NAME)
        ids_filter = ",".join(str(c.id) for c in companies)
        explain_params = {
            "q": q,
            "query_by": "name,legal_name,contacts,emails,phones,inn,kpp,address,website,notes",
            "filter_by": f"id:[{ids_filter}]",
            "prefix": "true",
            "num_typos": 2,
            "per_page": len(companies) + 10,
            "highlight_full_fields": "name,legal_name,contacts,emails,phones,address,website,notes",
            "synonym_sets": SYNONYM_SET_NAME,
            "stopwords": STOPWORDS_SET_ID,
        }
        res = _typesense_search(collection, explain_params)
        if not res:
            return _fallback_explain(companies, query, max_reasons_per_company)

        hits = res.get("hits") or []
        highlight_by_id = {}
        for h in hits:
            doc = h.get("document") or {}
            sid = doc.get("id")
            if not sid:
                continue
            hi = h.get("highlights") or []
            if hi:
                highlight_by_id[sid] = hi

        # Приоритет полей для причин: только главное (ИНН, телефон, название, контакт, адрес), без шума заметок
        REASON_PRIORITY = ("inn", "kpp", "phones", "name", "legal_name", "contacts", "address", "emails", "website", "notes")
        labels = {
            "name": "Название",
            "legal_name": "Юр. название",
            "contacts": "Контакт",
            "emails": "Email",
            "phones": "Телефон",
            "inn": "ИНН",
            "kpp": "КПП",
            "address": "Адрес",
            "website": "Сайт",
            "notes": "Примечание",
        }
        out = {}
        for c in companies:
            sid = str(c.id)
            highlights = highlight_by_id.get(sid) or []
            # Если Typesense не вернул подсветку — берём причины из Postgres (не подставляем "Совпадение: название")
            if not highlights:
                fallback = _fallback_explain([c], query, max_reasons_per_company)
                if c.id in fallback:
                    out[c.id] = fallback[c.id]
                else:
                    out[c.id] = SearchExplain(
                        company_id=c.id,
                        reasons=(),
                        reasons_total=0,
                        name_html=c.name or "",
                        inn_html=c.inn or "",
                        address_html=c.address or "",
                    )
                continue

            # Собираем причины по приоритету, не более 3 (без шума заметок)
            FIELD_PRIORITY = ("inn", "kpp", "phones", "name", "legal_name", "contacts", "address", "emails", "website", "notes")
            order = {f: i for i, f in enumerate(FIELD_PRIORITY)}
            highlight_by_field = {}
            for hl in highlights:
                field = hl.get("field") or ""
                raw = (hl.get("value") or hl.get("snippet") or "").strip()
                if isinstance(raw, list):
                    raw = " ".join(str(x).strip() for x in raw if x)[:500]
                if not raw:
                    continue
                highlight_by_field.setdefault(field, (raw, plain := raw.replace("<mark>", "").replace("</mark>", "")))
            sorted_fields = sorted(highlight_by_field.keys(), key=lambda f: (order.get(f, 99), f))
            reason_fields = [f for f in sorted_fields if f != "notes" or len(highlight_by_field) <= 1][:2]
            # Короткие сниппеты в "Найдено" (без длинного текста)
            _snippet_len = 70
            reasons = []
            for f in reason_fields:
                raw, plain = highlight_by_field[f]
                snippet = raw[: _snippet_len] + ("…" if len(raw) > _snippet_len else "")
                reasons.append(
                    SearchReason(
                        field=f,
                        label=labels.get(f, f),
                        value=plain[:_snippet_len] + ("…" if len(plain) > _snippet_len else ""),
                        value_html=snippet,
                    )
                )
            reason_field_set = set(reason_fields)

            # Подсвечиваем в таблице только то поле, по которому реально нашли
            name_html = highlight_by_field["name"][0][:300] if "name" in reason_field_set else (c.name or "")
            inn_html = highlight_by_field["inn"][0][:120] if "inn" in reason_field_set else (c.inn or "")
            address_html = highlight_by_field["address"][0][:300] if "address" in reason_field_set else (c.address or "")

            out[c.id] = SearchExplain(
                company_id=c.id,
                reasons=tuple(reasons),
                reasons_total=len(highlight_by_field),
                name_html=name_html,
                inn_html=inn_html,
                address_html=address_html,
            )
        return out


def _fallback_explain(companies, query, max_reasons_per_company):
    """При недоступности Typesense используем Postgres explain."""
    from companies.search_service import CompanySearchService
    return CompanySearchService(max_results_cap=5000).explain(
        companies=companies,
        query=query,
        max_reasons_per_company=max_reasons_per_company,
    )


def index_company(company: Company) -> bool:
    """
    Индексирует или обновляет одну компанию в Typesense (HTTP API).
    company должен быть загружен с prefetch_related("phones", "emails", "contacts__phones", "contacts__emails", "notes", "tasks").
    Возвращает True при успехе, False при ошибке или недоступности Typesense.
    """
    collection = getattr(settings, "TYPESENSE_COLLECTION_COMPANIES", COLLECTION_NAME)
    try:
        doc = build_company_document(company)
        r = requests.post(
            f"{_typesense_base_url()}/collections/{collection}/documents",
            json=doc,
            headers=_typesense_headers(),
            timeout=_TIMEOUT,
        )
        return r.status_code in (200, 201)
    except Exception as e:
        logger.warning("Typesense index_company %s failed: %s", company.id, e)
        return False


def delete_company_from_index(company_id: UUID) -> bool:
    """Удаляет компанию из индекса Typesense (HTTP API). Возвращает True при успехе."""
    collection = getattr(settings, "TYPESENSE_COLLECTION_COMPANIES", COLLECTION_NAME)
    try:
        r = requests.delete(
            f"{_typesense_base_url()}/collections/{collection}/documents/{company_id}",
            headers=_typesense_headers(),
            timeout=_TIMEOUT,
        )
        return r.status_code in (200, 204)
    except Exception as e:
        logger.warning("Typesense delete_company %s failed: %s", company_id, e)
        return False
