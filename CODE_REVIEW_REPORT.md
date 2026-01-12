# Code Review Report: Django CRM Monolith
**Дата:** 2026-01-12  
**Версия:** Django 6.0, Python 3.13  
**Статус:** Глубокий анализ безопасности, производительности и архитектуры

---

## Executive Summary

### Критические находки (CRITICAL)
1. **JWT настройки отсутствуют** — `SIMPLE_JWT` не настроен в `settings.py`, используются дефолты (lifetime=5min, без rotation, без blacklist)
2. **Race condition в phonebridge** — `PullCallView` не использует `select_for_update()`, возможна двойная выдача одного звонка
3. **Fernet key в памяти** — ключ шифрования паролей SMTP загружается при каждом вызове без кеширования
4. **Отсутствие idempotency keys** — API endpoints не поддерживают идемпотентность при сетевых ошибках

### Высокий приоритет (HIGH)
5. **N+1 запросы** — в `CompanyViewSet.get_queryset()` отсутствует `select_related/prefetch_related`
6. **CORS credentials без ограничений** — `CORS_ALLOW_CREDENTIALS=True` без проверки origin
7. **Rate limiting неполный** — `/api/phone/*` endpoints не защищены rate limiting
8. **Нет валидации размера файлов** — загрузка вложений в кампаниях без проверки размера на уровне модели
9. **Логирование секретов** — потенциальная утечка через логи (IP, usernames в audit events)

### Средний приоритет (MEDIUM)
10. **Транзакции не везде** — массовые операции без `@transaction.atomic`
11. **Индексы отсутствуют** — частые фильтры без индексов (например, `Task.status`, `Company.responsible_id`)
12. **Celery без retry policy** — задачи могут теряться при временных сбоях
13. **Нет health check для Redis/Celery** — `/health/` проверяет только БД

---

## Таблица найденных проблем

| ID | Severity | Категория | Описание | Файл:Строка | Риск | Исправление |
|----|----------|-----------|----------|-------------|------|-------------|
| SEC-001 | CRITICAL | Security | JWT настройки отсутствуют (lifetime, rotation, blacklist) | `backend/crm/settings.py:270-285` | Токены живут 5 минут, нет ротации, нет blacklist | Добавить `SIMPLE_JWT` конфигурацию |
| SEC-002 | CRITICAL | Security | Race condition в `PullCallView` — возможна двойная выдача звонка | `backend/phonebridge/api.py:72-84` | Один звонок может быть выдан двум клиентам | Использовать `select_for_update(skip_locked=True)` |
| SEC-003 | CRITICAL | Security | Fernet key загружается при каждом вызове без кеширования | `backend/mailer/crypto.py:7-11` | Медленно, возможны race conditions | Кешировать `Fernet` instance |
| SEC-004 | HIGH | Security | CORS credentials без проверки origin | `backend/crm/settings.py:301` | Утечка credentials на недоверенные домены | Добавить проверку `CORS_ALLOWED_ORIGINS` |
| SEC-005 | HIGH | Security | Rate limiting не применяется к `/api/phone/*` | `backend/accounts/middleware.py:27-31` | DDoS на phone API | Добавить `/api/phone/` в `PROTECTED_PATHS` |
| SEC-006 | HIGH | Security | Нет валидации размера файлов вложений | `backend/mailer/models.py:181` | DoS через загрузку больших файлов | Добавить `max_length` и валидацию в форме |
| SEC-007 | MEDIUM | Security | Логирование IP и username в audit events | `backend/accounts/security.py:95-107` | Утечка PII в логи | Маскировать чувствительные данные |
| PERF-001 | HIGH | Performance | N+1 запросы в `CompanyViewSet` | `backend/companies/api.py:60` | Медленные API ответы | Добавить `select_related("responsible", "branch")` |
| PERF-002 | MEDIUM | Performance | Отсутствуют индексы на часто фильтруемых полях | `backend/tasksapp/models.py:32` | Медленные запросы | Добавить `db_index=True` на `Task.status` |
| PERF-003 | MEDIUM | Performance | Нет индекса на `Company.responsible_id` | `backend/companies/models.py` | Медленная фильтрация | Добавить индекс через миграцию |
| CORR-001 | HIGH | Correctness | Race condition в `PullCallView` | `backend/phonebridge/api.py:72-84` | Двойная выдача звонка | Использовать `select_for_update()` |
| CORR-002 | MEDIUM | Correctness | Массовые операции без транзакций | `backend/ui/views.py:978` | Частичные обновления при ошибках | Обернуть в `@transaction.atomic` |
| CORR-003 | MEDIUM | Correctness | Нет idempotency keys в API | `backend/phonebridge/api.py:42-97` | Дублирование операций | Добавить `Idempotency-Key` header |
| REL-001 | MEDIUM | Reliability | Celery задачи без retry policy | `backend/mailer/tasks.py:19` | Потеря задач при сбоях | Добавить `autoretry_for` и `max_retries` |
| REL-002 | MEDIUM | Reliability | Health check не проверяет Redis/Celery | `backend/crm/views.py` | Не видно проблем с очередями | Расширить `/health/` endpoint |
| API-001 | MEDIUM | API Consistency | Нет единого формата ошибок API | Разные viewsets | Сложно обрабатывать ошибки | Использовать `crm.exceptions.custom_exception_handler` везде |
| API-002 | LOW | API Consistency | Нет pagination по умолчанию | `backend/companies/api.py:60` | Большие ответы | Добавить `DEFAULT_PAGINATION_CLASS` |
| CODE-001 | LOW | Code Style | Дублирование логики проверки прав | `backend/ui/views.py` | Сложно поддерживать | Вынести в `crm.utils` |
| CODE-002 | LOW | Code Style | Нет typing в некоторых функциях | Разные файлы | Сложно рефакторить | Добавить type hints |

