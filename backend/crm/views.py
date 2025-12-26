"""
Общие views для обработки ошибок с защитой от утечки информации.
"""
from django.http import Http404, HttpResponse
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
