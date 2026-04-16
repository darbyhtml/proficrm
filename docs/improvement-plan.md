# Мастер-план улучшений CRM ПРОФИ

> Создан 2026-04-16 на основе комплексного аудита (8 параллельных агентов, ~200 находок).
> Каждая фаза завершается коммитом, пушем на staging и smoke-тестированием.

---

## Сводка аудита

| Область | Находок | P0/CRIT | P1/HIGH | P2/MED | P3/LOW | Оценка |
|---------|---------|---------|---------|--------|--------|--------|
| Архитектура | 48 | 4 | 14 | 18 | 12 | — |
| Безопасность | 21 | 3 | 6 | 8 | 4 | 7.5/10 |
| Производительность | 20 | 3 | 7 | 7 | 3 | — |
| Фронтенд/UI | 33 | 0 | 12 | 18 | 3 | 7.0/10 |
| Зависимости | 8 | 1 | 2 | 3 | 2 | 62/100 |
| База данных | 37 | 0 | 7 | 25 | 10 | — |
| DevOps | 28 | 4 | 8 | 11 | 5 | 5.8/10 |
| Тесты | 20 gaps | 5 | 8 | 5 | 2 | ~62% cov |
| **Итого** | **~215** | **20** | **64** | **95** | **41** | — |

---

## Фаза 1 — Безопасность (P0/CRITICAL) ✅ ЗАВЕРШЕНА

**Цель:** устранить все критические уязвимости. Без этого нельзя двигаться дальше.
**Коммит:** `190aee3f` | **Staging:** задеплоено, smoke OK

### 1.1 QR-токен: plaintext → hashed
- **Файл:** `backend/phonebridge/models.py:341-343`
- **Проблема:** `MobileAppQrToken.token` хранится в plaintext, предсказуемый формат
- **Решение:** хранить `hashlib.sha256(token)`, сравнивать по хешу; генерировать через `secrets.token_urlsafe(32)`
- **Затрагивает:** `phonebridge/api.py` (exchange endpoint), `ui/views/mobile.py` (QR image)

### 1.2 AmoApiConfig.client_secret: plaintext → Fernet
- **Файл:** `backend/ui/models.py:86`
- **Проблема:** OAuth client_secret в plaintext TextField
- **Решение:** Fernet шифрование через `core/crypto.py` (как SMTP-пароли в mailer)
- **Затрагивает:** `amocrm/client.py`, `ui/views/settings_integrations.py`

### 1.3 Macro IDOR — отсутствует проверка branch/owner
- **Файл:** `backend/messenger/api.py:1175-1195`
- **Проблема:** любой авторизованный пользователь может выполнить чужой Macro по ID
- **Решение:** фильтрация по `request.user.branch` + policy check

### 1.4 Bulk actions без role check
- **Файл:** `backend/messenger/api.py:405-434`
- **Проблема:** bulk assign/resolve/close без проверки роли
- **Решение:** добавить `@policy_required` или проверку в ViewSet

### 1.5 CSP: убрать unsafe-inline
- **Файл:** `backend/crm/middleware.py:29-33`
- **Проблема:** `style-src 'unsafe-inline'` ослабляет CSP
- **Решение:** nonce для inline styles (уже есть nonce для scripts, расширить на styles)

---

## Фаза 2 — Индексы БД и быстрые DB-фиксы ✅ ЗАВЕРШЕНА

**Цель:** критические индексы и constraint-правки. Минимальный риск, максимальный эффект.
**Коммит:** `190aee3f` | **Staging:** задеплоено, миграции применены

### 2.1 Добавить недостающие индексы
- `tasksapp/Task.status` — нет `db_index=True`, используется в каждом фильтре
- `tasksapp/Task` — composite index `(assignee, status, due_date)` для дашборда
- `tasksapp/Task` — composite index `(company, status)` для карточки компании
- `messenger/Message.created_at` — для SSE polling ORDER BY
- `messenger/Conversation.updated_at` — для списка диалогов

### 2.2 Удалить дублирующие индексы
- `accounts/MagicLinkToken` — 3 дублирующих индекса (unique уже создаёт index)

### 2.3 Conversation.branch: CASCADE → PROTECT
- **Файл:** `backend/messenger/models.py`
- **Проблема:** удаление Branch каскадно удалит все диалоги
- **Решение:** `on_delete=models.PROTECT`