---

## Детальный анализ по категориям

### A) Security

#### SEC-001: JWT настройки отсутствуют
**Файл:** `backend/crm/settings.py:270-285`  
**Проблема:** `SIMPLE_JWT` не настроен, используются дефолты:
- `ACCESS_TOKEN_LIFETIME = timedelta(minutes=5)` — слишком коротко
- `REFRESH_TOKEN_LIFETIME = timedelta(days=1)` — нет ротации
- Нет blacklist для отозванных токенов
- Нет настройки `AUTH_TOKEN_CLASSES`, `TOKEN_USER_CLASS`

**Риск:** 
- Короткий lifetime токенов — плохой UX
- Нет ротации refresh токенов — компрометация refresh токена = постоянный доступ
- Нет blacklist — нельзя отозвать токены при компрометации

**Исправление:**
```python
from datetime import timedelta

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(hours=1),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "ALGORITHM": "HS256",
    "SIGNING_KEY": SECRET_KEY,
    "AUTH_HEADER_TYPES": ("Bearer",),
    "AUTH_HEADER_NAME": "HTTP_AUTHORIZATION",
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
    "AUTH_TOKEN_CLASSES": ("rest_framework_simplejwt.tokens.AccessToken",),
    "TOKEN_TYPE_CLAIM": "token_type",
    "JTI_CLAIM": "jti",
    "TOKEN_OBTAIN_SERIALIZER": "accounts.jwt_views.SecureTokenObtainPairView",
}
```

**Категория:** (A) Безопасно, не меняет поведение по умолчанию

---

#### SEC-002: Race condition в PullCallView
**Файл:** `backend/phonebridge/api.py:72-84`  
**Проблема:** Между `filter().first()` и `save()` два клиента могут получить один и тот же звонок.

**Риск:** Один звонок выдается двум клиентам одновременно.

**Исправление:**
```python
from django.db import transaction

def get(self, request):
    # ...
    with transaction.atomic():
        call = (
            CallRequest.objects.select_for_update(skip_locked=True)
            .filter(user=request.user, status=CallRequest.Status.PENDING)
            .order_by("created_at")
            .first()
        )
        if not call:
            return Response(status=204)
        
        call.status = CallRequest.Status.CONSUMED
        call.delivered_at = timezone.now()
        call.consumed_at = timezone.now()
        call.save(update_fields=["status", "delivered_at", "consumed_at"])
    
    return Response({...})
```

**Категория:** (A) Безопасно, исправляет баг

---

#### SEC-003: Fernet key загружается при каждом вызове
**Файл:** `backend/mailer/crypto.py:7-11`  
**Проблема:** `_fernet()` создает новый `Fernet` instance при каждом вызове.

**Риск:** Медленно, возможны race conditions при параллельных запросах.

