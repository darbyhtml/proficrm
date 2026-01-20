"""
Django management command для переноса примечаний из raw_fields в comment первого телефона.

Находит все контакты, у которых в raw_fields есть extracted_note_text,
и переносит это примечание в comment первого телефона (если его там еще нет).

Также находит "ложные" телефоны (значения, которые не похожи на телефоны, например "внутр. 22-067")
и переносит их в комментарии к существующим телефонам.
"""

from django.core.management.base import BaseCommand, CommandError
from companies.models import Contact, ContactPhone
import logging
import re

logger = logging.getLogger(__name__)


def _looks_like_phone(value: str) -> bool:
    """
    Проверяет, похоже ли значение на номер телефона.
    Телефон должен содержать достаточно цифр (минимум 7-10 цифр).
    """
    if not value or not isinstance(value, str):
        return False
    # Извлекаем только цифры
    digits = ''.join(c for c in value if c.isdigit())
    # Телефон должен содержать минимум 7 цифр (короткие номера) или больше
    # Но не слишком много (максимум 15 цифр для международных номеров)
    if len(digits) < 7 or len(digits) > 15:
        return False
    # Если значение содержит только буквы и пробелы - это не телефон
    if not any(c.isdigit() for c in value):
        return False
    return True


class Command(BaseCommand):
    help = "Переносит примечания из raw_fields.extracted_note_text в comment первого телефона контакта"

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
        self.stdout.write(self.style.SUCCESS("ПЕРЕНОС ПРИМЕЧАНИЙ В КОММЕНТАРИИ ТЕЛЕФОНОВ"))
        self.stdout.write(self.style.SUCCESS(f"{'=' * 80}\n"))

        if dry_run:
            self.stdout.write(self.style.WARNING("⚠️  РЕЖИМ DRY-RUN: изменения не будут сохранены\n"))

        # Находим все контакты с extracted_note_text в raw_fields
        contacts = Contact.objects.filter(
            raw_fields__extracted_note_text__isnull=False
        ).exclude(raw_fields__extracted_note_text="")

        if limit:
            contacts = contacts[:limit]

        total_contacts = contacts.count()
        self.stdout.write(f"Найдено контактов с примечаниями: {total_contacts}\n")

        if total_contacts == 0:
            self.stdout.write(self.style.SUCCESS("Нет контактов для обработки."))
            return

        processed = 0
        updated = 0
        skipped_no_phones = 0
        skipped_has_comment = 0
        fake_phones_fixed = 0  # Счетчик исправленных "ложных" телефонов
        errors = 0

        # Сначала обрабатываем "ложные" телефоны (значения, которые не похожи на телефоны)
        self.stdout.write("Шаг 1: Поиск и исправление 'ложных' телефонов...\n")
        all_contacts_with_phones = Contact.objects.filter(phones__isnull=False).distinct()
        if limit:
            all_contacts_with_phones = all_contacts_with_phones[:limit]
        
        for contact in all_contacts_with_phones:
            try:
                # Проверяем все телефоны контакта
                phones_list = list(contact.phones.all())
                if not phones_list:
                    continue
                
                # Находим "ложные" телефоны (не похожие на телефоны)
                fake_phones = []
                real_phones = []
                
                for phone in phones_list:
                    if _looks_like_phone(phone.value):
                        real_phones.append(phone)
                    else:
                        fake_phones.append(phone)
                
                if fake_phones:
                    # Если есть реальные телефоны, переносим "ложные" в комментарии
                    if real_phones:
                        first_real_phone = real_phones[0]
                        fake_texts = [p.value for p in fake_phones]
                        combined_fake_text = " | ".join(fake_texts)
                        
                        existing_comment = (first_real_phone.comment or "").strip()
                        if existing_comment:
                            new_comment = f"{existing_comment}; {combined_fake_text[:200]}"
                            new_comment = new_comment[:255]
                        else:
                            new_comment = combined_fake_text[:255]
                        
                        if not dry_run:
                            first_real_phone.comment = new_comment
                            first_real_phone.save(update_fields=["comment"])
                            # Удаляем "ложные" телефоны
                            for fake_phone in fake_phones:
                                fake_phone.delete()
                        
                        fake_phones_fixed += len(fake_phones)
                        if processed < 10:
                            self.stdout.write(
                                f"  Контакт {contact.id}: перенесено {len(fake_phones)} 'ложных' телефонов в комментарий"
                            )
                    else:
                        # Если нет реальных телефонов, оставляем как есть (или можно удалить)
                        if processed < 10:
                            self.stdout.write(
                                self.style.WARNING(
                                    f"  Контакт {contact.id}: все телефоны 'ложные', но нет реальных телефонов для переноса"
                                )
                            )
                
            except Exception as e:
                errors += 1
                logger.error(f"Ошибка при обработке контакта {contact.id}: {e}", exc_info=True)
        
        self.stdout.write(f"\nИсправлено 'ложных' телефонов: {fake_phones_fixed}\n")
        
        # Теперь обрабатываем примечания из raw_fields
        self.stdout.write("Шаг 2: Перенос примечаний из raw_fields в комментарии телефонов...\n")
        
        for contact in contacts:
            try:
                processed += 1
                if processed % 100 == 0:
                    self.stdout.write(f"Обработано: {processed}/{total_contacts}...")

                # Извлекаем примечание из raw_fields
                raw_fields = contact.raw_fields or {}
                note_text = raw_fields.get("extracted_note_text", "")

                if not note_text or not isinstance(note_text, str):
                    continue

                note_text = str(note_text).strip()
                if not note_text:
                    continue

                # Получаем первый телефон контакта
                first_phone = contact.phones.first()
                if not first_phone:
                    skipped_no_phones += 1
                    if processed <= 10:
                        self.stdout.write(
                            self.style.WARNING(
                                f"  Контакт {contact.id}: нет телефонов, пропускаем"
                            )
                        )
                    continue

                # Проверяем, есть ли уже это примечание в комментарии
                existing_comment = (first_phone.comment or "").strip()
                if existing_comment:
                    # Если примечание уже есть в комментарии (полностью или частично), пропускаем
                    if note_text in existing_comment or existing_comment in note_text:
                        skipped_has_comment += 1
                        if processed <= 10:
                            self.stdout.write(
                                self.style.WARNING(
                                    f"  Контакт {contact.id}: примечание уже в комментарии, пропускаем"
                                )
                            )
                        continue

                    # Объединяем с существующим комментарием
                    new_comment = f"{existing_comment}; {note_text[:200]}"
                    new_comment = new_comment[:255]
                else:
                    # Просто добавляем примечание
                    new_comment = note_text[:255]

                if not dry_run:
                    first_phone.comment = new_comment
                    first_phone.save(update_fields=["comment"])
                    updated += 1
                else:
                    updated += 1
                    if processed <= 10:
                        self.stdout.write(
                            f"  Контакт {contact.id}: будет обновлен комментарий телефона {first_phone.id}"
                        )
                        self.stdout.write(f"    Старый: {existing_comment or '(пусто)'}")
                        self.stdout.write(f"    Новый: {new_comment}")

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
        self.stdout.write(f"Обновлено телефонов: {updated}")
        self.stdout.write(f"Пропущено (нет телефонов): {skipped_no_phones}")
        self.stdout.write(f"Пропущено (уже есть в комментарии): {skipped_has_comment}")
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
