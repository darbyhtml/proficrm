"""F11 (2026-04-18): test-only settings — отключает SSL-редиректы
и HSTS, которые ломают DRF APIClient на staging (MSG widget/API тесты
получали 301 вместо 200/403 из-за SECURE_SSL_REDIRECT=True).

Использование:
    DJANGO_SETTINGS_MODULE=crm.settings_test python manage.py test

Или в scripts/test.sh — уже использует локальный settings без прод-флагов.

Решение следует по P0 из problems-solved.md [2026-04-18]:
«Staging test env: SECURE_SSL_REDIRECT=True ломает widget-тесты».
"""

from .settings import *

# ── ALLOWED_HOSTS: Django test Client по умолчанию шлёт HOST=testserver.
# Прод-settings имеет узкий whitelist без testserver → все view-тесты
# получали DisallowedHost 400 с пустым body (json.loads ломался). ──
ALLOWED_HOSTS = ["*"]

# ── SSL / security headers: отключены для test client ──
SECURE_SSL_REDIRECT = False
SECURE_HSTS_SECONDS = 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_HSTS_PRELOAD = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False

# ── Кэш: local-memory вместо Redis, чтобы тесты не задевали прод Redis ──
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "test-cache",
    }
}

# ── Email: in-memory для проверки отправки без реального SMTP ──
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# ── Celery: eager-режим — задачи выполняются синхронно ──
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

# ── Пароли: быстрый хэшер для тестов (ускоряет create_user в 10 раз) ──
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]

# ── Widget origin: dev-режим для тестов ──
# В проде MESSENGER_WIDGET_STRICT_ORIGIN=True → 403 при пустом allowlist.
# В тестах APIClient не передаёт Origin header и не настраивает inbox.
# allowed_domains — поэтому все widget-endpoint-тесты получали 403.
# Выключаем strict для тестовой среды (обратная совместимость dev).
MESSENGER_WIDGET_STRICT_ORIGIN = False

# ── Логирование: не шумим в тестах ──
import logging

logging.disable(logging.CRITICAL)