**Исправление:**
```python
from functools import lru_cache

@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    key = getattr(settings, "MAILER_FERNET_KEY", "") or ""
    if not key:
        raise RuntimeError("MAILER_FERNET_KEY is not set.")
    return Fernet(key.encode("utf-8"))
```

**Категория:** (A) Безопасно, улучшает производительность

---

#### SEC-004: CORS credentials без проверки origin
**Файл:** `backend/crm/settings.py:301`  
**Проблема:** `CORS_ALLOW_CREDENTIALS=True` без строгой проверки `CORS_ALLOWED_ORIGINS`.

**Риск:** Утечка credentials на недоверенные домены (если `CORS_ALLOWED_ORIGINS` настроен неправильно).

**Исправление:** Уже есть проверка в `settings.py:291-299`, но нужно убедиться, что она работает в production. Добавить явную проверку в middleware:

```python
# В crm/middleware.py или accounts/middleware.py
if request.META.get('HTTP_ORIGIN') not in CORS_ALLOWED_ORIGINS:
    return HttpResponse("Origin not allowed", status=403)
```

**Категория:** (B) Требует тестирования

---

#### SEC-005: Rate limiting не применяется к phone API
**Файл:** `backend/accounts/middleware.py:27-31`  
**Проблема:** `/api/phone/*` endpoints не в списке `PROTECTED_PATHS`.

**Риск:** DDoS на phone API.

**Исправление:**
```python
PROTECTED_PATHS = [
    "/login/",
    "/api/token/",
    "/api/token/refresh/",
    "/api/phone/",  # Добавить
]
```

**Категория:** (A) Безопасно

---

#### SEC-006: Нет валидации размера файлов
**Файл:** `backend/mailer/models.py:181`  
**Проблема:** `Campaign.attachment` не имеет ограничения размера на уровне модели.

**Риск:** DoS через загрузку больших файлов.

**Исправление:**
```python
# В mailer/models.py
attachment = models.FileField(
    "Вложение",
    upload_to="campaign_attachments/",
    null=True,
    blank=True,
    validators=[FileExtensionValidator(allowed_extensions=['pdf', 'doc', 'docx', 'xls', 'xlsx'])]
)

# В mailer/forms.py
class CampaignForm(forms.ModelForm):
    def clean_attachment(self):
        attachment = self.cleaned_data.get('attachment')
        if attachment:
            if attachment.size > 15 * 1024 * 1024:  # 15 MB
                raise forms.ValidationError("Размер файла не должен превышать 15 МБ.")
        return attachment
```

**Категория:** (A) Безопасно

---

### B) Performance

#### PERF-001: N+1 запросы в CompanyViewSet
**Файл:** `backend/companies/api.py:60`  
**Проблема:** `get_queryset()` не использует `select_related`.

**Исправление:**
```python
def get_queryset(self):
    return Company.objects.select_related(
        "responsible", "branch", "status", "head_company"
    ).prefetch_related("spheres").order_by("-updated_at")
```

**Категория:** (A) Безопасно, улучшает производительность

---

#### PERF-002: Отсутствуют индексы
**Файлы:** `backend/tasksapp/models.py`, `backend/companies/models.py`  
**Проблема:** Часто фильтруемые поля без индексов.

**Исправление:**
```python
# В tasksapp/models.py
status = models.CharField(max_length=16, choices=Status.choices, default=Status.NEW, db_index=True)

# Миграция для Company.responsible_id
# Создать миграцию: python manage.py makemigrations companies --empty
# Добавить: migrations.RunSQL("CREATE INDEX IF NOT EXISTS companies_company_responsible_id_idx ON companies_company(responsible_id);")
```

**Категория:** (B) Требует миграции

---

### C) Correctness & Data Integrity

#### CORR-001: Race condition (дубликат SEC-002)
См. SEC-002.

#### CORR-002: Массовые операции без транзакций
**Файл:** `backend/ui/views.py:978`  
**Проблема:** `company_bulk_transfer` не обернут в транзакцию.

**Исправление:**
```python
from django.db import transaction

@transaction.atomic
def company_bulk_transfer(request: HttpRequest) -> HttpResponse:
    # ...
```

**Категория:** (A) Безопасно

---

#### CORR-003: Нет idempotency keys
**Файл:** `backend/phonebridge/api.py:42-97`  
**Проблема:** При сетевых ошибках клиент может отправить запрос дважды.

