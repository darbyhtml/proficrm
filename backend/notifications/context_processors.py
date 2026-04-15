from __future__ import annotations

from datetime import timedelta

from django.utils import timezone
from django.core.cache import cache

from notifications.models import Notification
from tasksapp.models import Task
from companies.models import Company

_BELL_CACHE_TTL = 30  # seconds


def _get_bell_data(user, now):
    """Read-only bell data: notifications + task reminders. Cached per user for 30s."""
    cache_key = f"bell_data:{user.pk}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    notif_unread_count = Notification.objects.filter(user=user, is_read=False).count()
    notif_items = list(
        Notification.objects.filter(user=user, is_read=False).order_by("-created_at")[:10]
    )

    reminders_qs = (
        Task.objects.filter(assigned_to=user)
        .exclude(status__in=[Task.Status.DONE, Task.Status.CANCELLED])
        .select_related("company")
    )
    overdue = list(reminders_qs.filter(due_at__lt=now).order_by("due_at")[:10])
    today = list(reminders_qs.filter(due_at__date=now.date()).order_by("due_at")[:10])

    reminder_items = []
    for t in overdue:
        reminder_items.append({
            "title": f"Просрочено: {t.title}",
            "subtitle": (t.company.name if t.company else ""),
            "url": "/tasks/?overdue=1",
            "kind": "overdue",
        })
    for t in today:
        reminder_items.append({
            "title": f"На сегодня: {t.title}",
            "subtitle": (t.company.name if t.company else ""),
            "url": "/tasks/?today=1",
            "kind": "today",
        })
    reminder_count = len(overdue) + len(today)

    result = {
        "notif_unread_count": notif_unread_count,
        "notif_items": notif_items,
        "reminder_count": reminder_count,
        "reminder_items": reminder_items,
    }
    cache.set(cache_key, result, _BELL_CACHE_TTL)
    return result


def notifications_panel(request):
    """
    Данные для колокольчика:
    - notif_unread_count / notif_items: реальные уведомления (можно отмечать прочитанными)
    - reminder_count / reminder_items: напоминания из задач (просроченные/на сегодня)

    Read-only часть кэшируется в Redis на 30 секунд (per user).
    """
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {}

    now = timezone.now()
    bell = _get_bell_data(user, now)

    notif_unread_count = bell["notif_unread_count"]
    notif_items = bell["notif_items"]
    reminder_count = bell["reminder_count"]
    reminder_items = list(bell["reminder_items"])  # copy — contract items appended below

    # Напоминания по договорам (для ответственного): только read-only показ
    # в UI. Создание записей CompanyContractReminder + Notification вынесено
    # в celery-beat задачу notifications.tasks.generate_contract_reminders,
    # чтобы не писать в БД из read-пути GET-запроса.
    try:
        today_date = timezone.localdate(now)

        contract_qs = (
            Company.objects.filter(responsible=user, contract_until__isnull=False)
            .select_related("contract_type")
            .only("id", "name", "contract_until", "contract_type")
        )

        # 1) UI-напоминания: ближайшие 10 в пределах максимального warning_days
        # Используем максимальный warning_days из всех типов договоров или 30 дней по умолчанию
        max_warning_days = 30
        try:
            from companies.models import ContractType
            from django.db.models import Max
            max_warning = ContractType.objects.aggregate(max_warning=Max("warning_days"))
            if max_warning["max_warning"]:
                max_warning_days = max(max_warning["max_warning"], 30)
        except Exception:
            pass
        
        soon_until = today_date + timedelta(days=max_warning_days)
        soon = contract_qs.filter(contract_until__lte=soon_until).order_by("contract_until")[:10]
        for c in list(soon):
            days_left = (c.contract_until - today_date).days if c.contract_until else None
            if days_left is not None and c.contract_type:
                danger_days = c.contract_type.danger_days
                prefix = "Срочно: " if days_left <= danger_days else ""
            else:
                prefix = "Срочно: " if (days_left is not None and days_left < 14) else ""
            reminder_items.append(
                {
                    "title": f"{prefix}Договор до {c.contract_until.strftime('%d.%m.%Y')}",
                    "subtitle": c.name,
                    "url": f"/companies/{c.id}/",
                    "kind": "contract",
                }
            )
        reminder_count += soon.count()
    except Exception:
        # не ломаем UI колокольчика из-за напоминаний
        pass

    return {
        "notif_unread_count": notif_unread_count,
        "notif_items": notif_items,
        "reminder_count": reminder_count,
        "reminder_items": reminder_items,
        "bell_count": notif_unread_count + reminder_count,
    }


