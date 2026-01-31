"""
Backend поиска компаний через Typesense.
Совместим с интерфейсом CompanySearchService: apply(qs, query), explain(companies, query).
Typesense v30+: synonym_sets (top-level), curation_sets, analytics.
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


def _get_client():
    """Создаёт клиент Typesense. При ошибке (нет модуля, неверные настройки) возвращает None."""
    try:
        import typesense
        return typesense.Client({
            "nodes": [{
                "host": settings.TYPESENSE_HOST,
                "port": str(settings.TYPESENSE_PORT),
                "protocol": settings.TYPESENSE_PROTOCOL,
            }],
            "api_key": settings.TYPESENSE_API_KEY,
            "connection_timeout_seconds": 5,
        })
    except Exception:
        return None


def _typesense_base_url() -> str:
    """Базовый URL Typesense для прямых HTTP-запросов (synonym_sets в v30)."""
    port = str(getattr(settings, "TYPESENSE_PORT", 8108))
    protocol = (getattr(settings, "TYPESENSE_PROTOCOL", "http") or "http").rstrip("://")
    host = getattr(settings, "TYPESENSE_HOST", "localhost")
    return f"{protocol}://{host}:{port}"


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


def ensure_collection(client) -> None:
    """Создаёт коллекцию, если её нет."""
    try:
        client.collections[COLLECTION_NAME].retrieve()
    except Exception:
        try:
            client.collections.create(SCHEMA)
        except Exception as e:
            logger.warning("Typesense: не удалось создать коллекцию %s: %s", COLLECTION_NAME, e)
    ensure_stopwords(client)


def ensure_stopwords(client) -> None:
    """Создаёт или обновляет набор стоп-слов для русского поиска (орг. формы и т.д.)."""
    body = {"stopwords": RUSSIAN_ORG_STOPWORDS, "locale": "ru"}
    try:
        if hasattr(client, "stopwords"):
            sw = client.stopwords
            if hasattr(sw, "upsert"):
                sw.upsert(STOPWORDS_SET_ID, body)
            elif hasattr(sw, "__getitem__"):
                sw[STOPWORDS_SET_ID].upsert(body)
            else:
                logger.debug("Typesense: API stopwords не найден")
        else:
            logger.debug("Typesense: клиент без stopwords")
    except Exception as e:
        logger.debug("Typesense: стоп-слова не заданы (%s): %s", STOPWORDS_SET_ID, e)


def ensure_synonyms(client, collection: str | None = None) -> int:
    """
    Создаёт или обновляет синонимы для коллекции компаний (Typesense v30: synonym_sets).
    PUT /synonym_sets/:name с items, затем PATCH коллекции — synonym_sets: [name].
    Возвращает количество групп в наборе при успехе, иначе 0.
    """
    coll = collection or getattr(settings, "TYPESENSE_COLLECTION_COMPANIES", COLLECTION_NAME)
    api_key = getattr(settings, "TYPESENSE_API_KEY", "") or ""
    base = _typesense_base_url()
    headers = {"X-TYPESENSE-API-KEY": api_key, "Content-Type": "application/json"}
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


class TypesenseSearchBackend:
    """
    Поиск компаний через Typesense.
    Интерфейс совместим с CompanySearchService: apply(qs, query), explain(companies, query).
    """

    def __init__(self, *, max_results_cap: int = 5000):
        self.max_results_cap = max_results_cap
        self._client = None

    def _client_or_none(self):
        if self._client is None:
            try:
                self._client = _get_client()
            except Exception as e:
                logger.warning("Typesense: клиент не создан: %s", e)
                return None
        return self._client

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
        # Слишком короткий запрос (1 символ) — не дергаем Typesense, используем fallback или пусто
        if len(q) < 2:
            if getattr(settings, "TYPESENSE_FALLBACK_TO_POSTGRES", True):
                return self._fallback_apply(qs, q)
            return qs.none()

        client = self._client_or_none()
        if not client:
            if getattr(settings, "TYPESENSE_FALLBACK_TO_POSTGRES", True):
                logger.info("Typesense недоступен — fallback на Postgres.")
                return self._fallback_apply(qs, q)
            return qs.none()

        collection = getattr(settings, "TYPESENSE_COLLECTION_COMPANIES", COLLECTION_NAME)
        try:
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
            }
            search_params["stopwords"] = STOPWORDS_SET_ID
            res = client.collections[collection].documents.search(search_params)
        except Exception as e:
            logger.warning("Typesense search failed: %s", e)
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

        client = self._client_or_none()
        if not client:
            return _fallback_explain(companies, query, max_reasons_per_company)

        collection = getattr(settings, "TYPESENSE_COLLECTION_COMPANIES", COLLECTION_NAME)
        ids_filter = ",".join(str(c.id) for c in companies)
        try:
            explain_params = {
                "q": q,
                "query_by": "name,legal_name,contacts,emails,phones,inn,kpp,address,website,notes",
                "filter_by": f"id:[{ids_filter}]",
                "prefix": "true",
                "num_typos": 2,
                "per_page": len(companies) + 10,
                "highlight_full_fields": "name,legal_name,contacts,emails,phones,address,website,notes",
                "synonym_sets": SYNONYM_SET_NAME,
            }
            explain_params["stopwords"] = STOPWORDS_SET_ID
            res = client.collections[collection].documents.search(explain_params)
        except Exception as e:
            logger.warning("Typesense explain search failed: %s", e)
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

        out = {}
        for c in companies:
            sid = str(c.id)
            highlights = highlight_by_id.get(sid) or []
            reasons = []
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
            for hl in highlights[:max_reasons_per_company]:
                field = hl.get("field") or ""
                # Typesense: value — полное поле с <mark>, snippet — обрезка; для массивов — values/snippets
                raw = (hl.get("value") or hl.get("snippet") or "").strip()
                if isinstance(raw, list):
                    raw = " ".join(str(x).strip() for x in raw if x)[:500]
                if not raw:
                    continue
                label = labels.get(field, field)
                plain = raw.replace("<mark>", "").replace("</mark>", "")
                reasons.append(
                    SearchReason(
                        field=field,
                        label=label,
                        value=plain[:200],
                        value_html=raw[:500],
                    )
                )
            if not reasons:
                reasons.append(
                    SearchReason(
                        field="",
                        label="Совпадение",
                        value=(c.name or "")[:80],
                        value_html=(c.name or "")[:80],
                    )
                )
            out[c.id] = SearchExplain(
                company_id=c.id,
                reasons=tuple(reasons),
                reasons_total=len(reasons),
                name_html=(c.name or ""),
                inn_html=(c.inn or ""),
                address_html=(c.address or ""),
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
    Индексирует или обновляет одну компанию в Typesense.
    company должен быть загружен с prefetch_related("phones", "emails", "contacts__phones", "contacts__emails", "notes", "tasks").
    Возвращает True при успехе, False при ошибке или недоступности Typesense.
    """
    client = _get_client()
    if not client:
        return False
    try:
        from django.conf import settings as s
        collection = getattr(s, "TYPESENSE_COLLECTION_COMPANIES", COLLECTION_NAME)
        ensure_collection(client)
        doc = build_company_document(company)
        client.collections[collection].documents.upsert(doc)
        return True
    except Exception as e:
        logger.warning("Typesense index_company %s failed: %s", company.id, e)
        return False


def delete_company_from_index(company_id: UUID) -> bool:
    """Удаляет компанию из индекса Typesense. Возвращает True при успехе."""
    client = _get_client()
    if not client:
        return False
    try:
        from django.conf import settings as s
        collection = getattr(s, "TYPESENSE_COLLECTION_COMPANIES", COLLECTION_NAME)
        client.collections[collection].documents[str(company_id)].delete()
        return True
    except Exception as e:
        logger.warning("Typesense delete_company %s failed: %s", company_id, e)
        return False
