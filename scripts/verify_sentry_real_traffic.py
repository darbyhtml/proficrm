"""Wave 0.4 closeout — real-traffic verification of SentryContextMiddleware chain.

Проблема которую решает: shell-level вызов `_enrich_scope()` вручную не
эквивалент real HTTP через Django MIDDLEWARE chain. Верификация middleware
DoD требует integration-level test.

Два уровня verification:

## Level 1 — django.test.Client (integration)

Запускается из Django shell внутри web-контейнера. Использует Django TestClient
который ходит через полный MIDDLEWARE stack (SecurityMiddleware, Session,
Auth, Waffle, SentryContext, etc).

Trigger: `c.force_login(user).get("/_staff/trigger-test-error/", secure=True)`.
Endpoint gated:
- env STAFF_DEBUG_ENDPOINTS_ENABLED=1 (default off)
- @login_required
- user.is_staff

## Level 2 — Playwright browser (e2e)

Если IP разработчика в nginx whitelist staging — full browser flow:
login form → session cookie → GET /_staff/trigger-test-error/ → 500 →
GlitchTip event verified.

Этот модуль запускает **Level 1**. Level 2 — отдельный Node-скрипт
(не committed в этот файл из-за Playwright Python API conflict с глобальным
asyncio loop в Django-shell).

## Usage

    # На staging VPS:
    docker cp scripts/verify_sentry_real_traffic.py crm_staging_web:/tmp/v.py
    docker exec \
        -e VERIFY_USERNAME=sdm \
        crm_staging_web bash -c 'cd /app/backend && python manage.py shell < /tmp/v.py'

    # Затем через GlitchTip API проверить что event прилетел с правильными tags.

## Expected tags

Для user.username=sdm (is_staff=True, role=admin, branch=ekb) event должен
содержать:
    branch          = 'ekb'
    role            = 'admin'
    request_id      = <8-char UUID>
    feature_flags   = 'none'
    environment     = 'staging'
    user.id         = <sdm's id>
    user.username   = 'sdm'

При обнаружении расхождения — middleware chain сломан. Детали в
`docs/audit/process-lessons.md` §«Shell-level middleware test ≠ real HTTP».
"""

from __future__ import annotations

import os
import sys

import sentry_sdk
from django.test import Client

from accounts.models import User

USERNAME = os.environ.get("VERIFY_USERNAME", "sdm")

print(f"[verify] Looking for user username={USERNAME!r}")
try:
    u = User.objects.get(username=USERNAME)
except User.DoesNotExist:
    print(f"[verify] ERROR: user {USERNAME!r} not found", file=sys.stderr)
    sys.exit(2)

print(
    f"[verify] Found user: id={u.id} username={u.username!r} "
    f"role={u.role!r} branch={u.branch.code if u.branch else None!r} "
    f"is_staff={u.is_staff}"
)

c = Client(raise_request_exception=False)  # получаем 500 как response, не исключение
c.force_login(u)

# HTTPS + Host — обязательно для DJANGO_ALLOWED_HOSTS + SECURE_SSL_REDIRECT.
resp = c.get(
    "/_staff/trigger-test-error/",
    secure=True,
    HTTP_HOST="crm-staging.groupprofi.ru",
)

print(f"[verify] HTTP status: {resp.status_code}")
if resp.status_code == 404:
    print(
        "[verify] ВАЖНО: endpoint выключен (STAFF_DEBUG_ENDPOINTS_ENABLED=0 на этом env).\n"
        "         Проверь .env на staging. На prod это правильное поведение."
    )
    sys.exit(1)
if resp.status_code == 302:
    print("[verify] ВАЖНО: redirect на login — `force_login` не сработал для этого user.")
    sys.exit(1)
if resp.status_code != 500:
    print(f"[verify] Unexpected status {resp.status_code} — ожидается 500 от RuntimeError.")
    sys.exit(1)

print("[verify] Status 500 OK — Exception прошёл через MIDDLEWARE chain.")

# Flush Sentry SDK — дождаться отправки event в GlitchTip.
sentry_sdk.flush(timeout=10)
print("[verify] Sentry SDK flushed.")
print("[verify] DONE. Проверь свежий issue в GlitchTip через API:")
print(
    "         curl -sk -b /tmp/gt_auth.txt "
    "'https://glitchtip.groupprofi.ru/api/0/projects/groupprofi/crm-staging/issues/?limit=1'"
)
