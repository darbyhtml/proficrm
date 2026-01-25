"""
Перенос «Кто» (created_by) с администраторов на исполнителя задачи (assigned_to).

Находит задачи, где создатель — пользователь с ролью «Администратор» или is_superuser,
и переводит created_by на assigned_to (исполнителя). Задачи без assigned_to пропускаются.

Запуск:
  python manage.py fix_task_creator_from_admin [--dry-run] [--limit N] [--batch-size N]
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db.models import Q, F
from django.db import transaction

from accounts.models import User
from tasksapp.models import Task


class Command(BaseCommand):
    help = (
        "Переносит «Кто» (created_by) с администраторов на исполнителя (assigned_to).\n"
        "Задачи, где создатель — Администратор или суперпользователь, получают created_by = assigned_to.\n"
        "Задачи без assigned_to пропускаются."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Только показать, что будет сделано (ничего не изменять).",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Обработать не более N задач (для тестирования).",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=500,
            help="Размер батча для обновления (по умолчанию 500).",
        )

    def handle(self, *args, **options):
        dry_run = bool(options["dry_run"])
        limit = options.get("limit")
        batch_size = options.get("batch_size", 500)

        # Администраторы: роль ADMIN или is_superuser
        admin_ids = set(
            User.objects.filter(
                Q(role=User.Role.ADMIN) | Q(is_superuser=True)
            ).values_list("id", flat=True)
        )

        if not admin_ids:
            self.stdout.write(self.style.WARNING("В системе нет пользователей с ролью «Администратор» или is_superuser."))
            return

        self.stdout.write(f"Найдено администраторов: {len(admin_ids)}")

        # Задачи: created_by — админ, assigned_to не пустой, created_by != assigned_to
        qs = (
            Task.objects.filter(
                created_by_id__in=admin_ids,
                assigned_to__isnull=False,
            )
            .exclude(created_by_id=F("assigned_to_id"))
            .select_related("created_by", "assigned_to")
        )

        total = qs.count()

        if total == 0:
            self.stdout.write(self.style.SUCCESS("Нет задач для обновления (created_by=админ и assigned_to≠created_by)."))
            return

        self.stdout.write(
            self.style.WARNING(
                f"\nНайдено задач для переноса «Кто» с админа на исполнителя: {total}"
            )
        )
        if limit:
            self.stdout.write(self.style.WARNING(f"Ограничение: не более {limit} задач."))

        if dry_run:
            self.stdout.write("\n" + self.style.WARNING("=== DRY RUN — примеры ==="))
            for task in qs[:10]:
                self.stdout.write(
                    f"  ID: {task.id} | «Кто» было: {task.created_by} → будет: {task.assigned_to} | {task.title[:50]}"
                )
            if total > 10:
                self.stdout.write(f"  ... и ещё {total - 10}")
            self.stdout.write(
                self.style.WARNING("\nЗапустите без --dry-run для применения изменений.")
            )
            return

        # Один запрос: created_by_id = assigned_to_id для всех подходящих задач
        ids = list(qs.values_list("id", flat=True))
        if limit:
            ids = ids[:limit]

        updated = 0
        for i in range(0, len(ids), batch_size):
            batch_ids = ids[i : i + batch_size]
            with transaction.atomic():
                count = Task.objects.filter(id__in=batch_ids).update(created_by_id=F("assigned_to_id"))
                updated += count
            self.stdout.write(f"  Обработано: {updated}/{len(ids)}")

        self.stdout.write(self.style.SUCCESS(f"\nГотово. Перенесено «Кто» на исполнителя: {updated} задач."))
