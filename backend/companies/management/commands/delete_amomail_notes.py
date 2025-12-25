"""
Удаление всех заметок типа amomail_message из базы данных.
"""
from django.core.management.base import BaseCommand
from django.db import models

from companies.models import CompanyNote


class Command(BaseCommand):
    help = "Удалить все заметки типа amomail_message (письма из amoCRM)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Не удалять, только показать статистику",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        
        # Ищем все заметки с external_source содержащим "amomail" или текстом содержащим "amomail"
        # Также ищем по тексту заметки, так как старые заметки могут иметь "type: amomail" в тексте
        notes_qs = CompanyNote.objects.filter(
            models.Q(external_source__icontains="amomail") |
            models.Q(text__icontains="type: amomail") |
            models.Q(text__icontains="Письмо (amoMail)")
        )
        
        count = notes_qs.count()
        
        if count == 0:
            self.stdout.write(self.style.SUCCESS("Заметок типа amomail не найдено."))
            return
        
        self.stdout.write(
            self.style.WARNING(f"Найдено заметок типа amomail: {count}")
        )
        
        if dry_run:
            # Показываем примеры
            examples = notes_qs[:5]
            for note in examples:
                self.stdout.write(
                    f"  - ID: {note.id}, Компания: {note.company.name if note.company else 'N/A'}, "
                    f"Источник: {note.external_source}, UID: {note.external_uid}"
                )
            if count > 5:
                self.stdout.write(f"  ... и еще {count - 5} заметок")
            self.stdout.write(self.style.WARNING("\nЭто был DRY RUN. Для реального удаления запустите команду без --dry-run"))
        else:
            deleted = notes_qs.delete()[0]
            self.stdout.write(
                self.style.SUCCESS(f"✓ Удалено заметок: {deleted}")
            )