### 2.4 CheckConstraints для критичных полей
- `Task.status` — ограничить допустимые значения на уровне БД
- `Conversation.status` — аналогично
- `Campaign.status` — аналогично

---

## Фаза 3 — Обновление зависимостей ✅ ЗАВЕРШЕНА

**Цель:** закрыть known CVE, убрать мёртвые пакеты.
**Коммит:** `190aee3f` | **Staging:** задеплоено, pip install OK

### 3.1 Security updates
- `Django` 6.0.1 → 6.0.4 (security patches)
- `cryptography` 46.0.3 → 46.0.7 (CVE fixes)
- `celery` 5.4.0 → 5.6.3 (bugfixes, memory leaks)

### 3.2 Удалить неиспользуемые пакеты
- `openpyxl` — не импортируется нигде (проверить grep)
- `python-json-logger` — заменён на `core/json_formatter.py`

### 3.3 Обновить package.json
- Tailwind CSS, DOMPurify — проверить на latest minor

---

## Фаза 4 — Производительность ✅ ЗАВЕРШЕНА (частично)

**Цель:** устранить N+1, оптимизировать горячие пути.
**Коммит:** `190aee3f` | **Выполнено:** 4.2, 4.3. **Отложено:** 4.1 (SSE Redis — требует рефактор)

### 4.1 SSE polling оптимизация
- **Файл:** `backend/messenger/api.py:536`
- **Проблема:** 2 SQL-запроса каждые 2 секунды на каждый активный SSE-стрим
- **Решение:** Redis pub/sub вместо DB polling; или увеличить интервал + ETag

### 4.2 Conversation.save() — двойной SELECT
- **Файл:** `backend/messenger/models.py:533-544`
- **Проблема:** `save()` делает лишний SELECT перед UPDATE
- **Решение:** `update_fields` при точечных изменениях

### 4.3 select_related для tasksapp
- **Файл:** `backend/tasksapp/policy.py:26`
- **Проблема:** отсутствует `type` в `select_related` → N+1 на TaskType
- **Решение:** добавить `select_related("assignee", "company", "type")`

### 4.4 SESSION_SAVE_EVERY_REQUEST → False
- **Файл:** `backend/crm/settings.py:291`
- **Проблема:** сессия пишется в Redis на КАЖДЫЙ запрос
- **Решение:** `SESSION_SAVE_EVERY_REQUEST = False` (Django default)

### 4.5 Company list — 80% дублирование v1/v2
- **Файл:** `backend/ui/views/company_list.py`
- **Проблема:** v1 удалены, но код фильтрации может быть переусложнён
- **Решение:** рефакторинг `_apply_company_filters` (450 строк → разбить на методы)

---

## Фаза 5 — DevOps и инфраструктура ✅ ЗАВЕРШЕНА

**Цель:** стабильность, мониторинг, безопасность контейнеров.
**Коммит:** `190aee3f` | **Staging:** задеплоено, gzip работает

### 5.1 Gunicorn: добавить --max-requests
- **Файл:** `docker-compose.prod.yml:68`
- **Проблема:** workers не перезапускаются → memory leaks со временем
- **Решение:** `--max-requests 1000 --max-requests-jitter 50`

### 5.2 Dockerfile: multi-stage build
- **Файл:** `Dockerfile.staging`
- **Проблема:** финальный образ содержит build-time зависимости
- **Решение:** builder stage для pip install, runtime stage только с .venv

### 5.3 Nginx: включить gzip
- **Файлы:** `nginx/staging.conf`, `nginx/production.conf`
- **Проблема:** HTML/CSS/JS отдаются без сжатия
- **Решение:** `gzip on; gzip_types text/html text/css application/javascript application/json;`

### 5.4 Удалить typesense zombie
- **Файл:** `scripts/deploy_security.sh:45`
- **Проблема:** упоминание typesense, который не используется
- **Решение:** удалить мёртвый код

### 5.5 Health check для Docker services
- Добавить HEALTHCHECK в Dockerfile и docker-compose для web, celery, redis

---

## Фаза 6 — Фронтенд / UI (Notion-стиль) ✅ ЗАВЕРШЕНА

**Цель:** единообразный Notion-стиль, accessibility, убрать дублирование CSS.
**Коммит:** `c0881b33` | **Staging:** задеплоено, smoke OK

