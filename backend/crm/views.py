"""
Общие views для обработки ошибок с защитой от утечки информации.
"""
import os
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import render


def handler404(request, exception):
    """Обработчик 404 с защитой от утечки информации."""
    return render(request, "404.html", status=404)


def handler500(request):
    """Обработчик 500 с защитой от утечки информации."""
    # В production не показываем детали ошибки
    return render(request, "500.html", status=500)


def robots_txt(request):
    """Запрет индексации CRM поисковыми системами."""
    content = """User-agent: *
Disallow: /

# Internal CRM system - indexing prohibited
"""
    response = HttpResponse(content, content_type="text/plain; charset=utf-8")
    return response


def security_txt(request):
    """Security.txt для ответственного раскрытия уязвимостей."""
    from datetime import datetime, timedelta, timezone
    from django.conf import settings

    # Получаем email из Django settings (надежнее чем напрямую из os.getenv)
    security_email = getattr(settings, "SECURITY_CONTACT_EMAIL", "") or "security@example.com"

    # Дата истечения: через год от текущей даты (timezone-aware, не deprecated utcnow)
    expires_date = (datetime.now(timezone.utc) + timedelta(days=365)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    
    # Получаем домен из запроса (всегда используем HTTPS для canonical)
    host = request.get_host()
    canonical_url = f"https://{host}/.well-known/security.txt"
    
    content = f"""Contact: mailto:{security_email}
Expires: {expires_date}
Preferred-Languages: ru, en
Canonical: {canonical_url}

# Политика ответственного раскрытия уязвимостей
# Пожалуйста, сообщайте о найденных уязвимостях на указанный email
# Мы обязуемся ответить в течение 48 часов
"""
    response = HttpResponse(content, content_type="text/plain; charset=utf-8")
    return response


def sw_push_js(request):
    """
    Отдать Service Worker напрямую (не через redirect).
    Браузеры запрещают регистрацию SW через 302-redirect.
    """
    import pathlib
    from django.conf import settings as django_settings

    sw_path = pathlib.Path(django_settings.BASE_DIR) / "messenger" / "static" / "messenger" / "sw-push.js"
    try:
        content = sw_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise Http404("sw-push.js not found")

    response = HttpResponse(content, content_type="application/javascript; charset=utf-8")
    response["Service-Worker-Allowed"] = "/"
    response["Cache-Control"] = "no-cache"
    return response


def metrics_endpoint(request):
    """F11 (2026-04-18): Prometheus-совместимый /metrics endpoint.

    Формат — plain text exposition format, не требует prometheus_client
    как зависимости. Prometheus/Victoria Metrics/Grafana Agent парсят
    этот формат нативно.

    Защита: Bearer-токен из settings.METRICS_TOKEN. Если токен не задан —
    endpoint возвращает 503 (чтобы в проде не забыть настроить).

    Экспортируем:
      - crm_up — константа 1 (есть жизнь).
      - crm_companies_total — всего компаний.
      - crm_tasks_open — открытых задач (NEW + IN_PROGRESS).
      - crm_conversations_waiting_offline — off-hours очередь.
      - crm_conversations_open — открытые диалоги в чате.
      - crm_users_absent — сейчас в отпуске/больничном.
      - crm_mobile_app_builds_active — активных APK production.
    """
    from django.conf import settings as dj_settings
    from django.http import HttpResponse

    expected = getattr(dj_settings, "METRICS_TOKEN", "") or ""
    if not expected:
        # Если токен не задан в .env — endpoint отключён.
        return HttpResponse(
            "# METRICS_TOKEN not configured\n",
            status=503,
            content_type="text/plain; charset=utf-8",
        )

    auth = (request.META.get("HTTP_AUTHORIZATION") or "").strip()
    prefix = "Bearer "
    provided = auth[len(prefix):] if auth.startswith(prefix) else ""
    if provided != expected:
        return HttpResponse(
            "# unauthorized\n",
            status=401,
            content_type="text/plain; charset=utf-8",
        )

    lines: list[str] = []

    def gauge(name: str, value: int | float, help_text: str):
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} gauge")
        lines.append(f"{name} {value}")

    # Всегда-1 метрика для UP-check.
    gauge("crm_up", 1, "Application is up (constant 1)")

    # Бизнес-метрики — best-effort, любая ошибка → не блокирует.
    try:
        from companies.models import Company
        gauge("crm_companies_total", Company.objects.count(), "Total companies")
    except Exception:
        pass

    try:
        from tasksapp.models import Task
        open_tasks = Task.objects.filter(
            status__in=[Task.Status.NEW, Task.Status.IN_PROGRESS]
        ).count()
        gauge("crm_tasks_open", open_tasks, "Open tasks (NEW + IN_PROGRESS)")
    except Exception:
        pass

    try:
        from messenger.models import Conversation
        gauge(
            "crm_conversations_waiting_offline",
            Conversation.objects.filter(status=Conversation.Status.WAITING_OFFLINE).count(),
            "Off-hours conversations awaiting contact-back",
        )
        gauge(
            "crm_conversations_open",
            Conversation.objects.filter(status=Conversation.Status.OPEN).count(),
            "Open conversations in messenger",
        )
    except Exception:
        pass

    try:
        from accounts.models import UserAbsence
        from django.utils import timezone
        today = timezone.localdate()
        absent = UserAbsence.objects.filter(
            start_date__lte=today, end_date__gte=today
        ).values("user").distinct().count()
        gauge("crm_users_absent", absent, "Users currently absent (vacation/sick/dayoff)")
    except Exception:
        pass

    try:
        from phonebridge.models import MobileAppBuild
        gauge(
            "crm_mobile_app_builds_active",
            MobileAppBuild.objects.filter(is_active=True, env="production").count(),
            "Active production APK builds",
        )
    except Exception:
        pass

    body = "\n".join(lines) + "\n"
    return HttpResponse(body, content_type="text/plain; version=0.0.4; charset=utf-8")


