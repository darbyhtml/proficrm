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