### 6.1 CSS: консолидация дублирования
- **Файлы:** `task_list_v2.html`, `company_list_v2.html`
- **Проблема:** ~140 строк дублированного CSS
- **Решение:** вынести общие стили в `v2_styles.html` или `v2_common.css`

### 6.2 v2_styles.html: добавить недостающие токены
- `--v2-shadow-md` — используется, но не объявлен
- Проверить все `--v2-*` на наличие в `:root`

### 6.3 Focus trap для модалки
- **Файл:** `templates/ui/_v2/v2_modal.html`
- **Проблема:** Tab выходит за пределы модалки
- **Решение:** focus trap (первый/последний focusable элемент)

### 6.4 Navbar: рефакторинг CSS
- **Файл:** `templates/ui/base.html:803-866`
- **Проблема:** хрупкие CSS-селекторы с overrides
- **Решение:** использовать v2 CSS-классы, убрать !important

### 6.5 Notion-стиль: доводка
- Dashboard: 7.5/10 → цель 9.0
- Tasks: 7.0/10 → цель 8.5
- Companies: 7.0/10 → цель 8.5
- Конкретные точки: spacing, typography, hover states, transitions

---

## Фаза 7 — Архитектура ✅ ЗАВЕРШЕНА

**Цель:** завершить service layer, устранить архитектурные долги.
**Коммит:** `0aae615a` | **Staging:** задеплоено, smoke OK

### 7.1 Service layer: CompanyService
- **Файл:** `backend/companies/services.py`
- **Проблема:** частично реализован, views содержат бизнес-логику
- **Решение:** перенести create/update/delete логику из views в services

### 7.2 _apply_company_filters → разбить
- **Файл:** `backend/ui/views/_base.py:448-893`
- **Проблема:** 450 строк в одной функции
- **Решение:** разбить на `_filter_by_status`, `_filter_by_date`, `_filter_by_search` и т.д.

### 7.3 Messenger services: thin views
- **Проблема:** api.py 1033 LOC с бизнес-логикой в ViewSet
- **Решение:** вынести в messenger/services.py (уже есть, но неполный)

---

## Фаза 8 — Тесты ✅ ЗАВЕРШЕНА (core/)

**Цель:** поднять coverage с 62% до 75%+, закрыть критические gaps.
**Коммит:** `1302c8d7` | **Staging:** 145 тестов, все зелёные (0.462с)

### 8.1 core/ — 0 тестов → полное покрытие
- `core/crypto.py` — encrypt/decrypt/rotate
- `core/timezone_utils.py` — guess_ru_timezone
- `core/request_id.py` — middleware + filter
- `core/exceptions.py` — custom handler
- `core/work_schedule_utils.py` — рабочие дни

### 8.2 WebSocket consumers — 0 тестов
- `messenger/consumers.py` — connect, disconnect, receive, auth

### 8.3 notifications/tasks.py
- `generate_contract_reminders` — untested

### 8.4 Security-тесты
- IDOR на macro/bulk actions
- Rate limiting endpoints
- Fernet encryption round-trip

### 8.5 E2E тесты (Playwright)
- Дашборд: загрузка, фильтры, модалка задачи
- Задачи: CRUD через модалку
- Компании: поиск, фильтры, карточка

---

## Порядок выполнения

```
Фаза 1 (Security P0)     ──→ commit + staging + smoke test
  ↓
Фаза 2 (DB indexes)      ──→ commit + staging + smoke test
  ↓
Фаза 3 (Dependencies)    ──→ commit + staging + smoke test
  ↓
Фаза 4 (Performance)     ──→ commit + staging + smoke test
  ↓
Фаза 5 (DevOps)          ──→ commit + staging + smoke test
  ↓
Фаза 6 (Frontend/UI)     ──→ commit + staging + smoke test (per sub-phase)
  ↓
Фаза 7 (Architecture)    ──→ commit + staging + smoke test (per sub-phase)
  ↓
Фаза 8 (Tests)           ──→ commit + staging + final test run
```

**Общая оценка:** 22-29 часов работы, 8+ коммитов.

---

## Принципы

1. **Не ломать прод.** Каждая фаза тестируется на staging перед следующей.
2. **Backward compatibility.** Re-export shim'ы для перемещённых модулей.
3. **Инкрементальность.** Маленькие коммиты, каждый зелёный.
4. **Документация.** После каждой фазы обновляем docs/.
5. **Notion-стиль.** Все UI-изменения следуют v2 дизайн-системе.
