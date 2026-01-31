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
    from datetime import datetime, timedelta
    from django.conf import settings
    
    # Получаем email из Django settings (надежнее чем напрямую из os.getenv)
    security_email = getattr(settings, "SECURITY_CONTACT_EMAIL", "") or "security@example.com"
    
    # Дата истечения: через год от текущей даты
    expires_date = (datetime.utcnow() + timedelta(days=365)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    
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


def health_check(request):
    """
    Health check endpoint для мониторинга состояния сервиса.
    Проверяет доступность БД, Redis и Celery.
    """
    from django.db import connection
    from django.core.cache import cache
    from django.conf import settings
    
    health_status = {
        "status": "ok",
        "checks": {}
    }
    
    # Проверка БД
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        health_status["checks"]["database"] = "ok"
    except Exception as e:
        health_status["checks"]["database"] = f"error: {str(e)}"
        health_status["status"] = "degraded"
    
    # Проверка Redis (кеш)
    try:
        cache.set("health_check_test", "ok", 10)
        test_value = cache.get("health_check_test")
        if test_value == "ok":
            health_status["checks"]["cache"] = "ok"
        else:
            health_status["checks"]["cache"] = "error: cache not working"
            health_status["status"] = "degraded"
    except Exception as e:
        health_status["checks"]["cache"] = f"error: {str(e)}"
        health_status["status"] = "degraded"
    
    # Проверка Celery (если настроен)
    if hasattr(settings, "CELERY_BROKER_URL"):
        try:
            from celery import current_app
            inspect = current_app.control.inspect()
            stats = inspect.stats()
            if stats:
                health_status["checks"]["celery"] = "ok"
            else:
                health_status["checks"]["celery"] = "warning: no workers available"
        except Exception as e:
            health_status["checks"]["celery"] = f"warning: {str(e)}"

    # Опционально: проверка Typesense (не влияет на status — есть fallback на Postgres)
    backend = getattr(settings, "SEARCH_ENGINE_BACKEND", "postgres") or "postgres"
    if str(backend).strip().lower() == "typesense":
        try:
            from companies.search_backends.typesense_backend import _typesense_available
            health_status["checks"]["search_typesense"] = "ok" if _typesense_available() else "unavailable"
        except Exception as e:
            health_status["checks"]["search_typesense"] = f"unavailable: {str(e)}"

    status_code = 200 if health_status["status"] == "ok" else 503
    return JsonResponse(health_status, status=status_code)
