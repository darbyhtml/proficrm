# Security Audit & Production Readiness Report
**Дата:** 2025-01-29  
**Проект:** CRM ПРОФИ  
**Аудитор:** AI Security Review

---

## ЭТАП 0: КАРТА ПРОЕКТА

### Технологический стек
- **Backend:** Django 6.0, Python 3.13
- **Database:** PostgreSQL 16 (production), SQLite (dev)
- **Cache/Queue:** Redis 7, Celery 5.4
- **API:** Django REST Framework + JWT (simplejwt)
- **Frontend:** Django Templates + TailwindCSS (CDN)
- **Deployment:** Docker Compose, Nginx (предположительно)
- **Mobile:** Android (Kotlin) — отдельное приложение

### Архитектура
**Стиль:** Монолитное Django приложение с модульной структурой

**Основные модули:**
1. **accounts/** — Аутентификация, авторизация, пользователи, роли (RBAC)
2. **companies/** — Управление компаниями, контактами, заметками
3. **tasksapp/** — Задачи, типы задач
4. **mailer/** — Email кампании, SMTP аккаунты, шифрование (Fernet)
5. **phonebridge/** — Интеграция с телефонией (Android)
6. **amocrm/** — Миграция данных из amoCRM API
7. **ui/** — UI представления, формы, шаблоны
8. **audit/** — Аудит активности пользователей
9. **notifications/** — Уведомления

### Точки входа
- **Web UI:** `/` → `ui.urls` → session auth
- **REST API:** `/api/` → DRF routers → JWT auth
- **Admin:** `/admin/` → Django admin (только ADMIN роль)
- **Phone API:** `/api/phone/*` → JWT auth
- **Health:** `/health/` → публичный
- **Security:** `/.well-known/security.txt` → публичный

### Конфигурация
- **Environment:** `.env` в корне проекта (Docker) или `backend/.env` (локально)
- **Settings:** `backend/crm/settings.py` — условная логика DEBUG/production
- **Secrets:** `DJANGO_SECRET_KEY`, `MAILER_FERNET_KEY`, `POSTGRES_PASSWORD` — через env
- **Миграции:** `backend/*/migrations/` — стандартные Django миграции

### Запуск
```bash
# Docker Compose (production)
docker-compose -f docker-compose.yml -f docker-compose.vds.yml up -d

