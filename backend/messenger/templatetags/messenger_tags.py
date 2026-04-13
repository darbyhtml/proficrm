"""Template tags для UI-статусов диалогов.

Используется в operator-panel и списках диалогов для единообразного
рендера бейджа статуса ({% ui_status_badge conversation %}).
"""

from django import template

from messenger.models import Conversation

register = template.Library()


UI_STATUS_META = {
    Conversation.UiStatus.NEW: {
        "label": "Новый",
        "bg": "bg-red-500",
        "text": "text-white",
        "icon": "bell",
        "tooltip": "Никто ещё не взял. Нажми «Взять себе»",
    },
    Conversation.UiStatus.WAITING: {
        "label": "Ждёт ответа",
        "bg": "bg-amber-400",
        "text": "text-gray-900",
        "icon": "message-circle",
        "tooltip": "Клиент написал, ждёт твоего ответа",
    },
    Conversation.UiStatus.IN_PROGRESS: {
        "label": "В работе",
        "bg": "bg-blue-500",
        "text": "text-white",
        "icon": "hourglass",
        "tooltip": "Ты ответил, ждём реакции клиента",
    },
    Conversation.UiStatus.CLOSED: {
        "label": "Завершён",
        "bg": "bg-gray-400",
        "text": "text-white",
        "icon": "check",
        "tooltip": "Диалог закрыт",
    },
}


@register.inclusion_tag("messenger/_ui_status_badge.html")
def ui_status_badge(conversation):
    """Рендерит бейдж UI-статуса для диалога."""
    meta = UI_STATUS_META[conversation.ui_status]
    return {"meta": meta, "status": conversation.ui_status}
