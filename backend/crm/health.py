"""
Health / readiness / smoke endpoints — Wave 0.4 (2026-04-20).

- GET /health/  → 200 OK всегда если процесс жив. НЕ трогает БД/Redis.
                  Liveness probe для UptimeRobot и Kubernetes (в будущем).
- GET /ready/   → 200 OK если DB + Redis доступны; 503 если любой из них лежит.
                  Readiness probe — показатель, можно ли слать трафик в этот
                  контейнер. В W10 добавим проверку MinIO.
- GET /_debug/sentry-error/  → raise Exception — только при DEBUG=True.
                  Для smoke-test GlitchTip интеграции.

Endpoints специально без auth — health/ready должны быть доступны UptimeRobot
без credentials. CSRF exempt по той же причине.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.db import connection
from django.http import HttpRequest, JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET

logger = logging.getLogger(__name__)


@csrf_exempt
@require_GET
@never_cache
def health(_request: HttpRequest) -> JsonResponse:
    """Liveness — процесс отвечает. Не проверяет зависимости.

    Используется как дешёвый heartbeat. Если вернулся не-200 или вообще не
    вернулся — контейнер надо перезапускать.
    """
    return JsonResponse({"status": "ok"})


@csrf_exempt
@require_GET
@never_cache
def ready(_request: HttpRequest) -> JsonResponse:
    """Readiness — процесс может обслуживать трафик.

    Проверяет:
    - PostgreSQL: `SELECT 1` через Django default-connection.
    - Redis: `PING` через django-redis.

    Возвращает 503 если хоть что-то не ok. Тело всегда содержит
    детальный JSON для отладки.
    """
    checks: dict[str, dict[str, str]] = {}
    all_ok = True

    # --- Postgres ---
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        checks["database"] = {"status": "ok"}
    except Exception as exc:
        all_ok = False
        checks["database"] = {"status": "fail", "error": str(exc)[:200]}
        logger.error("ready: database check failed", exc_info=True)

    # --- Redis ---
    try:
        from django.core.cache import cache

        cache.set("_health_probe", "1", timeout=10)
        val = cache.get("_health_probe")
        if val != "1":
            raise RuntimeError(f"redis round-trip failed: got {val!r}")
        checks["redis"] = {"status": "ok"}
    except Exception as exc:
        all_ok = False
        checks["redis"] = {"status": "fail", "error": str(exc)[:200]}
        logger.error("ready: redis check failed", exc_info=True)

    # TODO Wave 10: добавить MinIO проверку через boto3 head_bucket.

    status_code = 200 if all_ok else 503
    return JsonResponse({"status": "ok" if all_ok else "fail", "checks": checks}, status=status_code)


@csrf_exempt
@require_GET
def sentry_smoke(_request: HttpRequest) -> JsonResponse:
    """Smoke-test для GlitchTip SDK. Доступен только при DEBUG=True.

    При вызове бросает исключение, которое Sentry SDK отправит в GlitchTip.
    В UI GlitchTip должна появиться ошибка с 5 тегами:
    user_id, role, branch, request_id, feature_flags.

    Запуск:
        curl https://crm-staging.groupprofi.ru/_debug/sentry-error/
    """
    if not settings.DEBUG:
        return JsonResponse(
            {"error": "Доступно только при DEBUG=True"}, status=404
        )
    # Намеренное исключение.
    raise RuntimeError("glitchtip-smoke-test (Wave 0.4)")