def health_check(request):
    """
    Health check endpoint для мониторинга.
    Возвращает 200 если всё OK, 503 если критичный компонент недоступен.

    Проверки:
      database — SELECT 1 к PostgreSQL
      cache     — set/get через Redis
      celery    — ping с таймаутом 2с (warning, не degraded)
      disk      — свободное место на / (warning < 20%, degraded < 5%)
    """
    import shutil
    from datetime import datetime, timezone
    from django.db import connection
    from django.core.cache import cache

    checks = {}
    degraded = False

    # --- database ---
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        checks["database"] = {"status": "ok"}
    except Exception as e:
        checks["database"] = {"status": "error", "detail": str(e)}
        degraded = True

    # --- cache (Redis) ---
    try:
        cache.set("_hc", "1", 5)
        if cache.get("_hc") == "1":
            checks["cache"] = {"status": "ok"}
        else:
            checks["cache"] = {"status": "error", "detail": "get returned wrong value"}
            degraded = True
    except Exception as e:
        checks["cache"] = {"status": "error", "detail": str(e)}
        degraded = True

    # --- celery (ping, timeout 2s — warning only) ---
    try:
        from celery import current_app
        reply = current_app.control.ping(timeout=1)
        if reply:
            checks["celery"] = {"status": "ok", "workers": len(reply)}
        else:
            checks["celery"] = {"status": "warning", "detail": "no workers responded"}
    except Exception as e:
        checks["celery"] = {"status": "warning", "detail": str(e)}

    # --- disk ---
    try:
        usage = shutil.disk_usage("/")
        free_pct = usage.free / usage.total * 100
        if free_pct < 5:
            checks["disk"] = {"status": "error", "free_pct": round(free_pct, 1)}
            degraded = True
        elif free_pct < 20:
            checks["disk"] = {"status": "warning", "free_pct": round(free_pct, 1)}
        else:
            checks["disk"] = {"status": "ok", "free_pct": round(free_pct, 1)}
    except Exception as e:
        checks["disk"] = {"status": "warning", "detail": str(e)}

    overall = "degraded" if degraded else "ok"
    status_code = 503 if degraded else 200

    return JsonResponse({
        "status": overall,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
    }, status=status_code)
