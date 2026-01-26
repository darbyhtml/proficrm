"""
Перенос задач на ответственного за компанию.

Проверяет, что задачи назначены на ответственного за компанию (company.responsible_id),
и переносит их, если назначены на другого сотрудника.

Запуск (Docker):
  docker compose exec web python manage.py fix_task_assigned_to_company_responsible [--dry-run] [--limit N] [--batch-size N]

Запуск (локально):
  python manage.py fix_task_assigned_to_company_responsible [--dry-run] [--limit N] [--batch-size N]
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db.models import Q, F
from django.db import transaction

from accounts.models import User
from companies.models import Company
from tasksapp.models import Task


class Command(BaseCommand):
    help = (
        "Переносит задачи на ответственного за компанию.\n"
        "Находит задачи, где assigned_to не совпадает с company.responsible_id,\n"
        "и переносит их на ответственного за компанию.\n"
        "Задачи без компании или без ответственного за компанию пропускаются."
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
        parser.add_argument(
            "--skip-completed",
            action="store_true",
            help="Пропускать выполненные и отменённые задачи.",
        )

    def handle(self, *args, **options):
        dry_run = bool(options["dry_run"])
        limit = options.get("limit")
        batch_size = options.get("batch_size", 500)
        skip_completed = bool(options["skip_completed"])

        self.stdout.write(self.style.WARNING("Поиск задач для переноса на ответственного за компанию..."))

        # Базовый запрос: задачи с компанией и ответственным за компанию
        qs = Task.objects.filter(
            company__isnull=False,
            company__responsible_id__isnull=False,
        ).select_related("company", "company__responsible", "assigned_to")

        # Исключаем задачи, где assigned_to уже совпадает с company.responsible_id
        qs = qs.exclude(assigned_to_id=F("company__responsible_id"))

        # Опционально: пропускаем выполненные и отменённые задачи
        if skip_completed:
            qs = qs.exclude(status__in=[Task.Status.DONE, Task.Status.CANCELLED])

        total = qs.count()

        if total == 0:
            self.stdout.write(
                self.style.SUCCESS(
                    "Нет задач для переноса. Все задачи уже назначены на ответственных за компании."
                )
            )
            return

        self.stdout.write(
            self.style.WARNING(
                f"\nНайдено задач для переноса на ответственного за компанию: {total}"
            )
        )
        if limit:
            self.stdout.write(self.style.WARNING(f"Ограничение: не более {limit} задач."))

        if dry_run:
            self.stdout.write("\n" + self.style.WARNING("=== DRY RUN — примеры ==="))
            examples = qs[:10]
            for task in examples:
                current_assigned = str(task.assigned_to) if task.assigned_to else "NULL"
                new_assigned = str(task.company.responsible) if task.company.responsible else "NULL"
                self.stdout.write(
                    f"  ID: {task.id} | Компания: {task.company.name[:40]} | "
                    f"Текущий исполнитель: {current_assigned} → "
                    f"Новый исполнитель: {new_assigned} | "
                    f"Задача: {task.title[:50]}"
                )
            if total > 10:
                self.stdout.write(f"  ... и ещё {total - 10}")
            self.stdout.write(
                self.style.WARNING("\nЗапустите без --dry-run для применения изменений.")
            )
            return

        # Реальное обновление
        ids = list(qs.values_list("id", flat=True))
        if limit:
            ids = ids[:limit]

        updated = 0
        errors = 0

        for i in range(0, len(ids), batch_size):
            batch_ids = ids[i : i + batch_size]
            with transaction.atomic():
                # Загружаем задачи с компаниями для обновления
                batch_tasks = Task.objects.filter(id__in=batch_ids).select_related(
                    "company", "company__responsible"
                )
                
                for task in batch_tasks:
                    if task.company and task.company.responsible_id:
                        try:
                            task.assigned_to_id = task.company.responsible_id
                            task.save(update_fields=["assigned_to", "updated_at"])
                            updated += 1
                        except Exception as e:
                            errors += 1
                            self.stdout.write(
                                self.style.ERROR(
                                    f"Ошибка при обновлении задачи {task.id}: {e}"
                                )
                            )
                    else:
                        errors += 1
                        self.stdout.write(
                            self.style.WARNING(
                                f"Пропущена задача {task.id}: нет компании или ответственного"
                            )
                        )
            
            self.stdout.write(f"  Обработано: {updated + errors}/{len(ids)}")

        self.stdout.write(
            self.style.SUCCESS(
                f"\nГотово. Перенесено задач на ответственного за компанию: {updated}"
            )
        )
        if errors > 0:
            self.stdout.write(
                self.style.WARNING(f"Ошибок/пропущено: {errors}")
            )
