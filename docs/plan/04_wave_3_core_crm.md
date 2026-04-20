# Волна 3. Core CRM hardening

**Цель волны:** Добить основной бизнес-функционал CRM: мульти-воронки (опционально), кастомные поля, дедупликация, импорт/экспорт, UTM tracking, аудит-лог по максимуму.

**Параллелизация:** высокая. Этапы 3.1, 3.2, 3.5, 3.6 независимы.

**Длительность:** 12–15 рабочих дней.

**Требования:** Wave 2 завершена. Policy Engine в ENFORCE.

---

## Этап 3.1. Multi-pipeline для сделок (опционально)

### Контекст
Сейчас `CompanyDeal` — единая модель без pipeline'ов (статус сделки — FK на `DealStatus`). Для B2B-тендеров это нормально, но частый запрос — разные воронки: «Продажи», «Тендеры», «Обслуживание», со своими статусами и SLA.

### Цель
Ввести модель Pipeline с настраиваемыми стадиями. Мигрировать существующие сделки в pipeline «Основная».

### Важно
Если вы **точно** не планируете добавлять вторую воронку в ближайшие 6 месяцев — **пропустите этот этап**. Добавить позже всегда можно. Не создавай абстракций впрок.

### Что делать
1. Модели:
   ```python
   class Pipeline(models.Model):
       name = models.CharField(max_length=100)
       slug = models.SlugField(unique=True)
       description = models.TextField(blank=True)
       is_active = models.BooleanField(default=True)
       order = models.PositiveSmallIntegerField(default=0)
       created_at, updated_at
   
   class PipelineStage(models.Model):
       pipeline = FK(Pipeline, related_name='stages')
       name = CharField(...)
       slug = SlugField(...)
       order = PositiveSmallIntegerField()
       probability = PositiveSmallIntegerField(default=50)  # % закрытия
       is_won = BooleanField(default=False)
       is_lost = BooleanField(default=False)
       sla_hours = PositiveIntegerField(null=True)
       color = CharField(max_length=7)  # hex
   ```

2. `CompanyDeal.pipeline = FK(Pipeline)`, `CompanyDeal.stage = FK(PipelineStage)`. Data migration: все existing deals → pipeline «Основная», стадии копировать из `DealStatus`.

3. UI:
   - Admin-страница управления воронками.
   - Deal create/edit — выбор pipeline и stage.
   - Kanban-вид сделок по воронке.
   - Фильтр сделок: по pipeline.

4. Analytics: метрики по воронке (conversion rate, average deal age, win rate) — см. Wave 8.

5. Permissions: ADMIN и SALES_HEAD могут управлять воронками. MANAGER — только использует.

### Инструменты
- `mcp__postgres__*` — миграция данных
- `mcp__context7__*` — паттерны kanban

### Definition of Done
- [ ] Модели созданы, миграция данных прошла
- [ ] Минимум 1 воронка «Основная» существует с 5 стадиями
- [ ] Kanban-view работает, drag-n-drop стадий (опционально, можно списком + dropdown)
- [ ] Все существующие сделки мигрированы без потерь
- [ ] Permissions: MANAGER не может создать pipeline
- [ ] Аналитика по воронкам в Wave 8 получит основу

### Артефакты
- Миграции
- `backend/companies/models/pipeline.py`
- `backend/companies/services/pipeline_service.py`
- `backend/ui/views/pages/pipelines.py`
- `backend/templates/pages/pipelines/*.html`
- `backend/api/v1/views/pipelines.py`
- `tests/companies/test_pipeline.py`
- `docs/features/pipelines.md`

### Валидация
```bash
pytest backend/companies/
playwright test tests/e2e/test_pipeline.py
```

### Откат
```bash
python manage.py migrate companies 00XX_before_pipeline
```

### Обновить в документации
- `docs/features/pipelines.md`
- `docs/decisions.md`: ADR-015

---

## Этап 3.2. Кастомные поля (typed JSON schema)

### Контекст
Частый запрос: «хочу добавить поле X для компаний / сделок / контактов без релиза». EAV (Entity-Attribute-Value) — плохая идея из-за производительности. JSONB + типизированная схема — хорошая.

### Цель
Для Company, Contact, CompanyDeal — добавить `custom_fields JSONB`. Admin может определить schema (field definitions), валидация при сохранении, отображение в UI, фильтрация.

