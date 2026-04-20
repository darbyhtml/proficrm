"""
Management-команда для наполнения дашборда тестовыми данными.

Использование:
    python manage.py seed_demo_data --user sdm
    python manage.py seed_demo_data --user sdm --clear

Создаёт задачи (разной срочности) для указанного пользователя, привязывая
их к существующим компаниям, где этот пользователь — responsible.
Также проставляет contract_until ближайшим 5 компаниям, чтобы блок
«Договоры» на дашборде не был пустым.
"""

from __future__ import annotations

import random
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from accounts.models import User
from companies.models import Company
from tasksapp.models import Task, TaskType


class Command(BaseCommand):
    help = "Наполнить дашборд тестовыми задачами и договорами для указанного пользователя."

    def add_arguments(self, parser):
        parser.add_argument("--user", required=True, help="username пользователя")
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Удалить ранее созданные демо-задачи (с пометкой [DEMO]) перед созданием новых",
        )
        parser.add_argument(
            "--count", type=int, default=12, help="Сколько задач создать (по умолчанию 12)"
        )

    def handle(self, *args, **opts):
        username = opts["user"]
        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist as e:
            raise CommandError(f"Пользователь {username!r} не найден") from e

        companies = list(Company.objects.filter(responsible=user)[:30])
        if not companies:
            # fallback: любые компании из подразделения пользователя
            if user.branch_id:
                companies = list(Company.objects.filter(branch_id=user.branch_id)[:30])
        if not companies:
            companies = list(Company.objects.all()[:30])
        if not companies:
            raise CommandError("В базе нет ни одной компании — сначала загрузите/создайте их")

        if opts["clear"]:
            deleted, _ = Task.objects.filter(assigned_to=user, title__startswith="[DEMO]").delete()
            self.stdout.write(self.style.WARNING(f"Удалено прежних демо-задач: {deleted}"))

        task_types = list(TaskType.objects.all()[:10])
        now = timezone.now()
        local_now = timezone.localtime(now)
        today_start = local_now.replace(hour=9, minute=0, second=0, microsecond=0)

        offsets = [
            -2,
            -1,  # просроченные (дни)
            0,
            0,
            0,  # сегодня
            1,
            2,
            3,
            4,
            5,  # на неделе
            6,
            7,  # на неделе конец
        ]
        count = min(int(opts["count"]), len(offsets))
        templates = [
            "Позвонить и уточнить договор",
            "Выслать коммерческое предложение",
            "Подтвердить встречу",
            "Запросить реквизиты",
            "Напомнить об оплате",
            "Подготовить счёт",
            "Согласовать условия поставки",
            "Актуализировать статус сделки",
            "Отправить акт сверки",
            "Связаться с ЛПР",
            "Обсудить сроки",
            "Сформировать отчёт",
        ]

        created = 0
        for i, days in enumerate(offsets[:count]):
            company = random.choice(companies)
            tt = random.choice(task_types) if task_types else None
            due_at = today_start + timedelta(days=days, hours=random.randint(0, 7))
            title = "[DEMO] " + (tt.name if tt else templates[i % len(templates)])
            Task.objects.create(
                title=title,
                company=company,
                assigned_to=user,
                created_by=user,
                type=tt,
                due_at=due_at,
                status=Task.Status.NEW,
            )
            created += 1

        # Договоры до конца месяца — для 5 случайных компаний.
        # Важно: форсим responsible=user, иначе блок «Договоры» на дашборде
        # (который фильтрует по responsible=user) останется пустым.
        contract_targets = random.sample(companies, k=min(5, len(companies)))
        today = local_now.date()
        for idx, c in enumerate(contract_targets):
            c.contract_until = today + timedelta(days=7 + idx * 5)
            if c.responsible_id != user.id:
                c.responsible = user
                c.save(update_fields=["contract_until", "responsible"])
            else:
                c.save(update_fields=["contract_until"])

        self.stdout.write(
            self.style.SUCCESS(
                f"Создано задач: {created}. Обновлено договоров: {len(contract_targets)}. Пользователь: {user.username}"
            )
        )