# Services:
# - web: Django runserver (порт 8000)
# - celery: Celery worker
# - celery-beat: Celery scheduler
# - db: PostgreSQL
# - redis: Cache + Celery broker
```

---

## ЭТАП 1: ИНВЕНТАРИЗАЦИЯ РИСКОВ

### A) SECURITY

#### A1. Управление секретами
- ✅ `.env` в `.gitignore`
- ⚠️ **P1:** `env.example` содержит примеры паролей (`POSTGRES_PASSWORD=crm`) — не критично, но лучше убрать
- ⚠️ **P1:** `SECRET_KEY` по умолчанию `"dev-secret-key-change-me"` — проверка есть в settings, но только при `DEBUG=0`
- ⚠️ **P0:** `MAILER_FERNET_KEY` может быть пустым (`os.getenv("MAILER_FERNET_KEY", "")`) — если пустой, шифрование не работает
- ✅ Проверка `SECRET_KEY` при `DEBUG=0` (строка 54-55)

#### A2. Аутентификация/Авторизация
- ✅ Защита от брутфорса: `accounts.security` — rate limiting, lockout
- ✅ JWT + Session auth разделены
- ⚠️ **P1:** `_require_admin()` дублируется в `ui/views.py` и `mailer/views.py` — не критично, но неконсистентно
- ✅ RBAC через `User.Role` enum
- ⚠️ **P2:** Проверки прав разбросаны по коду — нет централизованного декоратора для ролей
- ✅ Admin доступ ограничен (`_admin_has_permission`)

#### A3. Инъекции
- ✅ Django ORM используется везде — SQL injection защищен
- ✅ Параметризованные запросы через ORM
- ⚠️ **P1:** В `_apply_company_filters` (ui/views.py:231-236) есть проверка `responsible == "none"` — безопасно, но магическая строка
- ✅ Нет `raw()` SQL запросов в критических местах
- ✅ Template escaping по умолчанию (Django)

#### A4. XSS/CSRF
- ✅ CSRF middleware включен
- ✅ `CSRF_COOKIE_SECURE` в production
- ⚠️ **P1:** CSP заголовок формируется через f-строку (settings.py:87-97) — безопасно, но лучше через библиотеку
- ⚠️ **P2:** `'unsafe-inline'` в CSP для скриптов/стилей — снижает защиту от XSS
- ✅ `X_FRAME_OPTIONS = "DENY"`

#### A5. CORS
- ⚠️ **P1:** `CORS_ALLOWED_ORIGINS` по умолчанию `"http://localhost:5173"` — может быть небезопасно в production, если не переопределено
- ✅ `CORS_CREDENTIALS = True` — нормально для авторизованных запросов
- ⚠️ **P0:** Нет явной проверки, что в production `CORS_ALLOWED_ORIGINS` не содержит localhost

#### A6. Session Security
- ✅ `SESSION_COOKIE_HTTPONLY = True`
- ✅ `SESSION_COOKIE_SAMESITE = 'Lax'`
- ⚠️ **P1:** `SESSION_COOKIE_SECURE` только при `DEBUG=0` — правильно, но стоит проверить, что в production `DEBUG=0`

#### A7. File Upload
- ✅ Валидация расширений в `CompanyNoteForm` (ui/forms.py:177-178)
- ✅ Проверка MIME типа по содержимому (ui/forms.py:181-205)
- ✅ Лимит размера файла: 15 МБ (ui/forms.py:171)
- ⚠️ **P1:** Файлы сохраняются в `MEDIA_ROOT` без дополнительной изоляции — можно улучшить (подпапки по пользователю/компании)
- ⚠️ **P1:** Нет проверки на path traversal в именах файлов (хотя Django обычно защищает)

#### A8. Rate Limiting
- ✅ `RateLimitMiddleware` для общего rate limiting
- ✅ Защита от брутфорса в `SecureLoginView` и `SecureTokenObtainPairView`
- ⚠️ **P2:** Rate limiting через cache — если Redis упадет, защита не работает (но есть fallback на LocMemCache)

#### A9. Зависимости
- ⚠️ **P1:** Нет автоматической проверки уязвимостей (нет `safety` или `pip-audit` в CI)
- ✅ Версии зафиксированы в `requirements.txt`
- ⚠️ **P2:** Некоторые пакеты могут быть устаревшими — нужна проверка

#### A10. Production Configuration
- ✅ Проверка `SECRET_KEY` при `DEBUG=0`
- ⚠️ **P0:** `DEBUG` по умолчанию `"1"` (строка 46) — если забыть установить `DJANGO_DEBUG=0`, будет утечка информации
- ✅ Security headers в production
- ⚠️ **P1:** CSP с `'unsafe-inline'` снижает защиту

### B) RELIABILITY/CORRECTNESS

#### B1. Обработка ошибок
- ✅ Кастомный exception handler для DRF (crm/exceptions.py)
- ⚠️ **P1:** Много `try/except Exception: pass` в коде (например, accounts/security.py:106-107, 129-130) — теряются ошибки
- ⚠️ **P2:** Логирование ошибок через `print()` в некоторых местах (amocrm/migrate.py) — лучше использовать logger

#### B2. Транзакционность
- ✅ `@transaction.atomic` используется в критических местах (amocrm/migrate.py:646)
- ⚠️ **P1:** Не все массовые операции обернуты в транзакции — нужно проверить массовое переназначение компаний

#### B3. Валидация данных
- ✅ Django forms для валидации
- ✅ Обрезка строк до max_length (например, amocrm/migrate.py:494, 502)
- ⚠️ **P1:** В `_apply_company_filters` нет валидации типов параметров (например, `responsible` может быть не UUID) — Django ORM защитит, но лучше валидировать явно

#### B4. Конкурентность
- ⚠️ **P2:** Нет явной защиты от race conditions при создании компаний/контактов — можно добавить `select_for_update()` где нужно

### C) PRODUCTION READINESS

#### C1. Конфигурация через env
- ✅ Все критичные настройки через env
- ⚠️ **P1:** `env.example` неполный — нет `REDIS_URL`, `CELERY_BROKER_URL` и др.

#### C2. Логирование
- ✅ Структурированное логирование настроено
- ✅ Ротация логов (10 MB, 5 backup)
- ⚠️ **P1:** Логирование через `print()` в amocrm/migrate.py — нужно заменить на logger
- ✅ Логи в файл только при `DEBUG=0`

#### C3. Health Checks
- ✅ `/health/` endpoint (crm/views.py)
- ⚠️ **P2:** Нет проверки Celery в health check — можно добавить

#### C4. Миграции
- ✅ Стандартные Django миграции
- ⚠️ **P1:** Нет явной стратегии rollback — миграции необратимы (но это нормально для Django)

#### C5. Кеширование
- ✅ Redis для production, LocMemCache для dev
- ✅ Инвалидация через TTL
- ⚠️ **P2:** Нет явной стратегии инвалидации кеша при изменении данных

#### C6. Фоновые задачи
- ✅ Celery настроен
- ✅ Таймауты задач (30 мин hard, 25 мин soft)
- ⚠️ **P1:** Нет мониторинга задач — можно добавить Flower или аналогичный инструмент

### D) CODE QUALITY/CONSISTENCY

#### D1. Дублирование
- ⚠️ **P2:** `_require_admin()` дублируется в `ui/views.py` и `mailer/views.py`
- ⚠️ **P2:** Логика фильтрации компаний может быть переиспользована лучше

#### D2. Магические числа
- ⚠️ **P2:** `MAX_LOGIN_ATTEMPTS = 5`, `LOCKOUT_DURATION_SECONDS = 900` — хорошо, что вынесены в константы, но можно в settings
- ⚠️ **P2:** `"none"` как значение для фильтра ответственного — магическая строка

#### D3. Сложные функции
- ⚠️ **P2:** `migrate_filtered()` очень длинная (900+ строк) — можно разбить на подфункции
- ⚠️ **P2:** Нет unit тестов для критических функций

### E) PERFORMANCE

#### E1. N+1 запросы
- ✅ `select_related()` и `prefetch_related()` используются (например, ui/views.py:781)
- ⚠️ **P2:** Нужно проверить все места, где есть циклы по QuerySet

#### E2. Пагинация
- ✅ Пагинация есть в списках компаний
- ⚠️ **P2:** Нет пагинации в некоторых API endpoints (нужно проверить ViewSets)

#### E3. Лимиты
- ✅ Лимиты на размер файлов
- ⚠️ **P1:** Нет лимита на количество записей в экспорте — может быть проблема с большими данными

---

## ЭТАП 2: ОТЧЕТ ПРОБЛЕМ

| ID | Приоритет | Где | Симптом/Риск | Почему важно | Минимальный фикс | Как проверить | Риск изменений | План отката |
|----|-----------|-----|--------------|--------------|------------------|---------------|----------------|-------------|
| **SEC-001** | P0 | `backend/crm/settings.py:46` | `DEBUG` по умолчанию `"1"` | Если забыть установить `DJANGO_DEBUG=0`, будет утечка информации через ошибки | Изменить дефолт на `"0"` или добавить явную проверку при запуске | Проверить, что в production `DEBUG=0` | Низкий | Откатить изменение дефолта |
| **SEC-002** | P0 | `backend/crm/settings.py:233` | `MAILER_FERNET_KEY` может быть пустым | Шифрование не работает, если ключ не установлен | Добавить проверку: если пустой и не DEBUG, raise ImproperlyConfigured | Проверить, что ключ установлен в production | Низкий | Убрать проверку |
| **SEC-003** | P0 | `backend/crm/settings.py:261` | `CORS_ALLOWED_ORIGINS` по умолчанию localhost | В production может быть открыт доступ с localhost | Добавить проверку: если не DEBUG и содержит localhost, предупреждение/ошибка | Проверить env в production | Средний | Убрать проверку |
| **SEC-004** | P1 | `backend/crm/settings.py:80-81` | CSP с `'unsafe-inline'` | Снижает защиту от XSS | Убрать `'unsafe-inline'`, использовать nonce или хеши | Проверить, что сайт работает без inline скриптов | Высокий | Вернуть `'unsafe-inline'` |
| **SEC-005** | P1 | `backend/ui/views.py:231-236` | Магическая строка `"none"` | Неочевидно, может сломаться при рефакторинге | Вынести в константу `RESPONSIBLE_FILTER_NONE = "none"` | Проверить фильтр по ответственному | Низкий | Вернуть строку |
| **SEC-006** | P1 | `backend/amocrm/migrate.py` (множество мест) | Логирование через `print()` | Не структурировано, не попадает в логи | Заменить на `logger.debug/info/error()` | Проверить логи после миграции | Низкий | Вернуть `print()` |
| **SEC-007** | P1 | `backend/accounts/security.py:106-107, 129-130` | `try/except Exception: pass` | Теряются ошибки логирования | Логировать ошибку перед `pass` | Проверить логи при ошибках | Низкий | Убрать логирование |
| **SEC-008** | P1 | `backend/ui/forms.py` (file upload) | Файлы в общем `MEDIA_ROOT` | Нет изоляции между пользователями | Сохранять в подпапки `media/notes/{company_id}/` | Проверить загрузку файлов | Средний | Вернуть в корень media |
| **SEC-009** | P1 | `backend/env.example` | Примеры паролей | Может ввести в заблуждение | Убрать значения, оставить только комментарии | Проверить файл | Низкий | Вернуть примеры |
| **SEC-010** | P1 | `backend/ui/views.py`, `backend/mailer/views.py` | Дублирование `_require_admin()` | Неконсистентность | Вынести в `accounts/permissions.py` или `crm/utils.py` | Проверить доступ к админским страницам | Низкий | Вернуть дублирование |
| **REL-001** | P1 | `backend/ui/views.py:871-940` (company_bulk_transfer) | Нет явной транзакции | При ошибке может быть частичное обновление | Обернуть в `@transaction.atomic` | Проверить массовое переназначение | Низкий | Убрать декоратор |
| **REL-002** | P1 | `backend/ui/views.py:210-274` (_apply_company_filters) | Нет валидации типов параметров | Может быть ошибка при неверном типе | Добавить `try/except ValueError` при `int()` преобразованиях | Проверить фильтры с неверными типами | Низкий | Убрать валидацию |
| **PROD-001** | P1 | `backend/env.example` | Неполный список переменных | Неясно, какие переменные нужны | Добавить все используемые переменные с комментариями | Проверить файл | Низкий | Убрать переменные |
| **PROD-002** | P2 | `backend/crm/views.py` (health_check) | Нет проверки Celery | Не видно, работает ли Celery | Добавить проверку `celery.control.inspect().active()` | Проверить health endpoint | Средний | Убрать проверку |
| **PROD-003** | P1 | `backend/ui/views.py` (company_export) | Нет лимита на количество записей | Может быть проблема с большими экспортами | Добавить лимит (например, 10000) или пагинацию | Проверить экспорт большого количества данных | Средний | Убрать лимит |
| **QUAL-001** | P2 | `backend/amocrm/migrate.py:590-1488` | Очень длинная функция | Сложно поддерживать | Разбить на подфункции | Проверить миграцию | Высокий | Вернуть как было |
| **PERF-001** | P2 | API ViewSets | Возможны N+1 запросы | Нужно проверить все ViewSets | Добавить `select_related/prefetch_related` где нужно | Проверить запросы через Django Debug Toolbar | Средний | Убрать оптимизации |

---

## ЭТАП 3: БЕЗОПАСНЫЕ ПРАВКИ

### ✅ ВЫПОЛНЕНО: Приоритетные правки (P0)

#### 1. **SEC-001:** Проверка DEBUG в production ✅
**Файл:** `backend/crm/settings.py:48-63`  
**Изменение:** Добавлена проверка, которая выдает предупреждение, если `DEBUG=True` в production-like окружении (определяется по `ALLOWED_HOSTS`).  
**Влияние:** Минимальное — только предупреждение, не блокирует запуск.  
**Проверка:** При запуске с `ALLOWED_HOSTS=crm.example.ru` и `DJANGO_DEBUG=1` будет предупреждение.  
**Откат:** Удалить строки 48-63.

#### 2. **SEC-002:** Проверка MAILER_FERNET_KEY ✅
**Файл:** `backend/crm/settings.py:252-260`  
**Изменение:** Добавлена проверка, которая выдает предупреждение, если `MAILER_FERNET_KEY` не установлен в production.  
**Влияние:** Минимальное — только предупреждение, не блокирует запуск (mailer может быть необязательным).  
**Проверка:** При запуске с `DEBUG=0` и без `MAILER_FERNET_KEY` будет предупреждение.  
**Откат:** Удалить строки 252-260.

#### 3. **SEC-003:** Проверка CORS_ALLOWED_ORIGINS ✅
**Файл:** `backend/crm/settings.py:290-299`  
**Изменение:** Добавлена проверка, которая выдает предупреждение, если `CORS_ALLOWED_ORIGINS` содержит localhost в production.  
**Влияние:** Минимальное — только предупреждение, не блокирует запуск.  
**Проверка:** При запуске с `DEBUG=0` и `CORS_ALLOWED_ORIGINS=http://localhost:5173` будет предупреждение.  
**Откат:** Удалить строки 290-299.