### Что делать
1. Модели:
   ```python
   class CustomFieldDefinition(models.Model):
       entity_type = CharField(choices=["company", "contact", "deal"])
       key = SlugField()  # snake_case
       label = CharField()  # "Год основания"
       field_type = CharField(choices=["string", "text", "integer", "decimal", 
                                         "boolean", "date", "datetime", "url", 
                                         "email", "phone", "select", "multi_select"])
       options = JSONField(default=list, blank=True)  # для select
       required = BooleanField(default=False)
       default_value = JSONField(null=True, blank=True)
       order = PositiveSmallIntegerField(default=0)
       is_searchable = BooleanField(default=False)  # участвует в FTS
       is_filterable = BooleanField(default=True)
       visible_to_roles = JSONField(default=list)  # ["MANAGER", "ADMIN"]
   
   # У Company: 
   custom_fields = JSONField(default=dict, blank=True)
   # + GIN индекс для поиска по полям
   ```

2. **Admin UI** для управления definitions (ADMIN-only).

3. **Валидация**: сервис `custom_fields_validator.validate(entity_type, data)` → ValidationError.

4. **Отображение в формах**: dynamic form rendering на основе definitions.

5. **Поиск**: если `is_searchable=True` — добавлять в FTS index.

6. **Фильтрация**: в list views / API — support `?cf_year_founded=2015` и `?cf_year_founded__gt=2020`.

7. **Migrations**: при удалении field — опция «удалить данные» или «сохранить в archive JSONB».

8. **Audit**: любое изменение `CustomFieldDefinition` + данных — в `SecurityAuditLog`.

### Инструменты
- `mcp__postgres__*` — GIN индекс на JSONB
- `mcp__context7__*` — Django dynamic forms

### Definition of Done
- [ ] 3 entity_type поддерживают custom_fields
- [ ] Admin может создать/изменить/удалить definition
- [ ] Формы динамически рендерят поля
- [ ] API принимает и отдаёт custom_fields
- [ ] Поиск по searchable полям работает
- [ ] Тесты на каждый field_type (валидация + сохранение)

### Артефакты
- Миграции (включая GIN индексы на custom_fields)
- `backend/core/custom_fields/models.py`
- `backend/core/custom_fields/services.py`
- `backend/core/custom_fields/validators.py`
- `backend/core/custom_fields/forms.py` — dynamic form mixin
- `backend/templates/partials/custom_fields_form.html`
- `backend/api/v1/fields/custom_fields_field.py` (DRF serializer field)
- `tests/core/test_custom_fields.py`
- `docs/features/custom-fields.md`

### Валидация
```bash
pytest tests/core/test_custom_fields.py
# Manual: создать definition, увидеть на форме, сохранить, отфильтровать
```

### Откат
```bash
python manage.py migrate core 00XX_before_custom_fields
```

### Обновить в документации
- `docs/features/custom-fields.md`
- `docs/decisions.md`: ADR-016

---

## Этап 3.3. Глобальная дедупликация контактов и компаний

### Контекст
Сейчас `check_phone_duplicate` / `check_email_duplicate` — только внутри одной компании. При создании нового контакта через widget / import — можно получить дубликат глобально.

### Цель
При создании / импорте контакта — проверка на глобальные дубликаты, с опциями «merge», «skip», «create anyway».

### Что делать
1. **Нормализация**:
   - Email: lowercase, удалить dots для gmail'а (опционально).
   - Phone: `phonenumbers` lib, E.164 format.
   - Company name: lowercase, удалить юридические формы (ООО, АО, ИП и т.д.) для match.

2. **Дубликат-detection сервис**:
   ```python
   class DuplicateDetectionService:
       def find_contact_duplicates(email, phone) -> list[Contact]
       def find_company_duplicates(name, inn) -> list[Company]
   ```

3. **При создании** (widget, manual, import):
   - Если найдены дубликаты → `DuplicateWarning` в ответе.
   - UI: модал «Найден похожий контакт» с опциями.

4. **Merge flow**:
   - `merge_contacts(primary_id, duplicate_id)` — переносит conversations, deals, history.
   - Transaction + lock, чтобы не потерять данные.
   - Аудит-запись.
   - Undo в течение 24ч (хранить `MergedContactBackup`).

5. **Bulk deduplication tool**:
   - Management command `find_duplicates --entity=contacts --preview`.
   - Admin UI с ручным подтверждением merge.

