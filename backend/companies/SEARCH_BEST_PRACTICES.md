# Лучшие практики поиска по компаниям в CRM

Документ привязывает описанные в спецификации практики к текущей реализации в коде.

Поиск компаний **полностью реализован на PostgreSQL** (FTS + pg_trgm) через
`CompanySearchService` и модель `CompanySearchIndex`. Внешний движок Typesense
отключён и больше не используется.

Ключевые свойства текущего поиска:

- **EXACT-first**: при вводе email / полного телефона / ИНН сначала выполняется точный поиск
  по денормализованным полям индекса (`normalized_phones`, `normalized_emails`, `normalized_inns`)
  без тяжёлых JOIN, с сортировкой по `updated_at desc`.
- **Денормализованные поля для exact-поиска**: `CompanySearchIndex` содержит массивы нормализованных
  телефонов, email и ИНН из всех связанных сущностей (Company, CompanyPhone, CompanyEmail, ContactPhone, ContactEmail),
  что позволяет выполнять быстрый exact-поиск через GIN индексы без JOIN.
- **Защита от коротких запросов**: запросы длиной < 3 символов (не exact-типа) не запускают heavy FTS/trigram поиск.
- **Улучшенная glued-нормализация**: склейка токенов применяется только для строк длиной >= 6 символов
  и не применяется для строк, состоящих только из ОПФ (ооо, ип и т.д.), чтобы снизить шум.
- **Текстовый поиск (2 фазы)**: после exact-first для текстовых запросов применяются: (1) стоп-токены (ооо, ип, ул, дом и т.п.) — если после их удаления 0 значимых токенов, выдача пуста; (2) FTS по значимым токенам с AND-логикой (plainto_tsquery); (3) score_boost по точному/фразовому совпадению в plain_text и t_name (без JOIN); (4) поле-зависимый буст (сайт → t_other, ФИО → t_contacts).
- **Ранжирование текста**: название (vector_b) весит выше контактов (vector_c) и прочего (vector_d). Веса: название 10, идентификаторы 5, контакты 3, прочее 1. Итоговый score = score_boost + ts_rank-веса + digit_boost.
- **Команды обслуживания**:
  - `python manage.py normalize_companies_data` — ночная «гигиена» данных (телефоны → `normalize_phone`, email → `lower().strip()`).
  - `python manage.py rebuild_company_search_index` — полная переиндексация `CompanySearchIndex` (включая денормализованные поля).
- **Настройки окружения**:
  - `SEARCH_ENGINE_BACKEND=postgres` — явное указание, что используется PostgreSQL FTS.
- **Тюнинг весов**: в `search_service.py` — константы score_boost (100000/50000/20000/15000), веса ранжирования (10/5/3/1 для vector_b/a/c/d). Стоп-токены — `_SEARCH_STOP_TOKENS` в `search_index.py`.
- **Phase 1.5**: при точном/фразовом совпадении запроса с plain_text или t_name, если найдено ≤ N записей (по умолчанию 20), возвращаются только они, без FTS-хвоста.
- **Quality cutoff**: после ранжирования отсекаются результаты с score ниже порога (max(ABS_MIN_SCORE, top_score × RELATIVE_MIN_FACTOR); при top_score ≥ 50000 — порог top_score × 0.5). Не применяется к exact-first (телефон/email/ИНН).
- **Similarity fallback**: pg_trgm по name и t_name включается только когда FTS дал мало результатов (< 5), порог — из `SEARCH_TEXT_SIMILARITY_THRESHOLD` (по умолчанию 0.4).
- **Параметры через env** (опционально, дефолты не требуют .env): `SEARCH_TEXT_ABS_MIN_SCORE`, `SEARCH_TEXT_RELATIVE_MIN_FACTOR`, `SEARCH_TEXT_EXACT_CUTOFF_LIMIT`, `SEARCH_TEXT_SIMILARITY_ONLY_IF_FTS_EMPTY`, `SEARCH_TEXT_SIMILARITY_THRESHOLD` — см. раздел «Тюнинг текстового поиска» ниже.

---

## 1. Нормализация данных и регистронезависимый поиск

