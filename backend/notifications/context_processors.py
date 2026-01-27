from __future__ import annotations

from datetime import timedelta

from django.utils import timezone
from django.db import models

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
    # + создаём реальное уведомление на порогах (30/14 дней) с дедупликацией.
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

        # 2) Реальные уведомления (с дедупликацией) - используем настройки из ContractType
        if should_check:
            for c in contract_qs:
                if not c.contract_type or not c.contract_until:
                    continue
                warning_days = c.contract_type.warning_days
                danger_days = c.contract_type.danger_days
                days_left = (c.contract_until - today_date).days
                
                # Создаем уведомления на порогах warning_days и danger_days
                for days_before in [warning_days, danger_days]:
                    if days_before > days_left:
                        continue
                    target = c.contract_until - timedelta(days=days_before)
                    if target.date() != today_date:
                        continue
                    
                    exists = CompanyContractReminder.objects.filter(
                        user=user, company_id=c.id, contract_until=c.contract_until, days_before=days_before
                    ).exists()
                    if exists:
                        continue
                    CompanyContractReminder.objects.create(
                        user=user, company_id=c.id, contract_until=c.contract_until, days_before=days_before
                    )
                    if days_before == danger_days:
                        title = f"До окончания договора осталось {days_before} дней"
                        body = f"{c.name} · до {c.contract_until.strftime('%d.%m.%Y')}"
                    else:
                        title = f"До окончания договора осталось {days_before} дней"
                        body = f"{c.name} · до {c.contract_until.strftime('%d.%m.%Y')}"
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