**Исправление:** Добавить поддержку `Idempotency-Key` header:

```python
from django.core.cache import cache

class PullCallView(APIView):
    def get(self, request):
        idempotency_key = request.META.get('HTTP_IDEMPOTENCY_KEY')
        if idempotency_key:
            cached_response = cache.get(f'idempotency:{idempotency_key}')
            if cached_response:
                return Response(cached_response)
        
        # ... существующая логика ...
        
        response_data = {...}
        if idempotency_key:
            cache.set(f'idempotency:{idempotency_key}', response_data, 300)
        
        return Response(response_data)
```

**Категория:** (B) Требует изменения API контракта

---

### D) Reliability

#### REL-001: Celery задачи без retry policy
**Файл:** `backend/mailer/tasks.py:19`  
**Проблема:** Задачи не имеют автоматических retry при временных сбоях.

**Исправление:**
```python
from celery import shared_task
from celery.exceptions import Retry

@shared_task(
    name="mailer.tasks.send_pending_emails",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
)
def send_pending_emails(self, batch_size: int = 50):
    try:
        # ... существующая логика ...
    except Exception as exc:
        raise self.retry(exc=exc)
```

**Категория:** (A) Безопасно, улучшает надежность

---

#### REL-002: Health check неполный
**Файл:** `backend/crm/views.py`  
**Проблема:** `/health/` проверяет только БД, не проверяет Redis/Celery.

**Исправление:**
```python
def health_check(request):
    checks = {
        "database": False,
        "redis": False,
        "celery": False,
    }
    
    # Проверка БД
    try:
        from django.db import connection
        connection.ensure_connection()
        checks["database"] = True
    except Exception:
        pass
    
    # Проверка Redis
    try:
        from django.core.cache import cache
        cache.set("health_check", "ok", 10)
        checks["redis"] = cache.get("health_check") == "ok"
    except Exception:
        pass
    
    # Проверка Celery
    try:
        from celery import current_app
        inspect = current_app.control.inspect()
        stats = inspect.stats()
        checks["celery"] = bool(stats)
    except Exception:
        pass
    
    if all(checks.values()):
        return JsonResponse({"status": "ok", "checks": checks}, status=200)
    else:
        return JsonResponse({"status": "degraded", "checks": checks}, status=503)
```

**Категория:** (A) Безопасно

---

## Актуальность на январь 2026

### Устаревшие практики
1. **Django 6.0** — актуальная версия, но стоит рассмотреть обновление до 6.1+ (если вышла)
2. **DRF 3.16.1** — актуальная версия
3. **Celery 5.4.0** — актуальная версия
4. **Python 3.13** — очень новая версия, возможны проблемы совместимости с некоторыми пакетами

### Рекомендации
1. **Использовать `django-environ`** вместо `python-dotenv` для лучшей валидации env переменных
2. **Добавить `django-stubs`** для type checking
3. **Рассмотреть `django-extensions`** для полезных команд (например, `shell_plus`)
4. **Добавить `pre-commit` hooks** для автоматической проверки кода

---

## Единообразие (Conventions)

### Предлагаемые правила
1. **Service Layer:** Вынести бизнес-логику из views в сервисы (например, `companies/services.py`)
2. **Permissions:** Централизовать проверки прав в `crm/utils.py` (уже частично сделано)
3. **Logging:** Использовать структурированное логирование с `request_id` для трассировки
4. **API Responses:** Единый формат ошибок через `crm.exceptions.custom_exception_handler`
5. **Transactions:** Все массовые операции оборачивать в `@transaction.atomic`
6. **Type Hints:** Добавить type hints во все публичные функции

---

## Roadmap

### 1 день (Критические исправления)
- [ ] SEC-001: Добавить `SIMPLE_JWT` конфигурацию
- [ ] SEC-002: Исправить race condition в `PullCallView`
- [ ] SEC-003: Кешировать Fernet instance
- [ ] SEC-005: Добавить rate limiting для phone API
- [ ] SEC-006: Добавить валидацию размера файлов

### 1 неделя (Высокий приоритет)
- [ ] PERF-001: Исправить N+1 в `CompanyViewSet`
- [ ] PERF-002: Добавить индексы на часто фильтруемые поля
- [ ] CORR-002: Обернуть массовые операции в транзакции
- [ ] REL-001: Добавить retry policy для Celery задач
- [ ] REL-002: Расширить health check