| Практика | Реализация |
|----------|------------|
| lower + ё→е + схлопывание пробелов | `companies/search_index.py`: `fold_text()` — lower, замена "ё"→"е", `_WS_RE.sub(" ", s).strip()` |
| Только цифры из строки (телефоны, ИНН) | `companies/search_index.py`: `only_digits()` — `_DIGITS_RE.sub("", str(s))` |
| Игнорирование диакритики (unaccent) | Миграция `0040_company_search_index.py`: триггер заполняет tsvector через `unaccent(coalesce(NEW.t_ident, ''))` и т.д. |

Текстовые группы индекса (`t_ident`, `t_name`, `t_contacts`, `t_other`) и запросы нормализуются через `fold_text()` и `parse_query()` перед поиском.

---

## 2. Поиск по email

| Практика | Реализация |
|----------|------------|
| Регистронезависимость | Индексы на `Upper(email)` / `Upper(value)`; в индексе — `fold_text()` (lower). |
| GIN триграммный индекс по email | `Company`: `cmp_email_trgm_gin_idx`; `CompanyEmail`, `ContactEmail`: GIN по `value`. |
| Поиск по всем email (компания + контакты) | `search_index.py`: в `t_other` попадают `email_осн`, `email_компании`, `email_контакта`; поиск идёт по единому индексу. |
| **EXACT-поиск через денормализованное поле** | `CompanySearchIndex.normalized_emails` (ArrayField) содержит все email из Company.email, CompanyEmail.value, ContactEmail.value (normalized: lower().strip()). Поиск через `search_index__normalized_emails__contains=[email]` с GIN индексом `cmp_si_nemails_gin_idx` — без JOIN. |

---

## 3. Поиск по ФИО

| Практика | Реализация |
|----------|------------|
| Частичный поиск по фамилии/имени | GIN триграммные индексы: `Contact` — `ct_first_trgm_gin_idx`, `ct_last_trgm_gin_idx`. |
| Полное ФИО в индексе | `build_company_index_payload()`: строка вида `контакт: {last_name} {first_name}` в `t_contacts`. |
| Русская морфология (склонения) | FTS с `config='russian'` в триггере и в `SearchQuery(..., config="russian")`. |
| Нечёткий поиск (опечатки) | `CompanySearchService.apply()`: fallback по `TrigramWordSimilarity` по `name` и `search_index__t_name` с порогом `SIMILARITY_THRESHOLD = 0.3`, только для токенов длины ≥ `SIMILARITY_MIN_TOKEN_LEN` (3). |
| Веса ранжирования | Векторы A/B/C/D: контакты в vector_c; ранжирование через `SearchRank` с весами 10, 5, 2, 1. |

---

## 4. Поиск по названию компании

| Практика | Реализация |
|----------|------------|
| AND по словам (минимум шума) | `SearchQuery(" ".join(pq.text_tokens), search_type="plain", config="russian")` и AND по vector_a..d. |
| Игнорирование ООО/ЗАО/ИП и т.д. | `search_service.py`: `ORG_FORMS` — при формировании причин совпадения эти токены не требуют объяснения; в FTS стоп-слова русского словаря. |
| Опечатки (триграммы) | GIN по `Upper("name")`, `Upper("plain_text")`; similarity fallback по самому длинному токену запроса. |
| Порог схожести | `SIMILARITY_THRESHOLD = 0.3`; fallback только для токенов длины ≥ 3. |

---

## 5. Поиск по ИНН и КПП

| Практика | Реализация |
|----------|------------|
| Сильные (≥4 цифр) vs слабые (2–3) токены | `parse_query()`: `strong_digit_tokens` и `weak_digit_tokens`; только strong участвуют в AND-фильтрации. |
| Индексы | `Company`: btree по `inn`; GIN trgm по `inn` и по `kpp` (миграция 0042). `CompanySearchIndex.digits` — все цифры карточки, GIN trgm по `digits`. |
| **EXACT-поиск через денормализованное поле** | `CompanySearchIndex.normalized_inns` (ArrayField) содержит все валидные ИНН (10/12 цифр) из Company.inn (распарсенные списки через запятую/слеш). Поиск через `search_index__normalized_inns__contains=[inn_digits]` с GIN индексом `cmp_si_ninns_gin_idx` — без JOIN и без итерации по записям. |
| Приоритет в ранжировании | ИНН/КПП в vector_a (вес A); digit_boost: +2.0 за совпадение последовательности ≥9 цифр, +0.6 за shorter. |

---

## 6. Поиск по телефонам