6. **INN уникальность** для Company: если есть INN → уникальность по нему жёсткая (unique constraint).

### Инструменты
- `phonenumbers` lib
- `mcp__postgres__*` — для анализа duplicates

### Definition of Done
- [ ] DuplicateDetectionService работает для email, phone, name, INN
- [ ] Widget создание контакта: duplicate warning
- [ ] Merge flow: успешный merge + undo в 24ч
- [ ] Bulk detection command работает
- [ ] Unique constraint на Company.inn (где есть)
- [ ] Тесты: 15+ сценариев (clean create, exact match, fuzzy match, merge, undo)

### Артефакты
- `backend/companies/services/duplicate_detection.py`
- `backend/companies/services/merge_service.py`
- `backend/management/commands/find_duplicates.py`
- Миграции
- `tests/companies/test_duplicates.py`
- `docs/features/deduplication.md`

### Валидация
```bash
pytest tests/companies/test_duplicates.py
python manage.py find_duplicates --entity=contacts --preview
```

### Откат
```bash
git revert
# Unique constraint снять migration'ом
```

### Обновить в документации
- `docs/features/deduplication.md`

---

## Этап 3.4. Импорт и экспорт CSV / Excel

### Контекст
Менеджерам регулярно нужно загружать списки контактов из Excel и выгружать отчёты. Сейчас нет централизованного решения.

### Цель
Создать универсальный импорт/экспорт фреймворк для основных сущностей.

### Что делать
1. **django-import-export** как база. Создать `Resource` для Company, Contact, CompanyDeal, Task.

2. **Валидация при импорте**:
   - Обязательные поля.
   - Типы данных.
   - Phone normalization.
   - Дубликат-проверка (см. 3.3).
   - Валидация кастомных полей (см. 3.2).

3. **Mapping UI**:
   - Пользователь загружает XLSX/CSV.
   - Система показывает первые 10 строк + угадывает mapping (заголовки колонок → поля модели).
   - Пользователь корректирует.
   - Preview перед import.
   - Async через Celery + progress bar.

4. **Обработка ошибок**:
   - Строки с ошибками — в отдельный «error report» XLSX (скачать, исправить, повторить).

5. **Export**:
   - XLSX, CSV. Из list views с применёнными фильтрами.
   - Limit 100_000 rows (для Excel), больше — async через Celery.

6. **Permissions**:
   - Import companies: SALES_HEAD+.
   - Export: MANAGER может экспортировать только свои (policy-check), SALES_HEAD+ — всех в branch.
   - Audit log каждого import/export.

### Инструменты
- `django-import-export` + `openpyxl`
- `mcp__context7__*`

### Definition of Done
- [ ] Import flow работает для 4 entity
- [ ] Валидация и error report работают
- [ ] Async import через Celery, progress в UI
- [ ] Export работает для list views
- [ ] Permissions enforcement (тесты)
- [ ] Audit log записывает import/export

### Артефакты
- `backend/core/import_export/` — base classes
- `backend/companies/resources.py`, `contacts/resources.py`, etc.
- `backend/ui/views/pages/import_export/*.py`
- `backend/templates/pages/import_export/*.html`
- `tests/core/test_import_export.py`
- `docs/features/import-export.md`

### Валидация
```bash
pytest tests/core/test_import_export.py
playwright test tests/e2e/test_import_companies.py
```

### Откат
```bash
git revert
```

### Обновить в документации
- `docs/features/import-export.md`
- `docs/runbooks/import-best-practices.md` — гайд для менеджеров

---

## Этап 3.5. UTM и лид-трекинг

### Контекст
Widget принимает контакты с сайта, но UTM-метки теряются. Для аналитики источников — критично.

### Цель
Сохранять UTM-метки для каждого лида, отчёты по источникам в Wave 8.

### Что делать
1. **Widget**: при открытии — читать `document.referrer`, `window.location.search` (utm_*), хранить в sessionStorage. При создании диалога/контакта — отправлять на сервер.

2. **Модель** `LeadSource`:
   ```python
   entity_type, entity_id (FK to Company/Contact generic)
   referrer, utm_source, utm_medium, utm_campaign, utm_term, utm_content
   first_touch_at, first_touch_url, last_touch_at, last_touch_url
   # first-touch / last-touch attribution
   ```

3. **Multi-touch**: если контакт пришёл несколько раз — сохранять все touches, attribution — first & last.

