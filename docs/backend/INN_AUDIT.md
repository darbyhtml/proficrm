# Полный аудит: ИНН (8–12 цифр, в т.ч. 9)

Проверено: все места, где используется ИНН, парсинг или проверка длины (10/12 → 8–12).

---

## 1. Ядро: парсинг и нормализация

| Файл | Статус | Описание |
|------|--------|----------|
| **companies/inn_utils.py** | ✅ | `parse_inns`: regex 10/12 + извлечение по длине 10/12 из digits_only + **fallback 8–12 цифр** (одно значение). `normalize_inn_string`, `merge_inn_strings` используют `parse_inns`. |
| **companies/normalizers.py** | ✅ | `normalize_inn` вызывает `_normalize_inn_string` (inn_utils) — единая точка нормализации. |

---

## 2. Сохранение и модели

| Файл | Статус | Описание |
|------|--------|----------|
| **companies/models.py** | ✅ | `Company.inn` — CharField 255. В `save()` вызывается `normalize_inn(self.inn)[:255]`. Комментарий и help_text у `normalized_inns`: «8–12», «fallback 8–12». |
| **ui/forms.py** | ✅ | `CompanyForm`, `CompanyEditForm`, `CompanyInlineEditForm`: `clean_inn` → `normalize_inn(inn)`. Без проверки длины 10/12. |
| **companies/api.py** | ✅ | `CompanySerializer.validate_inn`: пусто — ошибка, иначе `normalize_inn(value)`. 9 цифр проходят. |

---

## 3. Поиск и индекс

| Файл | Статус | Описание |
|------|--------|----------|
| **companies/search_index.py** | ✅ | `_parse_inn_list` вызывает `parse_inns(inn_str)` из inn_utils. В индекс попадают те же значения (10/12 + fallback 8–12). |
| **companies/search_service.py** | ✅ | Exact-фаза: ИНН при `8 <= len(digits_only) <= 12`. `is_exact_type`: `8 <= len(only_digits(raw_clean)) <= 12`. Комментарии обновлены. |
| **companies/management/commands/rebuild_company_search_index.py** | ✅ | Берёт `normalized_inns` из `build_company_index_payload` (который использует `_parse_inn_list` → `parse_inns`). |
| **ui/views.py** (поиск компаний) | ✅ | Три места: «похоже на ИНН» — `8 <= len(q) <= 12`, `8 <= len(normalized_q) <= 12`, `8 <= len(tok) <= 12`. Используется `parse_inns` для фильтров. |

---

## 4. Проверка дубликатов и отображение

| Файл | Статус | Описание |
|------|--------|----------|
| **ui/views.py** (`company_duplicates`) | ✅ | GET-параметр `inn` нормализуется через `normalize_inn(inn_raw)` перед фильтром `Q(inn=inn)`. «901 000 327» и «901000327» дают один и тот же поиск. |
| **ui/views.py** (`_dup_reasons`) | ✅ | Сравнение `(c.inn or "").strip() == inn`; в контексте дублей `inn` уже нормализован. |
| **ui/templatetags/ui_extras.py** | ✅ | `split_inns` вызывает `parse_inns(value)` из inn_utils — те же правила для отображения списка ИНН в шаблонах. |

---

## 5. Импорт и внешние системы

| Файл | Статус | Описание |
|------|--------|----------|
| **amocrm/migrate.py** | ✅ | ИНН из amo: `normalize_inn_string(inn_raw)`, при обновлении — `merge_inn_strings(old_inn, incoming)`. Обе функции из inn_utils, используют `parse_inns`. |

---

## 6. Сигналы и перестроение индекса

| Файл | Статус | Описание |
|------|--------|----------|
| **companies/signals.py** | ✅ | `post_save` на `Company` → `rebuild_company_search_index(instance.id)`. Индекс строится через `build_company_index_payload` → `_parse_inn_list` → `parse_inns`. |

---

## 7. Шаблоны и фронтенд

| Место | Статус | Описание |
|-------|--------|----------|
| **templates (company_detail, company_list, base и др.)** | ✅ | Поле ИНН: `maxlength="255"`, без JS-проверки длины 10/12. Отображение `company.inn`, поиск по карточке по подстроке — без ограничения по длине. |

---

## 8. Документация и тесты

| Файл | Статус | Описание |
|------|--------|----------|
| **[SEARCH_BEST_PRACTICES.md](../search/SEARCH_BEST_PRACTICES.md)** | ✅ | Описание normalized_inns и exact-поиска обновлено: 8–12, parse_inns, fallback. |
| **companies/tests.py** | ✅ | `test_normalize_inn`: добавлены кейсы для «901000327» и «901 000 327». Комментарий про пустой результат уточнён. |
| **companies/tests_search.py** | ✅ | Docstring теста exact ИНН: «8–12 цифр». |
| **Миграция 0043** | ⚪ Не меняем | Историческая; в модели актуальный help_text уже обновлён. |

---

## 9. Итоговая сводка

- **Единая точка парсинга ИНН:** `companies.inn_utils.parse_inns` (10/12 + fallback 8–12 цифр).
- **Нормализация при сохранении:** везде через `normalize_inn` / `normalize_inn_string` / `merge_inn_strings` из того же ядра.
- **Индекс поиска:** `normalized_inns` заполняется через `_parse_inn_list` → `parse_inns`.
- **Поиск (exact и «похоже на ИНН»):** везде допускается 8–12 цифр.
- **Дубликаты:** сравнение по нормализованному ИНН.
- **Шаблоны:** отображение и фильтр `split_inns` используют те же правила.

Дыр и забытых мест с жёсткой привязкой только к 10/12 цифрам не осталось.