| Практика | Реализация |
|----------|------------|
| Единый формат (E.164) | Нормализация при сохранении: `ui/forms._normalize_phone`, `_normalize_phone_for_search` в views. |
| Агрегат цифр для поиска | `CompanySearchIndex.digits` — цифры из inn, kpp, phone, CompanyPhone, ContactPhone; поиск по `search_index__digits__contains=dt`. |
| **EXACT-поиск через денормализованное поле** | `CompanySearchIndex.normalized_phones` (ArrayField) содержит все телефоны из Company.phone, CompanyPhone.value, ContactPhone.value (normalized: `normalize_phone()` → E.164 формат `+7XXXXXXXXXX`). Поиск через `search_index__normalized_phones__contains=[phone_norm]` с GIN индексом `cmp_si_nphones_gin_idx` — без JOIN. |
| Защита от коротких запросов | Запрос только из слабых цифр (2–3) без текста → пустая выдача (`qs.none()`). Также защита от запросов < 3 символов (не exact-типа). |
| Подсветка в номере | `_find_ranges_digits()` + `highlight_html()` — маппинг позиций цифр в исходную строку. |

---

## 7. Релевантное ранжирование

| Практика | Реализация |
|----------|------------|
| Точные совпадения выше | Веса A>B>C>D; digit_boost за совпадение цифр. |
| Сортировка | `order_by("-search_score", "-updated_at")`. |
| Ограничение выдачи | `max_results_cap=5000` в `CompanySearchService`. |

---

## 8. Денормализованные поля для exact-поиска

Для ускорения exact-поиска по email/телефонам/ИНН без тяжёлых JOIN в `CompanySearchIndex` добавлены массивы:

- **`normalized_phones`** (ArrayField): все телефоны из `Company.phone`, `CompanyPhone.value`, `ContactPhone.value`, нормализованные через `normalize_phone()` в формат E.164 (`+7XXXXXXXXXX`).
- **`normalized_emails`** (ArrayField): все email из `Company.email`, `CompanyEmail.value`, `ContactEmail.value`, нормализованные через `lower().strip()`.
- **`normalized_inns`** (ArrayField): все валидные ИНН (10/12 цифр) из `Company.inn`, распарсенные из списков (запятая/слеш).

Поля заполняются в `build_company_index_payload()` и пересобираются при выполнении `rebuild_company_search_index`. GIN индексы (`cmp_si_nphones_gin_idx`, `cmp_si_nemails_gin_idx`, `cmp_si_ninns_gin_idx`) обеспечивают быстрый поиск через оператор `contains` без JOIN.

---

## 9. Индексы и технологии PostgreSQL

| Технология | Использование |
|------------|----------------|
| **pg_trgm** | Расширение включено (0033, 0040). GIN с `gin_trgm_ops` на name, legal_name, address, inn, kpp, phone, email (Company); plain_text, digits (CompanySearchIndex); value (CompanyPhone, CompanyEmail, ContactPhone, ContactEmail); first_name, last_name (Contact). |
| **Full-Text Search** | `CompanySearchIndex.vector_a..d` — tsvector, заполняются триггером с `to_tsvector('russian', unaccent(...))`, веса A/B/C/D. |
| **unaccent** | В триггере миграции 0040 при построении tsvector. |

---

## 9. Подсветка совпадений

| Практика | Реализация |
|----------|------------|
| Точный фрагмент, регистронезависимо + ё≈е | `_find_ranges_text()`: поиск по нормализованной строке, подсветка в оригинале; `highlight_html()`. |
| Ограничение числа подсветок | `max_matches=2` в `highlight_html()`. |
| Причины совпадения (поле) | `explain()` формирует `SearchReason` с `field`, `label`, `value`, `value_html` для UI. |

---

## 10. Отсечение шума

| Практика | Реализация |
|----------|------------|
| Минимальная длина/значимость запроса | Токены длины 1 отбрасываются в `parse_query()`; только слабые цифры без текста → пустая выдача. |
| **Стоп-токены текстового поиска** | `filter_stop_tokens()` в `search_index.py`: ооо, ип, ао, зао, ул, дом, офис, кв, корп, компания и т.п. Если после удаления 0 значимых токенов → пустая выдача. Запрос «ооо ромашка» ищет по «ромашка». |
| Стоп-слова FTS | FTS config `russian`; ORG_FORMS при объяснении. |
| Fuzzy только для длинных токенов | Similarity fallback только для значимых токенов `len(t) >= SIMILARITY_MIN_TOKEN_LEN` (3). |
| Порог схожести | `sim__gt=SEARCH_TEXT_SIMILARITY_THRESHOLD` (по умолчанию 0.4). Similarity только по name и t_name; включается только при пустом/малом FTS, если SEARCH_TEXT_SIMILARITY_ONLY_IF_FTS_EMPTY=1. |

