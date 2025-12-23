from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from notifications.models import Notification
from notifications.models import CompanyContractReminder
from notifications.service import notify
from tasksapp.models import Task
from companies.models import Company


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
    # В выпадающем списке показываем только актуальные (непрочитанные), чтобы после галочки они исчезали.
    notif_items = Notification.objects.filter(user=user, is_read=False).order_by("-created_at")[:10]

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

    # Напоминания по договорам (для ответственного): показываем в "Напоминаниях"
    # + создаём реальное уведомление на порогах (7/1/0 дней) с дедупликацией.
    try:
        now = timezone.now()
        today_date = timezone.localdate(now)
        # лёгкий троттлинг, чтобы не бегать по БД на каждом запросе
        last_ts = int(request.session.get("contract_reminders_checked_at", 0) or 0)
        now_ts = int(now.timestamp())
        should_check = (now_ts - last_ts) > 15 * 60
        if should_check:
            request.session["contract_reminders_checked_at"] = now_ts

        contract_qs = (
            Company.objects.filter(responsible=user, contract_until__isnull=False)
            .only("id", "name", "contract_until")
        )

        # 1) UI-напоминания: ближайшие 10 в пределах 7 дней
        soon_until = today_date + timedelta(days=7)
        soon = contract_qs.filter(contract_until__lte=soon_until).order_by("contract_until")[:10]
        for c in list(soon):
            reminder_items.append(
                {
                    "title": f"Договор заканчивается: {c.contract_until.strftime('%d.%m.%Y')}",
                    "subtitle": c.name,
                    "url": f"/companies/{c.id}/",
                    "kind": "contract",
                }
            )
        reminder_count += soon.count()

        # 2) Реальные уведомления (с дедупликацией)
        if should_check:
            thresholds = [7, 1, 0]
            for days_before in thresholds:
                target = today_date + timedelta(days=days_before)
                qs_hit = contract_qs.filter(contract_until=target)
                for c in qs_hit:
                    exists = CompanyContractReminder.objects.filter(
                        user=user, company_id=c.id, contract_until=target, days_before=days_before
                    ).exists()
                    if exists:
                        continue
                    CompanyContractReminder.objects.create(
                        user=user, company_id=c.id, contract_until=target, days_before=days_before
                    )
                    if days_before == 0:
                        title = "Сегодня заканчивается договор"
                        body = f"{c.name} · до {target.strftime('%d.%m.%Y')}"
                    else:
                        title = f"Скоро заканчивается договор (через {days_before} дн.)"
                        body = f"{c.name} · до {target.strftime('%d.%m.%Y')}"
                    notify(
                        user=user,
                        kind=Notification.Kind.COMPANY,
                        title=title,
                        body=body,
                        url=f"/companies/{c.id}/",
                    )
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


