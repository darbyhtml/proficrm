"""
Django management command для исправления внутренних номеров, которые были сохранены как отдельные телефоны.

Находит телефоны, которые являются внутренними номерами (внутр. 22-067 и т.п.),
и переносит их в comment предыдущего телефона того же контакта.
"""

from django.core.management.base import BaseCommand
from companies.models import Contact, ContactPhone
from amocrm.migrate import _is_internal_phone_comment
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Исправляет внутренние номера: переносит их из отдельных телефонов в comment предыдущего телефона"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Только показать, что будет сделано, без реальных изменений",
        )
        parser.add_argument(
            "--limit",
            type=int,
            help="Обработать только первые N контактов (для тестирования)",
        )

    def handle(self, *args, **options):
        dry_run = options.get("dry_run", False)
        limit = options.get("limit")

        self.stdout.write(self.style.SUCCESS(f"\n{'=' * 80}"))
        self.stdout.write(self.style.SUCCESS("ИСПРАВЛЕНИЕ ВНУТРЕННИХ НОМЕРОВ"))
        self.stdout.write(self.style.SUCCESS(f"{'=' * 80}\n"))

        if dry_run:
            self.stdout.write(self.style.WARNING("⚠️  РЕЖИМ DRY-RUN: изменения не будут сохранены\n"))

        # Находим все контакты с телефонами
        contacts = Contact.objects.filter(phones__isnull=False).distinct()

        if limit:
            contacts = contacts[:limit]

        total_contacts = contacts.count()
        self.stdout.write(f"Найдено контактов с телефонами: {total_contacts}\n")

        if total_contacts == 0:
            self.stdout.write(self.style.SUCCESS("Нет контактов для обработки."))
            return

        processed = 0
        fixed_contacts = 0
        fixed_phones = 0
        deleted_phones = 0
        errors = 0

        for contact in contacts:
            try:
                processed += 1
                if processed % 100 == 0:
                    self.stdout.write(f"Обработано: {processed}/{total_contacts}...")

                # Получаем все телефоны контакта, отсортированные по ID (порядок создания)
                phones = list(contact.phones.all().order_by("id"))
                if len(phones) < 2:
                    continue  # Нужно минимум 2 телефона

                contact_fixed = False
                phones_to_delete = []
                phones_to_update = []

                # Проходим по телефонам с конца (чтобы не сбить индексы при удалении)
                for i in range(len(phones) - 1, 0, -1):  # От последнего ко второму
                    phone = phones[i]
                    phone_value = (phone.value or "").strip()

                    # Проверяем, является ли это внутренним номером
                    if _is_internal_phone_comment(phone_value):
                        # Это внутренний номер - переносим в comment предыдущего телефона
                        prev_phone = phones[i - 1]
                        existing_comment = (prev_phone.comment or "").strip()

                        if existing_comment:
                            # Объединяем с существующим комментарием
                            new_comment = f"{existing_comment}; {phone_value}"
                        else:
                            new_comment = phone_value

                        new_comment = new_comment[:255]  # Ограничение длины

                        if prev_phone.comment != new_comment:
                            prev_phone.comment = new_comment
                            phones_to_update.append(prev_phone)
                            contact_fixed = True

                        # Помечаем внутренний номер на удаление
                        phones_to_delete.append(phone)
                        fixed_phones += 1

                        if processed <= 10:
                            self.stdout.write(
                                f"  Контакт {contact.id}: внутренний номер '{phone_value}' будет перенесен в comment телефона {prev_phone.id}"
                            )

                if contact_fixed:
                    fixed_contacts += 1
                    if not dry_run:
                        # Обновляем комментарии
                        for phone in phones_to_update:
                            phone.save(update_fields=["comment"])

                        # Удаляем внутренние номера
                        for phone in phones_to_delete:
                            phone.delete()
                            deleted_phones += 1

            except Exception as e:
                errors += 1
                logger.error(f"Ошибка при обработке контакта {contact.id}: {e}", exc_info=True)
                if processed <= 10:
                    self.stdout.write(
                        self.style.ERROR(f"  Ошибка при обработке контакта {contact.id}: {e}")
                    )

        self.stdout.write(self.style.SUCCESS(f"\n{'=' * 80}"))
        self.stdout.write(self.style.SUCCESS("РЕЗУЛЬТАТЫ"))
        self.stdout.write(self.style.SUCCESS(f"{'=' * 80}\n"))

        self.stdout.write(f"Обработано контактов: {processed}")
        self.stdout.write(f"Исправлено контактов: {fixed_contacts}")
        self.stdout.write(f"Перенесено внутренних номеров: {fixed_phones}")
        self.stdout.write(f"Удалено телефонов (внутренние номера): {deleted_phones}")
        if errors > 0:
            self.stdout.write(self.style.ERROR(f"Ошибок: {errors}"))

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    "\n⚠️  Это был DRY-RUN. Для реального исправления запустите без --dry-run"
                )
            )
        else:
            self.stdout.write(self.style.SUCCESS("\n✅ Исправление завершено!"))
