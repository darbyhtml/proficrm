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


def _ellipsize(text: str, *, start: int, end: int, max_len: int) -> tuple[int, int, bool, bool]:
    """
    Обрезаем [start:end] в пределах max_len, стараясь оставить вокруг совпадения контекст.
    """
    if max_len <= 0 or len(text) <= max_len:
        return 0, len(text), False, False
    span = end - start
    if span >= max_len:
        # совпадение само длиннее лимита — режем по краям
        s = max(0, start)
        e = min(len(text), start + max_len)
        return s, e, s > 0, e < len(text)
    pad = (max_len - span) // 2
    s = max(0, start - pad)
    e = min(len(text), end + pad)
    # докинем в другую сторону, если упёрлись
    while e - s < max_len and (s > 0 or e < len(text)):
        if s > 0:
            s -= 1
        if e - s >= max_len:
            break
        if e < len(text):
            e += 1
    return s, e, s > 0, e < len(text)


def highlight_html(
    text: str,
    *,
    text_tokens: tuple[str, ...],
    digit_tokens: tuple[str, ...],
    max_matches: int = 2,
    max_len: int = 120,
) -> str:
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

    # ограничиваем количество подсветок, чтобы не “переподсвечивать” и не раздувать HTML
    if max_matches and len(ranges) > max_matches:
        ranges = ranges[:max_matches]

    # режем сниппет вокруг первого совпадения, чтобы UI был читабельным
    snip_s, snip_e, left_cut, right_cut = _ellipsize(s, start=ranges[0][0], end=ranges[0][1], max_len=max_len)
    if snip_s or snip_e != len(s):
        # сдвигаем ranges в координаты сниппета и отбрасываем не попавшие
        new_ranges: list[tuple[int, int]] = []
        for a, b in ranges:
            if b <= snip_s or a >= snip_e:
                continue
            new_ranges.append((max(0, a - snip_s), min(snip_e - snip_s, b - snip_s)))
        ranges = _merge_ranges(new_ranges)
        s = s[snip_s:snip_e]

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
    html = "".join(out)
    if left_cut:
        html = "…" + html
    if right_cut:
        html = html + "…"
    return html


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

        strong_digits = pq.strong_digit_tokens
        weak_digits = pq.weak_digit_tokens

        # AND по strong digit tokens
        for dt in strong_digits:
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

        # Если запрос состоит только из цифр, но они все “слишком слабые” (например "7" или "12"),
        # чтобы не вернуть “всё”, просто ничего не фильтруем → пустая выдача.
        if not pq.text_tokens and not strong_digits and not weak_digits and pq.raw:
            return qs.none()
        if not pq.text_tokens and not strong_digits and weak_digits and pq.raw:
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
        for dt in strong_digits:
            w = 2.0 if len(dt) >= 9 else 0.6
            digit_boost = digit_boost + Case(When(search_index__digits__contains=dt, then=Value(w)), default=Value(0.0), output_field=FloatField())
        for dt in weak_digits:
            # слабые цифры — только буст, НЕ фильтр
            digit_boost = digit_boost + Case(When(search_index__digits__contains=dt, then=Value(0.15)), default=Value(0.0), output_field=FloatField())

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
        for dt in pq.strong_digit_tokens:
            note_match_q |= Q(text__contains=dt)
            task_match_q |= Q(title__contains=dt) | Q(description__contains=dt)
        for dt in pq.weak_digit_tokens:
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

        # Индекс (plain_text) для fallback explainability
        from companies.models import CompanySearchIndex
        idx_map = {i.company_id: i for i in CompanySearchIndex.objects.filter(company_id__in=company_ids).only("company_id", "plain_text")}

        ORG_FORMS = {"ооо", "ип", "зао", "оао", "пао", "ао", "нко", "тк", "ооо."}

        def _tokens_hit(value: str) -> tuple[set[str], set[str]]:
            vf = fold_text(value or "")
            vd = only_digits(value or "")
            hit_t: set[str] = set()
            hit_d: set[str] = set()
            for t in pq.text_tokens:
                if t in vf:
                    hit_t.add(t)
            for d in pq.strong_digit_tokens + pq.weak_digit_tokens:
                if d and d in vd:
                    hit_d.add(d)
            return hit_t, hit_d

        @dataclass(frozen=True)
        class Candidate:
            field: str
            label: str
            value: str
            value_html: str
            hit_text: frozenset[str]
            hit_digits: frozenset[str]
            priority: int

        def _mk_candidate(field: str, label: str, value: str, priority: int) -> Candidate | None:
            if not value:
                return None
            ht, hd = _tokens_hit(value)
            if not ht and not hd:
                # токены могли “упасть” в индекс/FTS, но не быть подстрокой (особенно для коротких слов).
                # Для орг-форм разрешаем показывать причину даже без прямого попадания.
                if any(t in ORG_FORMS for t in pq.text_tokens):
                    pass
                else:
                    return None
            html = highlight_html(
                value,
                text_tokens=pq.text_tokens,
                digit_tokens=pq.strong_digit_tokens + pq.weak_digit_tokens,
                max_matches=2,
                max_len=120,
            )
            return Candidate(field=field, label=label, value=value, value_html=html, hit_text=frozenset(ht), hit_digits=frozenset(hd), priority=priority)

        out: dict[UUID, SearchExplain] = {}

        for c in companies:
            cands: list[Candidate] = []

            # Приоритет (меньше = важнее)
            def add(field: str, label: str, value: str, pr: int):
                cand = _mk_candidate(field, label, (value or "").strip(), pr)
                if cand:
                    cands.append(cand)

            add("company.inn", "ИНН", c.inn or "", 1)
            add("company.kpp", "КПП", c.kpp or "", 1)
            add("company.name", "Название", c.name or "", 2)
            add("company.legal_name", "Юр. название", c.legal_name or "", 2)

            add("company.phone", "Телефон (осн.)", c.phone or "", 3)
            add("company.email", "Email (осн.)", c.email or "", 3)

            for p in phones_by_company.get(c.id, [])[:20]:
                add("company.phones.value", "Телефон (доп.)", p.value or "", 3)
                if (p.comment or "").strip():
                    add("company.phones.comment", "Комментарий к телефону", p.comment, 4)

            for e in emails_by_company.get(c.id, [])[:20]:
                add("company.emails.value", "Email (доп.)", e.value or "", 3)

            for ct in contacts_by_company.get(c.id, [])[:50]:
                full_name = " ".join([ct.last_name or "", ct.first_name or ""]).strip()
                add("contact.name", "Контакт", full_name, 4)
                add("contact.position", "Должность", ct.position or "", 5)
                add("contact.note", "Примечание контакта", ct.note or "", 6)
                for p in cphones_by_contact.get(ct.id, [])[:20]:
                    add("contact.phones.value", "Телефон контакта", p.value or "", 4)
                    if (p.comment or "").strip():
                        add("contact.phones.comment", "Комментарий телефона контакта", p.comment, 6)
                for e in cemails_by_contact.get(ct.id, [])[:20]:
                    add("contact.emails.value", "Email контакта", e.value or "", 5)

            add("company.address", "Адрес", c.address or "", 6)
            add("company.website", "Сайт", c.website or "", 7)
            add("company.activity_kind", "Вид деятельности", c.activity_kind or "", 7)
            add("company.work_schedule", "График работы", c.work_schedule or "", 7)
            add("company.contact_name", "Контакт (ФИО) [в карточке]", c.contact_name or "", 5)
            add("company.contact_position", "Контакт (должность) [в карточке]", c.contact_position or "", 6)

            for n in notes_by_company.get(c.id, [])[:50]:
                add("company.notes.text", "Заметка", (n.text or "").strip(), 8)
                if (n.attachment_name or "").strip():
                    add("company.notes.attachment_name", "Файл (заметка)", n.attachment_name, 8)

            for t in tasks_by_company.get(c.id, [])[:50]:
                add("company.tasks.title", "Задача", (t.title or "").strip(), 8)
                add("company.tasks.description", "Описание задачи", (t.description or "").strip(), 9)

            # Fallback: если причин мало — добавляем причину из plain_text (как last resort explainability)
            if len(cands) < 2:
                idx = idx_map.get(c.id)
                if idx and (idx.plain_text or "").strip():
                    cand = _mk_candidate("search_index.plain_text", "Совпадение (прочее)", idx.plain_text, 20)
                    if cand:
                        cands.append(cand)

            # Выбор причин: не “AND в одном поле”, а покрытие токенов по полям (greedy set cover)
            needed_text = set(pq.text_tokens) - ORG_FORMS
            needed_digits = set(pq.strong_digit_tokens)  # weak digits не обязаны быть объяснены

            selected: list[Candidate] = []
            covered_t: set[str] = set()
            covered_d: set[str] = set()

            # сортируем по приоритету и “информативности”
            cands_sorted = sorted(
                cands,
                key=lambda x: (x.priority, -(len(x.hit_text) + len(x.hit_digits)), -len(x.value)),
            )

            def _pick_best():
                best = None
                best_gain = -1
                for cand in cands_sorted:
                    if cand in selected:
                        continue
                    gain = len((needed_text - covered_t) & set(cand.hit_text)) + len((needed_digits - covered_d) & set(cand.hit_digits))
                    if gain > best_gain:
                        best_gain = gain
                        best = cand
                return best, best_gain

            # гарантируем минимум 1 причину по text и 1 по digits (если они есть)
            while (needed_text - covered_t or needed_digits - covered_d) and len(selected) < 3:
                best, gain = _pick_best()
                if not best or gain <= 0:
                    break
                selected.append(best)
                covered_t |= set(best.hit_text)
                covered_d |= set(best.hit_digits)

            # если всё ещё пусто — берём top-1 (чтобы explain никогда не был пустым)
            if not selected and cands_sorted:
                selected.append(cands_sorted[0])

            # reasons_total: сколько всего валидных причин нашли, а не сколько показываем
            reasons_total = len(cands_sorted)
            # вернём чуть больше, чтобы UI мог показать “ещё N причин” корректно, но без раздувания
            selected_for_ui = selected + [c for c in cands_sorted if c not in selected][: max(0, min(max_reasons_per_company, 10) - len(selected))]

            name_html = highlight_html(c.name or "", text_tokens=pq.text_tokens, digit_tokens=pq.strong_digit_tokens + pq.weak_digit_tokens, max_matches=2, max_len=120)
            inn_html = highlight_html(c.inn or "", text_tokens=pq.text_tokens, digit_tokens=pq.strong_digit_tokens + pq.weak_digit_tokens, max_matches=2, max_len=120)
            address_html = highlight_html(c.address or "", text_tokens=pq.text_tokens, digit_tokens=pq.strong_digit_tokens + pq.weak_digit_tokens, max_matches=2, max_len=120)

            out[c.id] = SearchExplain(
                company_id=c.id,
                reasons=tuple(SearchReason(field=x.field, label=x.label, value=x.value, value_html=x.value_html) for x in selected_for_ui),
                reasons_total=reasons_total,
                name_html=name_html,
                inn_html=inn_html,
                address_html=address_html,
            )

        return out