---

## 11. Текстовый поиск: порядок фаз и уменьшение мусора

1. **Exact-first** (без изменений): телефон / email / ИНН — по денормализованным полям, без FTS.
2. **Короткие запросы**: < 3 символа (не exact) → пустая выдача.
3. **Стоп-токены**: удаление стоп-токенов; 0 значимых → пустая выдача.
4. **Классификация типа запроса** (`classify_text_query`): website / person / address / company_name_or_general — для поле-зависимого буста (сайт/ФИО выше в релевантности).
5. **FTS по значимым токенам**: plainto_tsquery (AND по словам) по vector_a..d; fallback по основным полям Company (icontains по каждому токену).
6. **Score_boost** (только по полям индекса): exact plain_text +100000, phrase in plain_text +50000, t_name contains +20000; при типе website — t_other +20000; при типе person — t_contacts +15000.
7. **Phase 1.5 (текстовый exact)**: если fold_text(query) совпадает с plain_text (iexact) или входит в plain_text/t_name (icontains) и таких записей ≤ EXACT_CUTOFF_LIMIT (20) — возвращаются только они, сортировка по updated_at. Только для текстовых запросов.
8. **Ранжирование**: score_boost + 10×rank_b + 5×rank_a + 3×rank_c + 1×rank_d + digit_boost. Название (b) побеждает контакты (c) и прочее (d).
9. **Quality cutoff**: отсечка по score (см. выше). Только для текстового пути; exact-first без отсечки.
10. **Similarity**: только по name и t_name, только если FTS дал < 5 результатов (при SEARCH_TEXT_SIMILARITY_ONLY_IF_FTS_EMPTY=1).

---

## Тюнинг текстового поиска (env)

| Переменная | Описание | Дефолт |
|------------|----------|--------|
| SEARCH_TEXT_ABS_MIN_SCORE | Минимальный score для попадания в выдачу (абсолютный порог). | 0.5 |
| SEARCH_TEXT_RELATIVE_MIN_FACTOR | Доля от top_score (0..1): результаты с score < top_score × factor отсекаются. | 0.15 |
| SEARCH_TEXT_EXACT_CUTOFF_LIMIT | При точном/фразовом совпадении по plain_text/t_name, если найдено ≤ N записей — вернуть только их. | 20 |
| SEARCH_TEXT_SIMILARITY_ONLY_IF_FTS_EMPTY | 1/true — включать pg_trgm similarity только когда FTS дал < 5 результатов. | 1 |
| SEARCH_TEXT_SIMILARITY_THRESHOLD | Порог TrigramWordSimilarity для name/t_name (0.3–0.5). | 0.4 |

Ужесточение выдачи: увеличить RELATIVE_MIN_FACTOR (например 0.2–0.3) или ABS_MIN_SCORE. Ослабить: уменьшить RELATIVE_MIN_FACTOR. Увеличить «точный» порог: увеличить EXACT_CUTOFF_LIMIT.

---

## Файлы реализации

- **Поиск и ранжирование**: `companies/search_service.py` — `CompanySearchService.apply()`, `explain()`, `highlight_html()`, пороги.
- **Нормализация и индекс**: `companies/search_index.py` — `fold_text()`, `only_digits()`, `parse_query()`, `build_company_index_payload()`, `rebuild_company_search_index()`.
- **Модели и индексы**: `companies/models.py` — `Company`, `CompanySearchIndex`, Meta.indexes.
- **Миграции**: `0033_add_search_indexes.py` (GIN trgm), `0040_company_search_index.py` (индекс, триггер, unaccent), `0042_company_kpp_trgm_index.py` (GIN по КПП).
- **Перестроение индекса**: `companies/management/commands/rebuild_company_search_index.py`.

При доработках поиска ориентируйтесь на эти файлы и таблицы выше.