4. **UI**:
   - В карточке компании — раздел «Источники».
   - Analytics (Wave 8) — conversion funnel по UTM.

5. **Widget upgrade**: расширить JS чтобы захватывать всё. Проверить CORS, privacy.

### Definition of Done
- [ ] Widget собирает UTM и referrer
- [ ] `LeadSource` сохраняется при создании нового лида
- [ ] Multi-touch работает
- [ ] В UI отображается источник
- [ ] Тесты на атрибуцию

### Артефакты
- Миграции для `LeadSource`
- `backend/marketing/services/attribution_service.py`
- `backend/static/ui/widget/*.js` (обновлённый)
- `backend/ui/views/partials/company_sources.py`
- `tests/marketing/test_attribution.py`
- `docs/features/utm-tracking.md`

### Валидация
```bash
pytest tests/marketing/
# Manual: зайти на site?utm_source=google → открыть чат → проверить БД
```

### Откат
```bash
git revert
```

### Обновить в документации
- `docs/features/utm-tracking.md`

---

## Этап 3.6. Audit log полный (everywhere)

### Контекст
`ActivityEvent` и `CompanyHistoryEvent` есть, но покрытие неполное. Некоторые mutating операции не логируются.

### Цель
100% покрытие mutating операций через Django signals + decorator.

### Что делать
1. **Центральный сервис** `audit_log.py`:
   ```python
   @audit_log(action="company.create", resource=Company, extract_details=...)
   def create_company(...): ...
   ```

2. **Signals-based**:
   - `post_save`, `post_delete` для ключевых моделей → auto-log.
   - С исключениями (e.g., User.last_login).

3. **Что логируем**:
   - Entity (type, id, name)
   - Action (create, update, delete, transfer, merge, restore, approve)
   - Actor (user id, role)
   - Timestamp
   - Changes diff (для update — before/after)
   - IP, user agent, request_id

4. **Viewing audit log**:
   - В карточке компании — вкладка «История» (уже есть `CompanyHistoryEvent`).
   - Admin-страница глобального audit log с фильтрами.
   - ADMIN-only.

5. **Retention**:
   - 180 дней в основной БД (как сейчас).
   - После — архивация в S3 compressed JSONL.
   - Поиск по архиву — через отдельный tool (Wave 10).

6. **Performance**:
   - Write — async через Celery queue.
   - GIN индексы на фильтруемых полях.

### Definition of Done
- [ ] Все mutating сервисы вызывают audit
- [ ] Signals для backup-пути (на случай если забыли в сервисе)
- [ ] UI: company history полная
- [ ] Admin audit log работает
- [ ] Retention с архивацией в S3 работает (Wave 10 зависимость — отложить S3 часть если не готово)
- [ ] Тесты: 20+ сценариев, проверяющих наличие audit-записи после операции

### Артефакты
- `backend/core/audit/service.py`
- `backend/core/audit/decorators.py`
- `backend/core/audit/signals.py`
- `backend/ui/views/pages/admin/audit_log.py`
- `tests/core/test_audit.py`
- `docs/features/audit-log.md`

### Валидация
```bash
pytest tests/core/test_audit.py
# Manual coverage check: создать company → проверить запись; update → diff; delete → запись
```

### Откат
```bash
git revert
```

### Обновить в документации
- `docs/features/audit-log.md`

---

## Этап 3.7. Улучшение видимости (data scope)

### Контекст
Сейчас `visible_companies_qs` есть, но реализация разрознена. GROUP_MANAGER ≠ ADMIN должна соблюдаться строго.

### Цель
Единый data scope engine. `user.visible(Model)` — возвращает QS видимых юзеру записей.

### Что делать
1. **DataScope engine** `backend/policy/data_scope.py`:
   ```python
   class DataScope:
       @staticmethod
       def apply(queryset, user, action="read") -> QuerySet:
           # Делегирует в model-specific handlers
           ...
   ```

2. **Регистрация** per-model handlers:
   ```python
   @register_scope(Company, action="read")
   def company_read_scope(qs, user):
       if user.role == "ADMIN":
           return qs
       elif user.role == "BRANCH_DIRECTOR":
           return qs.filter(branch=user.branch)
       elif user.role == "GROUP_MANAGER":
           # свой филиал + подчинённые
           subordinate_ids = user.subordinates.values_list('id', flat=True)
           return qs.filter(Q(branch=user.branch) & (Q(responsible=user) | Q(responsible_id__in=subordinate_ids)))
       elif user.role == "MANAGER":
           return qs.filter(responsible=user)
       # ...
   ```

