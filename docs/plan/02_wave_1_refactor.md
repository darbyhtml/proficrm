# Волна 1. Архитектурная рефакторизация

**Цель волны:** Привести кодовую базу в состояние, где дальнейшие изменения не добавляют боли. Разрезать god-views, отделить pages от API, удалить legacy, ввести слои (views → services → repositories).

**Параллелизация:** 3 потока внутри волны (см. ниже). Между этапами — синхронизация через PR-review.

**Длительность:** 10–14 рабочих дней (основной рывок).

**Требования:** Wave 0 завершена. Baseline метрики зафиксированы. Feature flags установлены.

**Параллельные потоки внутри волны:**
- **Поток A (services layer)**: этапы 1.1 → 1.3 → 1.6
- **Поток B (views refactor)**: этапы 1.2 → 1.4 → 1.5
- **Поток C (cleanup)**: этапы 1.7 → 1.8

Если работаешь соло — иди строго по порядку 1.1 → 1.8.

---

## Этап 1.1. Выделение services layer из _base.py

### Контекст
`ui/views/_base.py` сейчас 1700+ LOC — helpers, decorators, querysets, бизнес-логика вперемешку. Это корень всех проблем: любое изменение одного модуля задевает файл, который импортируется отовсюду.

### Цель
Расформировать `_base.py` на тематические модули: `services/`, `querysets/`, `decorators/`, `helpers/`. После — `_base.py` либо исчезает, либо содержит только legacy-импорты для обратной совместимости.

### Что делать
1. **Анализ содержимого `_base.py`** через Agent:
   - Перечислить все классы/функции с их зависимостями (что импортируют, кто импортирует их).
   - Классифицировать по категориям: querysets, decorators, helpers, services (бизнес-логика), form handlers.
   - Сохранить карту в `docs/audit/base_py_map.md`.

2. **Извлечь queryset'ы** в `backend/companies/querysets.py`, `backend/accounts/querysets.py`, и т.д. Использовать Manager с custom QuerySet:
   ```python
   class CompanyQuerySet(models.QuerySet):
       def visible_to(self, user): ...
       def with_stats(self): ...
   
   class CompanyManager(models.Manager.from_queryset(CompanyQuerySet)):
       ...
   ```
   Правило: все фильтры видимости переезжают в `visible_to(user)` методы.

3. **Извлечь services**:
   - `backend/companies/services/company_service.py` — CRUD, transfer, merge, visible_scope.
   - `backend/companies/services/deal_service.py` — создание сделки, смена статуса, связывание с контактами.
   - `backend/companies/services/deletion_service.py` — заявка на удаление, approval, execute.
   - `backend/accounts/services/user_service.py` — роли, branch, MagicLink, invitations.
   - Сервисы — чистые функции или классы без Django-view-зависимостей. На вход — чистые данные + user, на выход — результат или exception.

4. **Извлечь decorators** в `backend/core/decorators/`:
   - `policy_required`, `require_role`, `require_branch`, `ajax_only`, `json_required`.

5. **Helpers** — в `backend/core/helpers/` с тематическими модулями: `formatting.py`, `datetime.py`, `phone.py`, `email.py`.

6. **Перевод импортов**: использовать `ruff --fix` с правилом I для сортировки, затем ручная проверка.

7. **Тесты**: для каждого нового модуля — базовые unit-тесты на min 70% coverage.

### Инструменты
- `Agent` для параллельного анализа _base.py
- `mcp__context7__*` — паттерны Django service layer
- `Bash` — rope/bowler/libcst для автоматических рефакторингов (AST-level)

### Definition of Done
- [ ] `_base.py` содержит ≤ 100 LOC (только re-export или удалён)
- [ ] Созданы services: `company_service`, `deal_service`, `deletion_service`, `user_service` (минимум)
- [ ] Coverage сервисов ≥ 80%
- [ ] Все существующие тесты (1179) — зелёные
- [ ] Линтеры (ruff, mypy) — зелёные на новых модулях
- [ ] Карта в `docs/audit/base_py_map.md` показывает «было → стало»

### Артефакты
- `backend/companies/querysets.py`
- `backend/companies/managers.py`
- `backend/companies/services/company_service.py`
- `backend/companies/services/deal_service.py`
- `backend/companies/services/deletion_service.py`
- `backend/accounts/services/user_service.py`
- `backend/core/decorators/*.py`
- `backend/core/helpers/*.py`
- `docs/audit/base_py_map.md`
- `docs/architecture.md`: раздел «Services layer»

