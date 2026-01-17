from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q

from tasksapp.models import Task


class Command(BaseCommand):
    help = (
        "Применяет новую логику статусов задач к существующим задачам:\n"
        "1. Если создатель = исполнитель (created_by == assigned_to), статус должен быть 'В работе' (IN_PROGRESS).\n"
        "2. Если создатель ≠ исполнитель, статус должен быть 'Новая' (NEW).\n"
        "3. Задачи со статусом 'Выполнена' (DONE) и 'Отменена' (CANCELLED) не изменяются."
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
            help="Ограничить количество обрабатываемых задач (для тестирования).",
        )

    def handle(self, *args, **options):
        dry_run = bool(options["dry_run"])
        limit = options.get("limit")

        self.stdout.write(self.style.WARNING("Поиск задач для обновления статусов..."))

        # Исключаем выполненные и отменённые задачи
        base_qs = Task.objects.exclude(
            status__in=[Task.Status.DONE, Task.Status.CANCELLED]
        ).select_related("created_by", "assigned_to")

        # 1. Задачи, где создатель = исполнитель, но статус не IN_PROGRESS
        self_tasks = base_qs.filter(
            created_by__isnull=False,
            assigned_to__isnull=False,
        ).filter(
            created_by_id=F("assigned_to_id")
        ).exclude(status=Task.Status.IN_PROGRESS)

        # 2. Задачи, где создатель ≠ исполнитель, но статус не NEW
        other_tasks = base_qs.filter(
            created_by__isnull=False,
            assigned_to__isnull=False,
        ).exclude(
            created_by_id=F("assigned_to_id")
        ).exclude(status=Task.Status.NEW)

        # 3. Задачи, где created_by или assigned_to = None (обрабатываем отдельно)
        null_tasks = base_qs.filter(
            Q(created_by__isnull=True) | Q(assigned_to__isnull=True)
        ).exclude(status=Task.Status.NEW)

        self_tasks_count = self_tasks.count()
        other_tasks_count = other_tasks.count()
        null_tasks_count = null_tasks.count()

        total = self_tasks_count + other_tasks_count + null_tasks_count

        if total == 0:
            self.stdout.write(
                self.style.SUCCESS("Все задачи уже имеют правильные статусы. Ничего не требуется обновить.")
            )
            return

        self.stdout.write(
            self.style.WARNING(
                f"\nНайдено задач для обновления: {total}\n"
                f"  - Создатель = исполнитель, но статус не 'В работе': {self_tasks_count}\n"
                f"  - Создатель ≠ исполнитель, но статус не 'Новая': {other_tasks_count}\n"
                f"  - Задачи с NULL создателем/исполнителем: {null_tasks_count}"
            )
        )

        if limit:
            self.stdout.write(
                self.style.WARNING(f"Ограничение: будет обработано максимум {limit} задач.")
            )

        if dry_run:
            self.stdout.write("\n" + self.style.WARNING("=== DRY RUN - примеры задач ==="))
            
            # Показываем примеры
            if self_tasks_count > 0:
                self.stdout.write("\nЗадачи, где создатель = исполнитель (должны быть 'В работе'):")
                examples = self_tasks[:5]
                for task in examples:
                    self.stdout.write(
                        f"  - ID: {task.id} | Статус: {task.get_status_display()} | "
                        f"Создатель: {task.created_by} | Исполнитель: {task.assigned_to} | "
                        f"Название: {task.title[:50]}"
                    )
            
            if other_tasks_count > 0:
                self.stdout.write("\nЗадачи, где создатель ≠ исполнитель (должны быть 'Новая'):")
                examples = other_tasks[:5]
                for task in examples:
                    self.stdout.write(
                        f"  - ID: {task.id} | Статус: {task.get_status_display()} | "
                        f"Создатель: {task.created_by} | Исполнитель: {task.assigned_to} | "
                        f"Название: {task.title[:50]}"
                    )
            
            if null_tasks_count > 0:
                self.stdout.write("\nЗадачи с NULL создателем/исполнителем (будут установлены в 'Новая'):")
                examples = null_tasks[:5]
                for task in examples:
                    self.stdout.write(
                        f"  - ID: {task.id} | Статус: {task.get_status_display()} | "
                        f"Создатель: {task.created_by or 'NULL'} | Исполнитель: {task.assigned_to or 'NULL'} | "
                        f"Название: {task.title[:50]}"
                    )

            self.stdout.write(
                self.style.WARNING(
                    "\nЭто DRY RUN, никаких изменений не внесено.\n"
                    "Запустите без --dry-run для реального обновления статусов."
                )
            )
            return

        # Реальное обновление
        self.stdout.write(self.style.WARNING("\nНачинаем обновление статусов..."))

        updated_self = 0
        updated_other = 0
        updated_null = 0

        # Обновляем задачи, где создатель = исполнитель
        if self_tasks_count > 0:
            qs = self_tasks
            if limit:
                qs = qs[:limit]
            
            ids = list(qs.values_list("id", flat=True))
            batch_size = 500
            
            for i in range(0, len(ids), batch_size):
                batch_ids = ids[i : i + batch_size]
                with transaction.atomic():
                    count = Task.objects.filter(id__in=batch_ids).update(
                        status=Task.Status.IN_PROGRESS
                    )
                    updated_self += count
                    self.stdout.write(f"  Обновлено задач (создатель = исполнитель): {updated_self}/{self_tasks_count}")

        # Обновляем задачи, где создатель ≠ исполнитель
        if other_tasks_count > 0:
            qs = other_tasks
            if limit:
                remaining_limit = (limit - updated_self) if limit else None
                if remaining_limit and remaining_limit > 0:
                    qs = qs[:remaining_limit]
            
            ids = list(qs.values_list("id", flat=True))
            batch_size = 500
            
            for i in range(0, len(ids), batch_size):
                batch_ids = ids[i : i + batch_size]
                with transaction.atomic():
                    count = Task.objects.filter(id__in=batch_ids).update(
                        status=Task.Status.NEW
                    )
                    updated_other += count
                    self.stdout.write(f"  Обновлено задач (создатель ≠ исполнитель): {updated_other}/{other_tasks_count}")

        # Обновляем задачи с NULL
        if null_tasks_count > 0:
            qs = null_tasks
            if limit:
                remaining_limit = (limit - updated_self - updated_other) if limit else None
                if remaining_limit and remaining_limit > 0:
                    qs = qs[:remaining_limit]
            
            ids = list(qs.values_list("id", flat=True))
            batch_size = 500
            
            for i in range(0, len(ids), batch_size):
                batch_ids = ids[i : i + batch_size]
                with transaction.atomic():
                    count = Task.objects.filter(id__in=batch_ids).update(
                        status=Task.Status.NEW
                    )
                    updated_null += count
                    self.stdout.write(f"  Обновлено задач (NULL создатель/исполнитель): {updated_null}/{null_tasks_count}")

        total_updated = updated_self + updated_other + updated_null

        self.stdout.write(
            self.style.SUCCESS(
                f"\nГотово! Обновлено задач: {total_updated}\n"
                f"  - Установлено 'В работе': {updated_self}\n"
                f"  - Установлено 'Новая': {updated_other + updated_null}"
            )
        )