### 1 месяц (Средний/Низкий приоритет)
- [ ] CORR-003: Добавить idempotency keys
- [ ] API-001: Унифицировать формат ошибок API
- [ ] API-002: Добавить pagination по умолчанию
- [ ] CODE-001: Вынести дублирующуюся логику в utils
- [ ] CODE-002: Добавить type hints

---

## Тест-план

### Минимальный smoke checklist
1. **Авторизация:**
   - [ ] Логин через UI работает
   - [ ] JWT токены выдаются и работают
   - [ ] Refresh токены работают
   - [ ] Rate limiting блокирует после 10 попыток

2. **API:**
   - [ ] `/api/companies/` возвращает данные
   - [ ] `/api/phone/calls/pull/` не выдает один звонок дважды
   - [ ] Фильтрация и поиск работают быстро

3. **Mailer:**
   - [ ] Пароли SMTP шифруются и расшифровываются
   - [ ] Отправка писем работает
   - [ ] Вложения не превышают 15 МБ

4. **Health:**
   - [ ] `/health/` возвращает 200 когда все ОК
   - [ ] `/health/` возвращает 503 при проблемах

### Автоматические тесты
1. **Unit тесты:**
   - `test_jwt_configuration` — проверка настроек JWT
   - `test_pull_call_race_condition` — проверка отсутствия дубликатов
   - `test_fernet_encryption` — проверка шифрования паролей

2. **Integration тесты:**
   - `test_api_rate_limiting` — проверка rate limiting
   - `test_company_api_performance` — проверка отсутствия N+1
   - `test_celery_retry` — проверка retry policy

---

## Конкретные патчи для критических исправлений

### Патч 1: Добавление SIMPLE_JWT конфигурации

**Файл:** `backend/crm/settings.py`  
**После строки 285:**

```python
# DRF / JWT
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
    ),
    "DEFAULT_FILTER_BACKENDS": (
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ),
    # Защита от утечки информации через ошибки API
    "EXCEPTION_HANDLER": "crm.exceptions.custom_exception_handler",
}

# JWT настройки (добавить после REST_FRAMEWORK)
from datetime import timedelta

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(hours=1),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": False,
    "ALGORITHM": "HS256",
    "SIGNING_KEY": SECRET_KEY,
    "VERIFYING_KEY": None,
    "AUDIENCE": None,
    "ISSUER": None,
    "JWK_URL": None,
    "LEEWAY": 0,
    "AUTH_HEADER_TYPES": ("Bearer",),
    "AUTH_HEADER_NAME": "HTTP_AUTHORIZATION",
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
    "USER_AUTHENTICATION_RULE": "rest_framework_simplejwt.authentication.default_user_authentication_rule",
    "AUTH_TOKEN_CLASSES": ("rest_framework_simplejwt.tokens.AccessToken",),
    "TOKEN_TYPE_CLAIM": "token_type",
    "TOKEN_USER_CLASS": "rest_framework_simplejwt.models.TokenUser",
    "JTI_CLAIM": "jti",
    "SLIDING_TOKEN_REFRESH_EXP_CLAIM": "refresh_exp",
    "SLIDING_TOKEN_LIFETIME": timedelta(minutes=5),
    "SLIDING_TOKEN_REFRESH_LIFETIME": timedelta(days=1),
    "TOKEN_OBTAIN_SERIALIZER": "rest_framework_simplejwt.serializers.TokenObtainPairSerializer",
    "TOKEN_REFRESH_SERIALIZER": "rest_framework_simplejwt.serializers.TokenRefreshSerializer",
    "TOKEN_VERIFY_SERIALIZER": "rest_framework_simplejwt.serializers.TokenVerifySerializer",
    "TOKEN_BLACKLIST_SERIALIZER": "rest_framework_simplejwt.serializers.TokenBlacklistSerializer",
}
```

**Примечание:** Для blacklist нужно установить `djangorestframework-simplejwt[with_blacklist]` и добавить в `INSTALLED_APPS`:
```python
INSTALLED_APPS = [
    # ...
    'rest_framework_simplejwt.token_blacklist',  # Добавить
]
```

**Проверка:** После применения проверить, что токены выдаются с правильным lifetime и refresh работает.

