from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from django.db import connection
from django.db.models import Case, F, FloatField, Q, Value, When
from django.db.models.functions import Coalesce
from django.contrib.postgres.search import SearchQuery, SearchRank
from django.utils.html import escape

from companies.models import Company, CompanyEmail, CompanyNote, CompanyPhone, Contact, ContactEmail, ContactPhone
from tasksapp.models import Task

from .search_index import ParsedQuery, parse_query, fold_text, only_digits


def _merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if not ranges:
        return []
    ranges = sorted(ranges, key=lambda x: (x[0], x[1]))
    out = [ranges[0]]
    for s, e in ranges[1:]:
        ps, pe = out[-1]
        if s <= pe:
            out[-1] = (ps, max(pe, e))
        else:
            out.append((s, e))
    return out


def _find_ranges_text(haystack: str, token: str) -> list[tuple[int, int]]:
    """
    Ищем token в haystack, регистронезависимо и с ё≈е.
    Возвращаем диапазоны по исходной строке haystack.
    """
    if not haystack or not token:
        return []
    h = str(haystack)
    t = fold_text(token)
    if not t:
        return []
    # простая линейная проверка (поиск по нормализованному представлению)
    h_fold = fold_text(h)
    ranges: list[tuple[int, int]] = []
    start = 0
    while True:
        idx = h_fold.find(t, start)
        if idx < 0:
            break
        # idx в fold-строке совпадает с idx в исходной строке по длине (мы не удаляем символы, только lower/ё→е/пробелы схлопываем),
        # но пробелы могли схлопнуться → безопаснее подсвечивать по “грубому” поиску в оригинале.
        # Поэтому берём окно и находим в оригинале casefold-поиск.
        # fallback: подсветим первую найденную подстроку в оригинале (case-insensitive, ё≈е) по длине токена.
        # Для стабильности — сканируем оригинал.
        # (это O(n*m) на небольшой строке; выполняется только для текущей страницы результатов)
        found = False
        for j in range(max(0, idx - 5), min(len(h), idx + 5) + 1):
            seg = h[j : j + len(token)]
            if fold_text(seg) == t:
                ranges.append((j, j + len(seg)))
                found = True
                break
        if not found:
            # “примерная” подсветка в fold координатах (может быть неточно при множественных пробелах)
            ranges.append((idx, idx + len(t)))
        start = idx + len(t)
    return _merge_ranges(ranges)


def _find_ranges_digits(raw_value: str, digit_token: str) -> list[tuple[int, int]]:
    """
    Подсветка цифр в строке, где могут быть +()-пробелы.
    Маппим позицию в digits-строке обратно в оригинал.
    """
    if not raw_value or not digit_token:
        return []
    src = str(raw_value)
    digits = only_digits(src)
    tok = only_digits(digit_token)
    if not digits or not tok:
        return []
    idx = digits.find(tok)
    if idx < 0:
        return []
    # строим маппинг digits_pos -> orig_pos
    mapping: list[int] = []
    for i, ch in enumerate(src):
        if ch.isdigit():
            mapping.append(i)
    if idx + len(tok) - 1 >= len(mapping):
        return []
    start = mapping[idx]
    end = mapping[idx + len(tok) - 1] + 1
    return [(start, end)]


def highlight_html(text: str, *, text_tokens: tuple[str, ...], digit_tokens: tuple[str, ...]) -> str:
    """
    Возвращает безопасный HTML, подсвечивая совпадения классом search-highlight.
    Подсветка выполняется по “плоскому” тексту (НЕ по innerHTML на фронте).
    """
    s = "" if text is None else str(text)
    ranges: list[tuple[int, int]] = []
    for t in text_tokens:
        ranges.extend(_find_ranges_text(s, t))
    for d in digit_tokens:
        ranges.extend(_find_ranges_digits(s, d))
    ranges = _merge_ranges(ranges)
    if not ranges:
        return escape(s)

    out: list[str] = []
    pos = 0
    for a, b in ranges:
        a = max(0, min(len(s), a))
        b = max(0, min(len(s), b))
        if b <= a:
            continue
        out.append(escape(s[pos:a]))
        out.append(f'<span class="search-highlight">{escape(s[a:b])}</span>')
        pos = b
    out.append(escape(s[pos:]))
    return "".join(out)


