# Проверка логики миграции AmoCRM: риски и выводы

## 1) Комментарии к телефону: разделение PHONE и phone_comment

### Контакты (ContactPhone) — **ок**
- `parse_phone_value` возвращает `phones` (E.164) и `comment`. В `ContactPhone` пишется `value=phone_e164`, `comment=phone_comment` — это **отдельные поля**. Само значение `value` — чисто E.164, без "доб. 4", "через приёмную" и т.п.

### Компании — **были проблемы** (исправлено)
- **Основной телефон `comp.phone`**: раньше брался `str(phones[0])` из `_extract_company_fields` **без** `parse_phone_value` и нормализации. В `comp.phone` могло попадать сырое значение с "доб. 4", "через приёмную". **Исправление**: используется `parse_phone_value`/`normalize_phone`, в `comp.phone` — только E.164, комментарий из первого телефона и общее примечание компании объединяются в `comp.phone_comment`.
- **`comp.phone_comment`**: получал только `company_note` (общее примечание). Теперь при наличии комментария/доб. из разбора первого телефона он объединяется с `company_note`.
- **CompanyPhone (доп. телефоны)**: в `value` писался результат `_normalize_phone` (E.164), но **comment не заполнялся** — в `_extract_company_fields` нет `parse_phone_value`, комментарии к доп. телефонам терялись. **Исправление**: при создании `CompanyPhone` используется `parse_phone_value`; в `value` — E.164, в `comment` — доб./комментарий по этому номеру.

---

## 2) Нормализация РФ: 8→+7, 7→+7, 10 цифр→+7, скобки

### `normalize_phone` (amocrm.migrate)
- `8XXXXXXXXXX` (11 цифр): `+7` + `phone_digits[1:]` — ок.
- `7XXXXXXXXXX` (11 цифр): `+` + `phone_digits` — ок.
- 10 цифр: `+7` + `phone_digits` — ок.
- `+7 (3452) 38-19-19`: скобки обрабатываются, `phone_digits` = "3452381919" → `+73452381919` — ок.
- `3843318282` (10 цифр): `+73843318282`. Логики вида «если не +7, то добавить +» **нет**, «+384» не появляется.
- Итог: правила для РФ выдержаны, 384 и подобные не превращаются в +384.

### `_normalize_phone` (ui.forms)
- Аналогичные правила для 8/7/10 цифр и +7. В нестандартных случаях (например, 12+ цифр) возвращается `phone[:50]` — не E.164, но это крайние случаи.

---

## 3) Дедупликация телефонов (после нормализации)

- **Контакты**: перед записью — дедуп по `normalize_phone(...).phone_e164` (`seen_p`). При обновлении — `existing_phones_map` по `(contact_id, value)`. Повторные запуски не добавляют тот же `value` повторно.
- **Компании**: при создании `CompanyPhone` — дедуп по `_normalize_phone(v)` против `main_phone_normalized` и `existing_phones_normalized`. Дубли в рамках батча не создаются.
- Ограничение: у `ContactPhone`/`CompanyPhone` нет уникального ограничения `(contact/company, value)` на уровне БД; защита только в коде и при `existing_*`/`seen_*`.

---

## 4) sanitize_name: не съедать полезные приставки

- Удаляются только паттерны: `доб. N`, `затем N`, `внутр. N`, `ext N`, `#N`, `x N`, `тональный`, `мини атс`, `перевести на …`, `добавочн…`.  
- **Не удаляются**: «ОК», «ЛПР», «приёмная» (их нет в паттернах).
- Примеры:
  - `ОК Екатерина доб.7` → имя «ОК Екатерина», «доб. 7» в `extracted` → в `note`.
  - `Андрей Павлович, доб. 4, затем 1` → «Андрей Павлович», инструкции в `note`.
  - `Приёмная` → без изменений.

---

## 5) Email: только из полей EMAIL / стандартного email

- Источники: `ac.get("email")` (стандартное поле контакта Amo) и `custom_fields` с `field_code=="EMAIL"` или `field_name` содержащим «email»/«почта»/«e-mail».
- Заметки (`notes`, `_embedded.notes`) **не** используются для извлечения email.

---

## 6) Метрики

| Метрика | Статус |
|--------|--------|
| `phones_rejected_as_note` | есть |
| `position_rejected_as_phone` | есть |
| `name_cleaned_extension_moved_to_note` | есть (по сути «инструкции из имени в note») |
| `name_instructions_moved_to_note` | есть (алиас) |
| `skynet_phone_values_rejected` | есть — значения из Skynet-поля, не распознанные как телефон |
| `company_phones_rejected_invalid` | есть — пропуск добавления в CompanyPhone при невалидном номере |

Остальные: `phones_rejected_invalid`, `phones_extracted_with_extension`, `position_phone_detected`, `emails_rejected_invalid_format`, `fields_skipped_to_prevent_blank_overwrite` — присутствуют.

---

## 7) Skynet phone field (список телефонов Скайнет)

- **Проблема**: поле могло содержать произвольный текст; `list_vals`/`_split_multi` режут по запятой/точке с запятой, и слова («сотовой», «телефонной» и т.п.) попадали в список «телефонов».
- **Исправление**:
  - Skynet обрабатывается отдельно: сырые строки из `_custom_values_text` (без `_split_multi` по запятой).
  - Каждая строка прогоняется через `parse_phone_value`; в `phones` попадают только валидные E.164.
  - Не-номера считаются в `skynet_phone_values_rejected`; при `> 0` пишется `logger.warning` с `company_id` и `name`.
- **CompanyPhone**: добавление только при `is_valid_phone(normalized)`; иначе `company_phones_rejected_invalid += 1` и запись не создаётся.
- При первом–третьем срабатывании `company_phones_rejected_invalid` в DEBUG логируется пример значения. В WARN по Skynet в лог добавляется `example=<первое отброшенное значение>`.

---

## 8) Задачи и заметки: счётчики и баг с циклом задач

### Задачи
- **Баг**: цикл обработки задач (task_uids, existing_tasks_by_uid, `for t in tasks`) был в ветке `else` (когда задачи не запрашиваются). При успешном `fetch` он не выполнялся, поэтому `created=0`, `updated=0`, `would_create=0`, `would_update=0` при 893 полученных.
- **Исправление**: цикл и все related-счётчики перенесены в ветку `if` (когда задачи запрашиваются). В `else` остаются только `res.tasks_seen = 0` и при dry_run — лог «задачи НЕ запрашиваются».
- В лог по задачам добавлены `would_create`, `would_update` для интерпретации dry-run.

### Заметки
- **notes_processed** не инкрементировался — учитывается при достижении create/update/skip_existing (после прохождения amomail).
- **notes_skipped_no_company** не учитывался — добавлен инкремент перед `continue` при `not company`.
- **Взаимоисключаемость**: `notes_skipped_no_text` увеличивается только при реальном `continue` (в non–dry_run), чтобы одна заметка не попадала и в `skipped_no_text`, и в `skipped_amomail`. В лог добавлен `would_add`; в комментарии указано, что `skipped_*` взаимно исключают друг друга по одной заметке.