---

### Патч 2: Исправление race condition в PullCallView

**Файл:** `backend/phonebridge/api.py`  
**Заменить строки 72-84:**

```python
from django.db import transaction

def get(self, request):
    import logging
    logger = logging.getLogger(__name__)
    
    device_id = (request.query_params.get("device_id") or "").strip()
    if not device_id:
        logger.warning(f"PullCallView: device_id missing for user {request.user.id}")
        return Response({"detail": "device_id is required"}, status=400)

    # Проверяем, что device_id принадлежит текущему пользователю (безопасность)
    device_exists = PhoneDevice.objects.filter(user=request.user, device_id=device_id).exists()
    if not device_exists:
        logger.warning(f"PullCallView: device_id {device_id} not found for user {request.user.id}")
        return Response({"detail": "Device not found or access denied"}, status=403)

    # обновим last_seen
    PhoneDevice.objects.filter(user=request.user, device_id=device_id).update(last_seen_at=timezone.now())

    # Проверяем наличие pending запросов для этого пользователя
    pending_count = CallRequest.objects.filter(user=request.user, status=CallRequest.Status.PENDING).count()
    logger.debug(f"PullCallView: user {request.user.id}, device {device_id}, pending calls: {pending_count}")
    
    # ИСПРАВЛЕНИЕ: Используем select_for_update для предотвращения race condition
    with transaction.atomic():
        call = (
            CallRequest.objects.select_for_update(skip_locked=True)
            .filter(user=request.user, status=CallRequest.Status.PENDING)
            .order_by("created_at")
            .first()
        )
        if not call:
            return Response(status=204)

        call.status = CallRequest.Status.CONSUMED
        now = timezone.now()
        call.delivered_at = now
        call.consumed_at = now
        call.save(update_fields=["status", "delivered_at", "consumed_at"])
    
    logger.info(f"PullCallView: delivered call {call.id} to user {request.user.id}, phone {call.phone_raw}")

    return Response(
        {
            "id": str(call.id),
            "phone": call.phone_raw,
            "company_id": str(call.company_id) if call.company_id else None,
            "contact_id": str(call.contact_id) if call.contact_id else None,
            "note": call.note,
            "created_at": call.created_at,
        }
    )
```

**Проверка:** Запустить два параллельных запроса к `/api/phone/calls/pull/` и убедиться, что один и тот же звонок не выдается дважды.

---

### Патч 3: Кеширование Fernet instance

**Файл:** `backend/mailer/crypto.py`  
**Заменить весь файл:**

```python
from __future__ import annotations

from functools import lru_cache
from django.conf import settings
from cryptography.fernet import Fernet


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    """
    Кешированный Fernet instance для шифрования паролей SMTP.
    Использует LRU cache для избежания повторного создания объекта.
    """
    key = getattr(settings, "MAILER_FERNET_KEY", "") or ""
    if not key:
        raise RuntimeError("MAILER_FERNET_KEY is not set. Set it in .env / env variables.")
    return Fernet(key.encode("utf-8"))


def encrypt_str(value: str) -> str:
    """Зашифровать строку (пароль SMTP)."""
    if value is None:
        value = ""
    token = _fernet().encrypt(value.encode("utf-8"))
    return token.decode("utf-8")


def decrypt_str(token: str) -> str:
    """Расшифровать строку (пароль SMTP)."""
    if not token:
        return ""
    value = _fernet().decrypt(token.encode("utf-8"))
    return value.decode("utf-8")
```

**Проверка:** Проверить, что шифрование/расшифровка работают корректно после изменений.

---

### Патч 4: Добавление rate limiting для phone API

**Файл:** `backend/accounts/middleware.py`  
**Заменить строки 27-31:**

```python
# Пути с защитой от брутфорса (только эти пути защищаются)
PROTECTED_PATHS = [
    "/login/",
    "/api/token/",
    "/api/token/refresh/",
    "/api/phone/",  # Добавлено: защита phone API от DDoS
]
```

**Проверка:** Отправить более 10 запросов к `/api/phone/calls/pull/` в минуту и убедиться, что возвращается 429.

---

### Патч 5: Добавление валидации размера файлов

**Файл:** `backend/mailer/forms.py`  
**Добавить в класс `CampaignForm`:**