@dataclass(frozen=True)
class SearchReason:
    field: str
    label: str
    value: str
    value_html: str


@dataclass(frozen=True)
class SearchExplain:
    company_id: UUID
    reasons: tuple[SearchReason, ...]
    reasons_total: int
    name_html: str
    inn_html: str
    address_html: str


class CompanySearchService:
    """
    Единый сервис поиска компаний:
    - быстро: FTS (GIN) + pg_trgm (GIN) + digits blob
    - точно: AND по токенам + ранжирование (идентификаторы > название > контакты > прочее)
    - полно: ищем по индексированной “карточке” (CompanySearchIndex)
    - объяснимо: для результата формируем match_reasons и подсвечиваем их детерминированно
    """

    def __init__(self, *, max_results_cap: int = 5000):
        self.max_results_cap = max_results_cap

    def apply(self, *, qs, query: str):
        """
        Возвращает queryset компаний, отфильтрованный и (по умолчанию) отсортированный по релевантности.
        """
        if connection.vendor != "postgresql":
            # fallback: старый icontains (проект уже его использует)
            q = (query or "").strip()
            if not q:
                return qs
            return qs.filter(
                Q(name__icontains=q)
                | Q(inn__icontains=q)
                | Q(kpp__icontains=q)
                | Q(legal_name__icontains=q)
                | Q(address__icontains=q)
                | Q(phone__icontains=q)
                | Q(email__icontains=q)
                | Q(contact_name__icontains=q)
                | Q(contact_position__icontains=q)
            )

        pq: ParsedQuery = parse_query(query)
        if not pq.raw:
            return qs

        indexed = Q(search_index__isnull=False)
        indexed_match = Q()
        fallback_match = Q()

        # AND по digit tokens
        for dt in pq.digit_tokens:
            indexed_match &= Q(search_index__digits__contains=dt)
            # fallback по основным числовым полям (без сканирования по связанным таблицам)
            fallback_match &= (
                Q(inn__contains=dt)
                | Q(kpp__contains=dt)
                | Q(phone__contains=dt)
            )

        # AND по text tokens (fts на индексе, icontains на fallback)
        if pq.text_tokens:
            tsq = SearchQuery(" ".join(pq.text_tokens), search_type="plain", config="russian")
            indexed_match &= (
                Q(search_index__vector_a=tsq)
                | Q(search_index__vector_b=tsq)
                | Q(search_index__vector_c=tsq)
                | Q(search_index__vector_d=tsq)
            )
            for tok in pq.text_tokens:
                fallback_match &= (
                    Q(name__icontains=tok)
                    | Q(legal_name__icontains=tok)
                    | Q(address__icontains=tok)
                    | Q(email__icontains=tok)
                    | Q(contact_name__icontains=tok)
                    | Q(contact_position__icontains=tok)
                )
        else:
            tsq = None

        # Если запрос состоит только из цифр, а digit_tokens отфильтровались (например "12"),
        # чтобы не вернуть “всё”, просто ничего не фильтруем → пустая выдача.
        if not pq.text_tokens and not pq.digit_tokens and pq.raw:
            return qs.none()

        qs = qs.filter((indexed & indexed_match) | (Q(search_index__isnull=True) & fallback_match))

        # Ранжирование (важность)
        score = Value(0.0, output_field=FloatField())
        if tsq is not None:
            rank_a = SearchRank(F("search_index__vector_a"), tsq)
            rank_b = SearchRank(F("search_index__vector_b"), tsq)
            rank_c = SearchRank(F("search_index__vector_c"), tsq)
            rank_d = SearchRank(F("search_index__vector_d"), tsq)
            score = (
                Coalesce(rank_a, 0.0) * Value(10.0)
                + Coalesce(rank_b, 0.0) * Value(5.0)
                + Coalesce(rank_c, 0.0) * Value(2.0)
                + Coalesce(rank_d, 0.0) * Value(1.0)
            )

        digit_boost = Value(0.0, output_field=FloatField())
        for dt in pq.digit_tokens:
            w = 3.0 if len(dt) >= 9 else 0.7
            digit_boost = digit_boost + Case(
                When(search_index__digits__contains=dt, then=Value(w)),
                default=Value(0.0),
                output_field=FloatField(),
            )

        qs = qs.annotate(
            search_score=Case(
                When(search_index__isnull=False, then=score + digit_boost),
                default=Value(0.0),
                output_field=FloatField(),
            )
        )

        return qs.order_by("-search_score", "-updated_at")

    def explain(self, *, companies: list[Company], query: str, max_reasons_per_company: int = 50) -> dict[UUID, SearchExplain]:
        """
        Формирует match_reasons + готовые HTML-сниппеты для UI (без JS-regex по innerHTML).
        Делает O(1) запросов по связанным таблицам на страницу результатов.
        """
        pq = parse_query(query)
        if not pq.raw or not companies:
            return {}

        company_ids = [c.id for c in companies]

        # bulk загрузка связанных значений (без N+1)
        phones = list(CompanyPhone.objects.filter(company_id__in=company_ids).only("company_id", "value", "comment"))
        emails = list(CompanyEmail.objects.filter(company_id__in=company_ids).only("company_id", "value"))

        contacts = list(Contact.objects.filter(company_id__in=company_ids).only("id", "company_id", "first_name", "last_name", "position", "note"))
        contact_ids = [c.id for c in contacts]
        cphones = list(ContactPhone.objects.filter(contact_id__in=contact_ids).only("contact_id", "value", "comment"))
        cemails = list(ContactEmail.objects.filter(contact_id__in=contact_ids).only("contact_id", "value"))

        # Заметки/задачи могут быть большими по объёму → забираем только потенциально релевантные записи (OR по токенам).
        note_match_q = Q()
        task_match_q = Q()
        for tok in pq.text_tokens:
            note_match_q |= Q(text__icontains=tok) | Q(attachment_name__icontains=tok)
            task_match_q |= Q(title__icontains=tok) | Q(description__icontains=tok)
        for dt in pq.digit_tokens:
            note_match_q |= Q(text__contains=dt)
            task_match_q |= Q(title__contains=dt) | Q(description__contains=dt)

        notes_qs = CompanyNote.objects.filter(company_id__in=company_ids).only("company_id", "text", "attachment_name")
        tasks_qs = Task.objects.filter(company_id__in=company_ids).only("company_id", "title", "description")
        if note_match_q:
            notes_qs = notes_qs.filter(note_match_q)
        if task_match_q:
            tasks_qs = tasks_qs.filter(task_match_q)

        notes = list(notes_qs.order_by("-created_at"))
        tasks = list(tasks_qs.order_by("-created_at"))

        phones_by_company: dict[UUID, list[CompanyPhone]] = {}
        for p in phones:
            phones_by_company.setdefault(p.company_id, []).append(p)

        emails_by_company: dict[UUID, list[CompanyEmail]] = {}
        for e in emails:
            emails_by_company.setdefault(e.company_id, []).append(e)

        contacts_by_company: dict[UUID, list[Contact]] = {}
        for c in contacts:
            if not c.company_id:
                continue
            contacts_by_company.setdefault(c.company_id, []).append(c)

        cphones_by_contact: dict[UUID, list[ContactPhone]] = {}
        for p in cphones:
            cphones_by_contact.setdefault(p.contact_id, []).append(p)

        cemails_by_contact: dict[UUID, list[ContactEmail]] = {}
        for e in cemails:
            cemails_by_contact.setdefault(e.contact_id, []).append(e)

        notes_by_company: dict[UUID, list[CompanyNote]] = {}
        for n in notes:
            notes_by_company.setdefault(n.company_id, []).append(n)

        tasks_by_company: dict[UUID, list[Task]] = {}
        for t in tasks:
            if not t.company_id:
                continue
            tasks_by_company.setdefault(t.company_id, []).append(t)

        out: dict[UUID, SearchExplain] = {}

        for c in companies:
            reasons: list[SearchReason] = []

            def add_reason(field: str, label: str, value: str):
                if not value:
                    return
                value_html = highlight_html(value, text_tokens=pq.text_tokens, digit_tokens=pq.digit_tokens)
                reasons.append(SearchReason(field=field, label=label, value=value, value_html=value_html))

            # Важность: ИНН/КПП → Название → Контакты → Коммуникации → Адрес → Заметки/Задачи → Прочее
            add_reason("company.inn", "ИНН", c.inn or "")
            add_reason("company.kpp", "КПП", c.kpp or "")
            add_reason("company.name", "Название", c.name or "")
            add_reason("company.legal_name", "Юр. название", c.legal_name or "")

            # Коммуникации компании
            add_reason("company.phone", "Телефон (осн.)", c.phone or "")
            add_reason("company.email", "Email (осн.)", c.email or "")

            for p in phones_by_company.get(c.id, [])[:20]:
                add_reason("company.phones.value", "Телефон (доп.)", p.value or "")
                if (p.comment or "").strip():
                    add_reason("company.phones.comment", "Комментарий к телефону", p.comment)

            for e in emails_by_company.get(c.id, [])[:20]:
                add_reason("company.emails.value", "Email (доп.)", e.value or "")

            # Контакты
            for ct in contacts_by_company.get(c.id, [])[:50]:
                full_name = " ".join([ct.last_name or "", ct.first_name or ""]).strip()
                add_reason("contact.name", "Контакт", full_name)
                add_reason("contact.position", "Должность", ct.position or "")
                add_reason("contact.note", "Примечание контакта", ct.note or "")
                for p in cphones_by_contact.get(ct.id, [])[:20]:
                    add_reason("contact.phones.value", "Телефон контакта", p.value or "")
                    if (p.comment or "").strip():
                        add_reason("contact.phones.comment", "Комментарий телефона контакта", p.comment)
                for e in cemails_by_contact.get(ct.id, [])[:20]:
                    add_reason("contact.emails.value", "Email контакта", e.value or "")

            # Адрес/прочие поля
            add_reason("company.address", "Адрес", c.address or "")
            add_reason("company.website", "Сайт", c.website or "")
            add_reason("company.activity_kind", "Вид деятельности", c.activity_kind or "")
            add_reason("company.work_schedule", "График работы", c.work_schedule or "")
            add_reason("company.contact_name", "Контакт (ФИО) [в карточке]", c.contact_name or "")
            add_reason("company.contact_position", "Контакт (должность) [в карточке]", c.contact_position or "")

            for n in notes_by_company.get(c.id, [])[:50]:
                add_reason("company.notes.text", "Заметка", (n.text or "").strip())
                if (n.attachment_name or "").strip():
                    add_reason("company.notes.attachment_name", "Файл (заметка)", n.attachment_name)

            for t in tasks_by_company.get(c.id, [])[:50]:
                add_reason("company.tasks.title", "Задача", (t.title or "").strip())
                add_reason("company.tasks.description", "Описание задачи", (t.description or "").strip())

            # Фильтруем причины так, чтобы каждая реально “попадала” в запрос (иначе будем показывать мусор).
            filtered: list[SearchReason] = []
            for r in reasons:
                val_fold = fold_text(r.value)
                ok = True
                for tok in pq.text_tokens:
                    if tok not in val_fold:
                        ok = False
                        break
                for dt in pq.digit_tokens:
                    if dt and dt not in only_digits(r.value):
                        ok = False
                        break
                if ok:
                    filtered.append(r)
                if len(filtered) >= max_reasons_per_company:
                    break

            # Если строгая фильтрация убрала всё (например, токены распределены по разным полям) —
            # оставляем хотя бы “лучшие” причины (для explainability).
            final = filtered if filtered else reasons[:10]

            name_html = highlight_html(c.name or "", text_tokens=pq.text_tokens, digit_tokens=pq.digit_tokens)
            inn_html = highlight_html(c.inn or "", text_tokens=pq.text_tokens, digit_tokens=pq.digit_tokens)
            address_html = highlight_html(c.address or "", text_tokens=pq.text_tokens, digit_tokens=pq.digit_tokens)

            out[c.id] = SearchExplain(
                company_id=c.id,
                reasons=tuple(final),
                reasons_total=len(final),
                name_html=name_html,
                inn_html=inn_html,
                address_html=address_html,
            )

        return out

