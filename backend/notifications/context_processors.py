from __future__ import annotations

from django.utils import timezone

from notifications.models import Notification
from tasksapp.models import Task


def notifications_panel(request):
    """
    Данные для колокольчика:
    - notif_unread_count / notif_items: реальные уведомления (можно отмечать прочитанными)
    - reminder_count / reminder_items: напоминания из задач (просроченные/на сегодня)
    """
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {}

    notif_unread_count = Notification.objects.filter(user=user, is_read=False).count()
    notif_items = Notification.objects.filter(user=user).order_by("-created_at")[:10]

    now = timezone.now()
    # напоминания: мои задачи (назначенные пользователю)
    reminders = (
        Task.objects.filter(assigned_to=user)
        .exclude(status__in=[Task.Status.DONE, Task.Status.CANCELLED])
        .select_related("company")
    )
    overdue = reminders.filter(due_at__lt=now).order_by("due_at")[:10]
    today = reminders.filter(due_at__date=now.date()).order_by("due_at")[:10]
    reminder_items = []
    for t in list(overdue):
        reminder_items.append(
            {
                "title": f"Просрочено: {t.title}",
                "subtitle": (t.company.name if t.company else ""),
                "url": f"/tasks/?overdue=1",
                "kind": "overdue",
            }
        )
    for t in list(today):
        reminder_items.append(
            {
                "title": f"На сегодня: {t.title}",
                "subtitle": (t.company.name if t.company else ""),
                "url": f"/tasks/?today=1",
                "kind": "today",
            }
        )
    reminder_count = len(overdue) + len(today)

    return {
        "notif_unread_count": notif_unread_count,
        "notif_items": notif_items,
        "reminder_count": reminder_count,
        "reminder_items": reminder_items,
        "bell_count": notif_unread_count + reminder_count,
    }


