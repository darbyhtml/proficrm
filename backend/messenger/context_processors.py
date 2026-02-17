"""
Context processors для шаблонов messenger.
"""

from django.conf import settings

from .selectors import get_messenger_unread_count


def messenger_globals(request):
    """
    Добавляет messenger_unread_count для отображения badge в меню «Диалоги».
    Считается только при включённом messenger и аутентифицированном пользователе.
    """
    out = {}
    if getattr(settings, "MESSENGER_ENABLED", False) and getattr(request, "user", None) and request.user.is_authenticated:
        out["messenger_unread_count"] = get_messenger_unread_count(request.user)
    else:
        out["messenger_unread_count"] = 0
    return out