### Валидация
```bash
wc -l backend/ui/views/_base.py  # ≤ 100
pytest backend/  # 1179+ зелёные
mypy backend/companies/services/ backend/accounts/services/
ruff check backend/
```

### Откат
```bash
git revert <commit-sha>
```

### Обновить в документации
- `docs/architecture.md`: layered architecture диаграмма, service-layer соглашения
- `docs/decisions.md`: ADR-005 «Service layer для бизнес-логики»

---

## Этап 1.2. Разделение company_detail.py на pages/*.py

### Контекст
`ui/views/company_detail.py` сейчас 2698 LOC. Один файл обслуживает 30+ URL-routes: карточка, редактирование, deals, tasks, notes, phones, emails, contacts, history, deletion-requests, search — всё в одном месте. Любое изменение в одном разделе — риск регрессии в другом.

### Цель
Разделить на 8–10 тематических файлов в `backend/ui/views/pages/company/`. Каждый ≤ 400 LOC.

### Что делать
1. Проанализировать `company_detail.py` через Agent: сгруппировать views по доменной области.

2. Создать структуру:
   ```
   backend/ui/views/pages/company/
     __init__.py      # re-exports для URL обратной совместимости
     detail.py        # главная карточка (GET /company/<id>/)
     edit.py          # форма редактирования
     deals.py         # сделки компании (list/create/update/delete)
     tasks.py         # задачи
     notes.py         # заметки + теги
     contacts.py      # контактные лица
     communication.py # phones + emails
     history.py       # timeline
     deletion.py      # запрос на удаление + approval
     search.py        # внутренний поиск
   ```

3. **URL routing** — оставить старые URL-паттерны, только поменять target view:
   ```python
   # backend/ui/urls/company.py
   from backend.ui.views.pages.company import detail, edit, deals, ...
   urlpatterns = [
       path('<int:pk>/', detail.CompanyDetailView.as_view(), name='company_detail'),
       ...
   ]
   ```

4. **Общий базовый класс** для всех views карточки: `CompanyPageBaseView(LoginRequiredMixin, PolicyMixin, View)` с методами `get_company()`, `get_context_data()`, `has_permission()`.

5. **Templates** — если они ссылаются на конкретный view-имя в url-tag'ах, ничего не меняем. Если view-имена меняются — обновить шаблоны.

6. **Тесты** — существующие должны проходить. Добавить миграционные integration тесты: один на каждый URL, проверка 200/302/403.

### Инструменты
- `Agent` для анализа
- `mcp__playwright__*` — smoke-тесты после рефакторинга (вся карточка должна открываться без ошибок)

### Definition of Done
- [ ] `company_detail.py` удалён или ≤ 50 LOC (legacy imports)
- [ ] 10 новых файлов в `pages/company/`, каждый ≤ 400 LOC
- [ ] Все существующие URL работают (smoke test)
- [ ] Playwright smoke-тест: переход по всем табам карточки
- [ ] Coverage views ≥ 70%

### Артефакты
- `backend/ui/views/pages/company/*.py` (10 файлов)
- `backend/ui/urls/company.py` (обновлённый)
- `tests/e2e/test_company_card_smoke.py`
- `docs/architecture.md`: раздел «URL routing и pages»

### Валидация
```bash
wc -l backend/ui/views/pages/company/*.py  # все ≤ 400
pytest backend/  # зелёные
playwright test tests/e2e/test_company_card_smoke.py
```

### Откат
```bash
git revert <commit-sha>
```

### Обновить в документации
- `docs/architecture.md`: новая структура views

---

## Этап 1.3. Отделение pages от API

### Контекст
Сейчас половина views возвращает HTML, половина — JsonResponse. Для фронта это хаос: иногда форма POST → редирект, иногда fetch → JSON. Для Android и будущих интеграций — нужен чистый `/api/v1/*`.