3. **Интеграция**:
   - ListViews: `Model.objects.visible_to(request.user)`.
   - DRF: `DataScopeFilter` backend для viewset.
   - Templates: никогда не показывай данные в обход scope.

4. **Hierarchy**:
   - `User.subordinates` (M2M self-reference, direct + transitive).
   - `User.manager` (FK self) — прямой руководитель.
   - Скрипт пересчёта `rebuild_subordinates_hierarchy`.

5. **Тесты**: для каждой роли × модели — 3-5 сценариев visibility.

### Инструменты
- `mcp__context7__*`

### Definition of Done
- [ ] DataScope engine работает для Company, Contact, Deal, Task, Conversation, Campaign
- [ ] User.subordinates / User.manager настраиваются в admin
- [ ] DataScopeFilter интегрирован в DRF
- [ ] Тесты visibility: 60+ сценариев
- [ ] Performance: scope не добавляет N+1, EXPLAIN показывает индексы

### Артефакты
- `backend/policy/data_scope.py`
- `backend/policy/scope_registry.py`
- `backend/companies/scopes.py`, `messenger/scopes.py`, etc.
- `backend/api/v1/filters/scope.py`
- `tests/policy/test_data_scope.py`
- `docs/features/data-scope.md`

### Валидация
```bash
pytest tests/policy/test_data_scope.py
# Role-switch E2E test
```

### Откат
```bash
git revert
```

### Обновить в документации
- `docs/features/data-scope.md`

---

## Этап 3.8. Bulk actions (массовые операции)

### Контекст
Менеджерам нужны массовые операции: передать 50 компаний другому менеджеру, поставить тег на 100 контактов, добавить 200 в рассылку.

### Цель
Единый bulk-action фреймворк с previvew, async execution, audit.

### Что делать
1. **Framework** `backend/core/bulk_actions/`:
   - Регистрация action'ов per-model.
   - Action принимает queryset + params.
   - Preview: показать первые 10 затрагиваемых + total count.
   - Execution: Celery task с chunk'ами.

2. **Стандартные actions**:
   - Company: `assign_responsible`, `add_tags`, `change_branch`, `bulk_delete` (via approval), `export`.
   - Contact: `add_to_campaign`, `merge_by_key`, `bulk_update_consent`.
   - Deal: `move_to_stage`, `assign_manager`.
   - Task: `bulk_complete`, `bulk_reschedule`.

3. **UI**:
   - Checkboxes в list views (Company list, Contact list, etc.).
   - «Действия» dropdown → выбор action → params dialog → preview → confirm → progress.

4. **Permissions**:
   - Каждый action проходит через policy check.
   - Bulk delete — только ADMIN, через approval flow.

5. **Audit**:
   - Одна запись на bulk action с количеством + ссылкой на log_id.
   - Опционально — per-row мелкие записи.

### Definition of Done
- [ ] Framework работает, 4+ action'а реализовано
- [ ] Preview + async execution
- [ ] UI: checkboxes + dropdown
- [ ] Permissions и audit работают
- [ ] Тесты на 5+ bulk actions

### Артефакты
- `backend/core/bulk_actions/`
- `backend/companies/bulk_actions.py`, etc.
- `backend/templates/partials/bulk_actions_toolbar.html`
- `backend/static/ui/bulk-actions.js`
- `tests/core/test_bulk_actions.py`
- `docs/features/bulk-actions.md`

### Валидация
```bash
pytest tests/core/test_bulk_actions.py
# E2E: select 50 companies → assign → preview → confirm → check result
```

### Откат
```bash
git revert
```

### Обновить в документации
- `docs/features/bulk-actions.md`

---

## Checklist завершения волны 3

- [ ] Multi-pipeline (если решили делать) — работает
- [ ] Custom fields — работают для 3 entity types
- [ ] Deduplication — защита от дубликатов, merge flow
- [ ] Import/Export — XLSX/CSV для 4+ entity
- [ ] UTM tracking — для всех новых лидов через widget
- [ ] Audit log — покрытие ≥ 95%
- [ ] Data scope — унифицирован, все views соблюдают
- [ ] Bulk actions — framework + 4+ action'а

**Только после этого** — переход к Wave 4 (Tasks & Notifications).