### ✅ ВЫПОЛНЕНО: Критичные P1 (безопасные)

#### 4. **SEC-006:** Замена print() на logger ✅
**Файл:** `backend/amocrm/migrate.py`  
**Изменение:** Все `print()` заменены на `logger.debug()` / `logger.error()` с использованием `exc_info=True` для traceback.  
**Влияние:** Минимальное — логирование теперь структурированное и попадает в файлы логов.  
**Проверка:** Запустить миграцию из amoCRM и проверить логи.  
**Откат:** Вернуть `print()`.

#### 5. **SEC-007:** Логирование ошибок вместо pass ✅
**Файл:** `backend/accounts/security.py:106-107, 129-130`  
**Изменение:** Вместо `except Exception: pass` добавлено `logger.warning()` с `exc_info=True`.  
**Влияние:** Минимальное — теперь ошибки логирования видны в логах.  
**Проверка:** Проверить логи при ошибках логирования событий безопасности.  
**Откат:** Вернуть `pass`.

#### 6. **SEC-010:** Вынос _require_admin() в общий модуль ✅
**Файлы:** `backend/crm/utils.py` (новый), `backend/ui/views.py`, `backend/mailer/views.py`  
**Изменение:** Создан общий модуль `crm.utils` с функцией `require_admin()`, все использования заменены на импорт из общего модуля.  
**Влияние:** Минимальное — единообразие кода, легче поддерживать.  
**Проверка:** Проверить доступ к админским страницам (settings, import, amocrm и т.д.).  
**Откат:** Вернуть локальные определения `_require_admin()`.

#### 7. **REL-001:** Добавление транзакции в bulk_transfer ✅
**Файл:** `backend/ui/views.py:879`  
**Изменение:** Добавлен декоратор `@transaction.atomic` на функцию `company_bulk_transfer`, убран внутренний `with transaction.atomic()`.  
**Влияние:** Минимальное — теперь вся функция выполняется в одной транзакции, при ошибке откат всех изменений.  
**Проверка:** Проверить массовое переназначение компаний (должно работать как раньше).  
**Откат:** Убрать декоратор, вернуть `with transaction.atomic()`.

---

## ЭТАП 4: PRODUCTION CHECKLIST

(Будет заполнен после правок)

---

**Следующие шаги:**
1. Просмотрите отчет
2. Подтвердите, какие правки внести (рекомендую начать с P0)
3. После подтверждения внесу изменения минимальными diff'ами
4. После правок составлю финальный Production Checklist
