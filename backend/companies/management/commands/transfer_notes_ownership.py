"""
Django management command для переноса владения заметок с author=None на ответственного за компанию.

Находит все заметки компаний, у которых author=None, и переносит их во владение
ответственного за компанию (company.responsible).
"""

from django.core.management.base import BaseCommand
from django.db.models import Q
from companies.models import CompanyNote, Company
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Переносит владение заметок с author=None на ответственного за компанию"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Только показать, что будет сделано, без реальных изменений",
        )
        parser.add_argument(
            "--limit",
            type=int,
            help="Обработать только первые N заметок (для тестирования)",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=1000,
            help="Размер батча для bulk_update (по умолчанию: 1000)",
        )

    def handle(self, *args, **options):
        dry_run = options.get("dry_run", False)
        limit = options.get("limit")
        batch_size = options.get("batch_size", 1000)

        self.stdout.write(self.style.SUCCESS(f"\n{'=' * 80}"))
        self.stdout.write(self.style.SUCCESS("ПЕРЕНОС ВЛАДЕНИЯ ЗАМЕТОК С AUTHOR=None"))
        self.stdout.write(self.style.SUCCESS(f"{'=' * 80}\n"))

        if dry_run:
            self.stdout.write(self.style.WARNING("⚠️  РЕЖИМ DRY-RUN: изменения не будут сохранены\n"))

        # Находим все заметки с author=None, у которых есть компания с ответственным
        notes_qs = (
            CompanyNote.objects.filter(author__isnull=True)
            .select_related("company", "company__responsible")
            .filter(company__isnull=False, company__responsible__isnull=False)
        )
        
        if limit:
            notes_qs = notes_qs[:limit]

        total_notes = notes_qs.count()
        self.stdout.write(f"Найдено заметок с author=None (с ответственным): {total_notes}\n")

        if total_notes == 0:
            self.stdout.write(self.style.SUCCESS("Нет заметок для обработки."))
            return

        # Подсчитываем заметки без ответственного
        notes_no_responsible = (
            CompanyNote.objects.filter(author__isnull=True)
            .filter(Q(company__isnull=True) | Q(company__responsible__isnull=True))
            .count()
        )
        if notes_no_responsible > 0:
            self.stdout.write(
                self.style.WARNING(f"Пропущено заметок без ответственного: {notes_no_responsible}\n")
            )

        transferred = 0
        skipped_no_responsible = 0
        errors = 0
        batch = []

        self.stdout.write(f"Обработка батчами по {batch_size} заметок...\n")

        for note in notes_qs.iterator(chunk_size=batch_size):
            try:
                company = note.company
                responsible = company.responsible

                if not company or not responsible:
                    skipped_no_responsible += 1
                    continue

                note.author = responsible
                batch.append(note)
                transferred += 1

                # Показываем первые несколько примеров
                if transferred <= 10:
                    self.stdout.write(
                        f"  Заметка {note.id} (компания: {company.name}): "
                        f"{'будет перенесено' if dry_run else 'перенесено'} на {responsible}"
                    )

                # Выполняем bulk_update при достижении размера батча
                if len(batch) >= batch_size:
                    if not dry_run:
                        CompanyNote.objects.bulk_update(batch, ["author"], batch_size=batch_size)
                    batch = []
                    self.stdout.write(f"  Обработано: {transferred}/{total_notes} заметок...")

            except Exception as e:
                errors += 1
                logger.error(f"Ошибка при обработке заметки {note.id}: {e}", exc_info=True)
                if errors <= 10:
                    self.stdout.write(
                        self.style.ERROR(f"  Ошибка при обработке заметки {note.id}: {e}")
                    )

        # Обрабатываем оставшиеся заметки в батче
        if batch:
            if not dry_run:
                CompanyNote.objects.bulk_update(batch, ["author"], batch_size=batch_size)
            self.stdout.write(f"  Обработано: {transferred}/{total_notes} заметок...")

        self.stdout.write(self.style.SUCCESS(f"\n{'=' * 80}"))
        self.stdout.write(self.style.SUCCESS("РЕЗУЛЬТАТЫ"))
        self.stdout.write(self.style.SUCCESS(f"{'=' * 80}\n"))

        self.stdout.write(f"Обработано заметок: {total_notes}")
        self.stdout.write(f"Перенесено владения: {transferred}")
        if skipped_no_responsible > 0:
            self.stdout.write(f"Пропущено (нет ответственного): {skipped_no_responsible}")
        if errors > 0:
            self.stdout.write(self.style.ERROR(f"Ошибок: {errors}"))

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    "\n⚠️  Это был DRY-RUN. Для реального переноса запустите без --dry-run"
                )
            )
        else:
            self.stdout.write(self.style.SUCCESS("\n✅ Перенос завершен!"))
