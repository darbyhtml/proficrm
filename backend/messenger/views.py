"""
Views для messenger app.
"""

from django.shortcuts import render
from django.http import Http404
from django.contrib.auth.decorators import login_required

from .models import Inbox
from .utils import ensure_messenger_enabled_view


@login_required
def widget_demo(request):
    """
    Demo страница для тестирования виджета.
    
    Доступна только авторизованным пользователям (для безопасности).
    """
    ensure_messenger_enabled_view()

    # Получить первый активный Inbox для демо
    inbox = Inbox.objects.filter(is_active=True).first()
    
    if not inbox:
        # Если нет активных inbox - показать инструкцию
        return render(
            request,
            'messenger/widget_demo.html',
            {
                'inbox': None,
                'widget_token': None,
            },
        )

    return render(
        request,
        'messenger/widget_demo.html',
        {
            'inbox': inbox,
            'widget_token': inbox.widget_token,
        },
    )
