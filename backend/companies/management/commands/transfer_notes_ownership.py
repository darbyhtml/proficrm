"""
Django management command для переноса владения заметок с author=None на ответственного за компанию.

Находит все заметки компаний, у которых author=None, и переносит их во владение
ответственного за компанию (company.responsible).
"""

from django.core.management.base import BaseCommand
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

    def handle(self, *args, **options):
        dry_run = options.get("dry_run", False)
        limit = options.get("limit")

        self.stdout.write(self.style.SUCCESS(f"\n{'=' * 80}"))
        self.stdout.write(self.style.SUCCESS("ПЕРЕНОС ВЛАДЕНИЯ ЗАМЕТОК С AUTHOR=None"))
        self.stdout.write(self.style.SUCCESS(f"{'=' * 80}\n"))

        if dry_run:
            self.stdout.write(self.style.WARNING("⚠️  РЕЖИМ DRY-RUN: изменения не будут сохранены\n"))

        # Находим все заметки с author=None
        notes_qs = CompanyNote.objects.filter(author__isnull=True).select_related("company", "company__responsible")
        
        if limit:
            notes_qs = notes_qs[:limit]

        total_notes = notes_qs.count()
        self.stdout.write(f"Найдено заметок с author=None: {total_notes}\n")

        if total_notes == 0:
            self.stdout.write(self.style.SUCCESS("Нет заметок для обработки."))
            return

        transferred = 0
        skipped_no_responsible = 0
        errors = 0

        for note in notes_qs:
            try:
                company = note.company
                if not company:
                    skipped_no_responsible += 1
                    if transferred + skipped_no_responsible <= 10:
                        self.stdout.write(
                            self.style.WARNING(f"  Заметка {note.id}: нет компании, пропускаем")
                        )
                    continue

                responsible = company.responsible
                if not responsible:
                    skipped_no_responsible += 1
                    if transferred + skipped_no_responsible <= 10:
                        self.stdout.write(
                            self.style.WARNING(
                                f"  Заметка {note.id} (компания: {company.name}): нет ответственного, пропускаем"
                            )
                        )
                    continue

                if not dry_run:
                    note.author = responsible
                    note.save(update_fields=["author"])
                    transferred += 1
                    if transferred <= 10:
                        self.stdout.write(
                            f"  Заметка {note.id} (компания: {company.name}): перенесено на {responsible}"
                        )
                else:
                    transferred += 1
                    if transferred <= 10:
                        self.stdout.write(
                            f"  Заметка {note.id} (компания: {company.name}): будет перенесено на {responsible}"
                        )

            except Exception as e:
                errors += 1
                logger.error(f"Ошибка при обработке заметки {note.id}: {e}", exc_info=True)
                if transferred + skipped_no_responsible + errors <= 10:
                    self.stdout.write(
                        self.style.ERROR(f"  Ошибка при обработке заметки {note.id}: {e}")
                    )

        self.stdout.write(self.style.SUCCESS(f"\n{'=' * 80}"))
        self.stdout.write(self.style.SUCCESS("РЕЗУЛЬТАТЫ"))
        self.stdout.write(self.style.SUCCESS(f"{'=' * 80}\n"))

        self.stdout.write(f"Обработано заметок: {total_notes}")
        self.stdout.write(f"Перенесено владения: {transferred}")
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