```python
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator

class CampaignForm(forms.ModelForm):
    class Meta:
        model = Campaign
        fields = ["name", "subject", "sender_name", "body_html", "attachment"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "input"}),
            "subject": forms.TextInput(attrs={"class": "input"}),
            "sender_name": forms.TextInput(attrs={"class": "input", "placeholder": "Например: CRM ПРОФИ / Отдел продаж"}),
            "body_html": forms.Textarea(attrs={"class": "textarea", "rows": 10, "placeholder": "<p>...</p>", "id": "id_body_html"}),
        }
    
    def clean_attachment(self):
        """Валидация размера и типа файла вложения."""
        attachment = self.cleaned_data.get('attachment')
        if attachment:
            # Проверка размера (15 МБ максимум)
            max_size = 15 * 1024 * 1024  # 15 МБ
            if attachment.size > max_size:
                raise ValidationError(f"Размер файла не должен превышать 15 МБ. Текущий размер: {attachment.size / 1024 / 1024:.2f} МБ.")
            
            # Проверка расширения файла
            allowed_extensions = ['pdf', 'doc', 'docx', 'xls', 'xlsx', 'txt', 'csv', 'zip', 'rar']
            file_extension = attachment.name.split('.')[-1].lower() if '.' in attachment.name else ''
            if file_extension not in allowed_extensions:
                raise ValidationError(f"Недопустимый тип файла. Разрешенные типы: {', '.join(allowed_extensions)}")
        
        return attachment
```

**Проверка:** Попытаться загрузить файл больше 15 МБ и убедиться, что форма не валидируется.

---

### Патч 6: Исправление N+1 в CompanyViewSet

**Файл:** `backend/companies/api.py`  
**Заменить строку 60:**

```python
def get_queryset(self):
    return Company.objects.select_related(
        "responsible", "branch", "status", "head_company"
    ).prefetch_related("spheres").order_by("-updated_at")
```

**Проверка:** Включить `django-debug-toolbar` и проверить количество SQL запросов при получении списка компаний (должно быть ~3-5 вместо 100+).

---

### Патч 7: Добавление retry policy для Celery задач

**Файл:** `backend/mailer/tasks.py`  
**Заменить декоратор на строке 18:**

```python
@shared_task(
    name="mailer.tasks.send_pending_emails",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
)
def send_pending_emails(self, batch_size: int = 50):
    """
    Отправка писем из очереди с автоматическими retry при ошибках.
    
    Args:
        batch_size: Максимум писем за итерацию на кампанию
    """
    try:
        # ... существующая логика ...
    except Exception as exc:
        logger.error(f"Error in send_pending_emails task: {exc}", exc_info=True)
        # Автоматический retry с экспоненциальной задержкой
        raise self.retry(exc=exc)
```

**Проверка:** Временно отключить Redis и убедиться, что задача автоматически повторяется.

---

## Дополнительные рекомендации

### Мониторинг и логирование

1. **Добавить structured logging:**
```python
import structlog

logger = structlog.get_logger(__name__)
logger.info("user_login", user_id=user.id, ip=ip, success=True)
```

2. **Добавить метрики:**
   - Количество запросов к API
   - Время ответа endpoints
   - Количество ошибок по типам
   - Использование Redis/Celery

3. **Добавить alerting:**
   - Уведомления при ошибках 500
   - Уведомления при недоступности Redis/Celery
   - Уведомления при превышении rate limits

### Тестирование

1. **Добавить pytest:**
```bash
pip install pytest pytest-django pytest-cov
```

2. **Создать базовые тесты:**
   - `tests/test_security.py` — тесты безопасности
   - `tests/test_api.py` — тесты API endpoints
   - `tests/test_celery.py` — тесты Celery задач

3. **Добавить CI/CD:**
   - Автоматический запуск тестов при push
   - Проверка линтера (flake8, black)
   - Проверка типов (mypy)

---

## Заключение

Проект в целом хорошо структурирован, но есть несколько критических проблем безопасности и производительности, которые нужно исправить в первую очередь. Большинство исправлений безопасны и не меняют поведение системы.

**Приоритет действий:**
1. Критические проблемы безопасности (SEC-001, SEC-002, SEC-003)
2. Проблемы производительности (PERF-001, PERF-002)
3. Улучшения надежности (REL-001, REL-002)
4. Улучшения кода (CODE-001, CODE-002)
