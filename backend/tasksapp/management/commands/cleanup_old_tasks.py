from __future__ import annotations

from datetime import datetime

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from tasksapp.models import Task
from accounts.models import User


class Command(BaseCommand):
    help = (
        "Очистка старых задач:\n"
        "1. Задачи со статусом 'Выполнено' старше 3 месяцев - удаляются.\n"
        "2. Старые задачи (по году дедлайна) - переносятся в заметки компании и удаляются.\n"
        "   Задачи без компании удаляются без заметок.\n"
        "   По умолчанию ориентируемся на год дедлайна < 2025."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--year",
            type=int,
            default=2025,
            help="Оставляем задачи с дедлайном начиная с этого года (включительно). "
                 "Все задачи с due_at.year < year будут удалены (по умолчанию 2025).",
        )
        parser.add_argument(
            "--months",
            type=int,
            default=3,
            help="Количество месяцев хранения выполненных задач перед удалением (по умолчанию 3).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Только показать, что будет сделано (ничего не изменять).",
        )
        parser.add_argument(
            "--user-id",
            type=int,
            default=None,
            help="ID пользователя, от имени которого будут созданы заметки. "
                 "Если не указан, берётся первый активный администратор/управляющий.",
        )

    def _get_note_author(self, user_id: int | None) -> User:
        """
        Определяем, от чьего имени создавать заметки.
        1) Если передан user_id — берём этого пользователя.
        2) Иначе ищем ADMIN / GROUP_MANAGER / суперпользователя.
        """
        from accounts.models import User as UserModel

        if user_id:
            return UserModel.objects.get(id=user_id)

        # Сначала суперпользователь
        qs = UserModel.objects.filter(is_active=True, is_superuser=True).order_by("id")
        user = qs.first()
        if user:
            return user

        # Затем администратор / управляющий
        qs = UserModel.objects.filter(
            is_active=True,
            role__in=[UserModel.Role.ADMIN, UserModel.Role.GROUP_MANAGER],
        ).order_by("id")
        user = qs.first()
        if not user:
            raise RuntimeError("Не найден ни один активный пользователь с правами администратора/управляющего.")
        return user

    def handle(self, *args, **options):
        target_year = int(options["year"])
        months = int(options["months"])
        dry_run = bool(options["dry_run"])
        user_id = options.get("user_id")
        
        # Сначала обрабатываем выполненные задачи старше N месяцев
        self._cleanup_completed_tasks(months, dry_run)
        
        # Затем обрабатываем старые задачи по году дедлайна

        self._cleanup_old_tasks_by_year(target_year, dry_run, user_id)
    
    def _cleanup_completed_tasks(self, months: int, dry_run: bool):
        """Удаляет задачи со статусом 'Выполнено' старше указанного количества месяцев."""
        from datetime import timedelta
        
        cutoff_date = timezone.now() - timedelta(days=months * 30)
        
        self.stdout.write(
            self.style.WARNING(
                f"Поиск выполненных задач старше {months} месяцев (completed_at < {cutoff_date.date()})."
            )
        )
        
        qs = Task.objects.filter(
            status=Task.Status.DONE,
            completed_at__isnull=False,
            completed_at__lt=cutoff_date
        )
        
        total = qs.count()
        if total == 0:
            self.stdout.write(self.style.SUCCESS("Выполненных задач для удаления не найдено."))
            return
        
        self.stdout.write(
            self.style.WARNING(
                f"Найдено выполненных задач для удаления: {total}."
            )
        )
        
        if dry_run:
            examples = qs[:5]
            self.stdout.write("Примеры задач для удаления:")
            for t in examples:
                self.stdout.write(
                    f"- {t.id} | completed_at={t.completed_at} | title={t.title!r}"
                )
            self.stdout.write(
                self.style.WARNING(
                    "\nЭто DRY RUN, никаких изменений не внесено. "
                    "Запустите без --dry-run для реального удаления."
                )
            )
            return
        
        deleted_count = 0
        batch_size = 500
        ids = list(qs.values_list("id", flat=True))
        
        for i in range(0, len(ids), batch_size):
            batch_ids = ids[i : i + batch_size]
            with transaction.atomic():
                Task.objects.filter(id__in=batch_ids).delete()
                deleted_count += len(batch_ids)
        
        self.stdout.write(
            self.style.SUCCESS(
                f"Готово. Удалено выполненных задач: {deleted_count}."
            )
        )
    
    def _cleanup_old_tasks_by_year(self, target_year: int, dry_run: bool, user_id: int | None):
        """Переносит старые задачи (по году дедлайна) в заметки компании и удаляет их."""
        self.stdout.write(
            self.style.WARNING(
                f"Поиск задач с дедлайном раньше {target_year} года (due_at.year < {target_year})."
            )
        )

        qs = Task.objects.all().select_related("company", "type")
        qs = qs.filter(due_at__isnull=False)
        qs = qs.filter(due_at__year__lt=target_year)

        total = qs.count()
        if total == 0:
            self.stdout.write(self.style.SUCCESS("Старых задач по дедлайну не найдено."))
            return

        with_company = qs.filter(company__isnull=False).count()
        without_company = total - with_company

        self.stdout.write(
            self.style.WARNING(
                f"Найдено задач: всего={total}, с компанией={with_company}, без компании={without_company}."
            )
        )

        if dry_run:
            examples = qs[:5]
            self.stdout.write("Примеры задач для удаления:")
            for t in examples:
                self.stdout.write(
                    f"- {t.id} | company={t.company_id or '—'} | "
                    f"due_at={t.due_at} | title={t.title!r}"
                )
            self.stdout.write(
                self.style.WARNING(
                    "\nЭто DRY RUN, никаких изменений не внесено. "
                    "Запустите без --dry-run для реального удаления."
                )
            )
            return

        author = self._get_note_author(user_id)
        self.stdout.write(self.style.SUCCESS(f"Заметки будут создаваться от имени пользователя: {author} (id={author.id})"))

        # Импортируем здесь, чтобы избежать циклических импортов на уровне модуля
        from ui.views import _create_note_from_task

        created_notes = 0
        deleted_tasks = 0

        # Обрабатываем задачами порциями, чтобы не держать всё в памяти
        batch_size = 500
        ids = list(qs.values_list("id", flat=True))

        for i in range(0, len(ids), batch_size):
            batch_ids = ids[i : i + batch_size]
            batch_qs = (
                Task.objects.filter(id__in=batch_ids)
                .select_related("company", "type")
            )
            with transaction.atomic():
                for task in batch_qs:
                    if task.company_id:
                        _create_note_from_task(task, author)
                        created_notes += 1
                    task.delete()
                    deleted_tasks += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Готово. Создано заметок: {created_notes}, удалено задач: {deleted_tasks}."
            )
        )