### Цель
Разделить два мира:
- `backend/ui/views/pages/*` — только HTML-ответы, session auth
- `backend/api/v1/*` — только JSON (DRF viewset'ы), JWT или session + DRF permissions

### Что делать
1. Для каждой «смешанной» view в inventory (см. Wave 0) принять решение:
   - Если использует `fetch` из JS — переносить в `api/v1/`.
   - Если классический form-submit → redirect — оставить в `pages/`.

2. Создать DRF viewsets для основных сущностей (если ещё нет):
   - `api/v1/companies/` — list, retrieve, create, update, destroy
   - `api/v1/deals/`
   - `api/v1/tasks/`
   - `api/v1/notes/`
   - `api/v1/contacts/`

3. Serializers — с правильными permissions, validation, read/write split.

4. Заменить все inline `JsonResponse` на viewset-методы или `APIView`.

5. В JS поменять endpoint'ы: было `fetch('/company/1/deals/update/')` → стало `fetch('/api/v1/deals/5/', {method: 'PATCH'})`.

6. **Версионирование**: префикс `/api/v1/` закреплён, изменения - через `/api/v2/`.

7. **OpenAPI**: drf-spectacular должен давать полную схему без «unknown type». `schema.yaml` генерируется в CI.

### Инструменты
- `Agent` для анализа
- `mcp__context7__*` — DRF viewsets docs
- `mcp__playwright__*` — E2E тест, что JS обращается к новым endpoint'ам

### Definition of Done
- [ ] Ни одна `pages/*.py` view не возвращает JSON (кроме явных `_partial_*` для HTMX)
- [ ] `api/v1/*` покрывает CRUD для всех основных сущностей
- [ ] `/api/schema/` возвращает валидный OpenAPI 3.0
- [ ] Вся фронтовая JS обновлена на новые endpoint'ы
- [ ] Playwright E2E: все JSON-операции (создание задачи, обновление note, добавление контакта) работают

### Артефакты
- `backend/api/v1/views/companies.py`, `deals.py`, `tasks.py`, `notes.py`, `contacts.py`
- `backend/api/v1/serializers/*.py`
- `backend/api/v1/urls.py`
- `backend/static/ui/*.js` — обновлённые fetch-вызовы
- `docs/api/README.md` — описание API
- `tests/e2e/test_api_journeys.py`

### Валидация
```bash
pytest backend/api/
curl http://localhost:8001/api/schema/ -o /tmp/schema.yaml
python -c "import yaml; yaml.safe_load(open('/tmp/schema.yaml'))"
playwright test tests/e2e/test_api_journeys.py
```

### Откат
```bash
git revert <commit-sha>
```

### Обновить в документации
- `docs/architecture.md`: раздел «API split: pages vs api/v1»
- `docs/decisions.md`: ADR-006 «Разделение HTML и JSON layer»

---

## Этап 1.4. Удаление legacy app: amocrm/

### Контекст
App `amocrm/` — остаток отказа от интеграции с AmoCRM. Код мёртвый, но модели в БД есть, миграции накопились, импорты встречаются.

### Цель
Полностью удалить `amocrm/` app: код, модели, миграции, импорты, references.

### Что делать
1. Проверить usage: `grep -r "from amocrm" backend/ | wc -l`. Ожидание: все импорты — внутри самого amocrm или в старых тестах.

2. Bounded-check: есть ли FK из других моделей на модели amocrm? Если да — отдельный этап миграции данных.

3. Сделать финальный дамп данных (на всякий случай):
   ```bash
   python manage.py dumpdata amocrm --indent 2 > backups/amocrm-final-dump-$(date +%F).json
   ```

4. Создать миграцию удаления:
   ```python
   # amocrm/migrations/00XX_delete_all_models.py
   class Migration(migrations.Migration):
       dependencies = [...]
       operations = [
           migrations.DeleteModel('AmoApiConfig'),
           migrations.DeleteModel('AmoContact'),
           ...
       ]
   ```

5. Удалить директорию `backend/amocrm/`, обновить `INSTALLED_APPS`, `urls`, references.

6. Прогнать тесты — ожидание: 0 падений.

### Инструменты
- `mcp__postgres__*` — проверка зависимостей FK
- `Agent` — поиск всех references

### Definition of Done
- [ ] `backend/amocrm/` не существует
- [ ] `INSTALLED_APPS` не содержит `amocrm`
- [ ] `grep -r "amocrm" backend/` — 0 совпадений (кроме миграции удаления в `django_migrations` таблице)
- [ ] Все тесты зелёные
- [ ] Бэкап сохранён в `backups/`

### Артефакты
- Миграция удаления (на диске временно, затем тоже удаляется через `squashmigrations` при желании)
- `backups/amocrm-final-dump-YYYY-MM-DD.json`
- `docs/decisions.md`: ADR-007 «Удаление amocrm legacy»

### Валидация
```bash
python manage.py showmigrations amocrm  # "No installed app"
grep -r "amocrm" backend/ --include="*.py" | wc -l  # 0
pytest
```

### Откат
```bash
git revert <commit-sha>
python manage.py migrate amocrm 00XX_previous  # до миграции удаления
python manage.py loaddata backups/amocrm-final-dump-YYYY-MM-DD.json
```

### Обновить в документации
- `docs/decisions.md`: ADR-007
- `docs/architecture.md`: убрать упоминания amocrm

---

## Этап 1.5. Типизация через mypy (strict для новых модулей)

### Контекст
`mypy` установлен в Wave 0.2 в щадящем режиме. Новые модули (services/, api/v1/) должны быть strict-typed с самого начала.

### Цель
Включить `strict = True` для `backend/core/*`, `backend/*/services/*`, `backend/api/v1/*`. Для legacy — оставить щадящий режим.

### Что делать
1. Обновить `mypy.ini`:
   ```ini
   [mypy-backend.core.*]
   strict = True
   
   [mypy-backend.*.services.*]
   strict = True
   
   [mypy-backend.api.v1.*]
   strict = True
   
   [mypy-backend.*.views.*]
   check_untyped_defs = True
   # не strict — слишком много legacy
   ```

2. Добавить type hints везде в strict-зонах. Использовать:
   - `from __future__ import annotations`
   - `TYPE_CHECKING` для circular imports
   - `TypedDict` для structured dict'ов
   - `Protocol` для интерфейсов сервисов
   - `Literal` для enum-подобных строк
   - `Self` (Python 3.11+) для factory методов

3. **Pydantic v2** как input-validation layer для сервисов (опционально, но рекомендую):
   ```python
   class CompanyCreateInput(BaseModel):
       name: str = Field(min_length=1, max_length=255)
       phone: str | None = None
       ...
   ```

4. **Django-stubs config**: указать `DJANGO_SETTINGS_MODULE = "crm.settings.test"` для mypy.

5. Коммитить итерационно: по одному сервису, чтобы не получить «стену ошибок».

### Инструменты
- `mcp__context7__*` — mypy, django-stubs, pydantic v2 docs

### Definition of Done
- [ ] `mypy backend/` на strict-зонах — 0 ошибок
- [ ] `mypy` в CI запущен и зелёный
- [ ] Все новые сервисы (Wave 1.1) имеют полные типы
- [ ] Pydantic инпуты для публичных методов сервисов (если решено)

### Артефакты
- `mypy.ini` (обновлённый)
- Все файлы в strict-зонах — с типами

### Валидация
```bash
mypy backend/core backend/companies/services backend/api/v1 --strict
```

### Откат
Откатить settings mypy.ini в менее strict — типы остаются безвредными.

### Обновить в документации
- `docs/decisions.md`: ADR-008 «mypy strict для новых модулей»
- `docs/testing.md`: секция «Type safety»

---

## Этап 1.6. Единый error handling и exceptions

### Контекст
Сейчас ошибки летают как Django exceptions вперемешку с сырыми exc'ами, 500-ки возвращаются без структуры, API ответы не стандартизованы.

### Цель
Ввести иерархию кастомных exceptions и единый error-handler для API. Response shape всегда одинаковый: `{ "error": { "code": "...", "message": "...", "details": {...} } }`.

### Что делать
1. Создать `backend/core/exceptions.py`:
   ```python
   class CRMError(Exception):
       code: str = "internal_error"
       http_status: int = 500
       default_message: str = "Внутренняя ошибка"
   
   class ValidationError(CRMError):
       code = "validation_error"
       http_status = 400
   
   class PermissionDenied(CRMError):
       code = "permission_denied"
       http_status = 403
   
   class ResourceNotFound(CRMError):
       code = "not_found"
       http_status = 404
   
   class ConflictError(CRMError):
       code = "conflict"
       http_status = 409
   
   class RateLimitExceeded(CRMError):
       code = "rate_limited"
       http_status = 429
   
   class DomainError(CRMError):  # бизнес-ошибки
       http_status = 422
   ```

2. DRF custom exception handler `backend/api/v1/exception_handler.py`.

3. Django middleware для pages: кастомные 403, 404, 500 страницы.

4. Сервисы бросают domain exceptions (`DomainError`), никогда — сырые Django exceptions.

5. Логирование: все CRMError пишутся в Sentry с `level=warning` (не error), все unexpected — с `level=error`.

### Инструменты
- `mcp__context7__*` — DRF exception handling

### Definition of Done
- [ ] `backend/core/exceptions.py` создан, 10+ кастомных классов
- [ ] DRF handler подключён, все API-ответы с ошибками имеют единую shape
- [ ] Django 403/404/500 кастомные шаблоны
- [ ] Сервисы переведены на domain exceptions
- [ ] Тесты на error-shape (минимум 5 scenarios)

### Артефакты
- `backend/core/exceptions.py`
- `backend/api/v1/exception_handler.py`
- `backend/ui/views/error_handlers.py`
- `backend/templates/errors/403.html`, `404.html`, `500.html`
- `tests/api/test_error_handling.py`

### Валидация
```bash
pytest tests/api/test_error_handling.py
curl -X POST http://localhost:8001/api/v1/companies/ -d '{}'  # 400 с shape
```

### Откат
```bash
git revert
```

### Обновить в документации
- `docs/api/errors.md`: полный перечень error codes
- `docs/decisions.md`: ADR-009 «Error handling strategy»

---

## Этап 1.7. Cleanup orphan code + dead imports

### Контекст
Накопилось: неиспользуемые utility функции, мёртвые templates, старые JS-файлы, дублирующиеся CSS-токены (`--v2-*` vs `--v3-*`).

### Цель
Очистить проект от мёртвого кода. Метрика: LOC уменьшается минимум на 10%.

### Что делать
1. **Dead imports**: `ruff check --select F401 --fix`.
2. **Unused functions**: `vulture backend/ --min-confidence 80`. Руками проверить и удалить.
3. **Orphan templates**: скрипт, который проходит по всем .html и проверяет, используется ли в views. Неиспользуемые — удалить.
4. **Orphan static**: аналогично для JS/CSS.
5. **CSS tokens v2→v3**:
   - Найти все `--v2-*` использования.
   - Для каждого — либо заменить на v3 эквивалент, либо удалить из token-файла если токен нигде не используется.
6. **unused migrations**: проверить на ignored migrations (redundant operations).

### Инструменты
- `Bash` — vulture, grep, find
- `Agent` — параллельный анализ templates/static

### Definition of Done
- [ ] LOC уменьшился минимум на 10% (сравнить с baseline Wave 0)
- [ ] `ruff check --select F` — 0 ошибок
- [ ] Нет orphan templates / static
- [ ] Нет `--v2-*` токенов в активных файлах (только `--v3-*`)
- [ ] Все тесты зелёные

### Артефакты
- Отчёт `docs/audit/cleanup-report.md`: что удалено, LOC delta

### Валидация
```bash
cloc backend/ --exclude-dir=migrations,.git,node_modules
vulture backend/ --min-confidence 80 | wc -l  # должно быть << baseline
```

### Откат
```bash
git revert
```

### Обновить в документации
- `docs/audit/cleanup-report.md`

---

## Этап 1.8. Финальная проверка: performance regression

### Контекст
Рефакторинг мог случайно ухудшить перформанс. Нужно сверить с baseline (Wave 0.6).

### Цель
Убедиться, что после всей Wave 1 performance не просел.

### Что делать
1. Прогнать ту же pg_stat_statements выборку, сравнить с baseline.
2. Прогнать k6 нагрузочные тесты.
3. Lighthouse на ключевых страницах.
4. Если регрессия — задокументировать в `docs/audit/wave1-perf-regression.md` и либо откатить, либо добавить в backlog Wave 13.

### Инструменты
- `mcp__postgres__*`
- `mcp__playwright__*` — Lighthouse

### Definition of Done
- [ ] Сравнение с baseline сделано
- [ ] Регрессии (если есть) задокументированы
- [ ] Критичные регрессии (>20% ухудшение) — исправлены или откачены

### Артефакты
- `docs/audit/wave1-perf-comparison.md`

### Валидация
Human review.

### Откат
Только в случае критической регрессии.

### Обновить в документации
- `docs/current-sprint.md`: Wave 1 завершена

---

## Checklist завершения волны 1

- [ ] Все 8 этапов пройдены
- [ ] `_base.py` ≤ 100 LOC
- [ ] `company_detail.py` разрезан на pages/
- [ ] `api/v1/*` полный CRUD для основных сущностей
- [ ] `amocrm/` app удалён
- [ ] mypy strict для новых модулей зелёный
- [ ] Error handling унифицирован
- [ ] Cleanup снизил LOC на 10%+
- [ ] Performance не просел

**Только после этого** — переход к Wave 2 (Security).
