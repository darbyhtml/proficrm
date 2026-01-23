"""
Команда для миграции телефонов из ContactPhone (служебные контакты из AMO) в CompanyPhone.

Переносит телефоны из служебных контактов (где amocrm_contact_id отрицательный)
в модель CompanyPhone для правильного отображения в разделе "Дополнительные телефоны".
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Max

from companies.models import Company, Contact, ContactPhone, CompanyPhone
from ui.forms import _normalize_phone


class Command(BaseCommand):
    help = (
        "Миграция телефонов из ContactPhone (служебные контакты из AMO) в CompanyPhone.\n"
        "Переносит телефоны из служебных контактов (amocrm_contact_id < 0) в CompanyPhone."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Только показать, что будет перенесено, без выполнения изменений",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Ограничить количество обрабатываемых компаний (для тестирования, 0 = все)",
        )
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Показать детальную информацию о каждой миграции",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        limit = options.get("limit", 0)
        verbose = options.get("verbose", False)

        self.stdout.write(self.style.SUCCESS("=" * 80))
        self.stdout.write(self.style.SUCCESS("Миграция телефонов из ContactPhone в CompanyPhone"))
        self.stdout.write(self.style.SUCCESS("=" * 80))
        
        if dry_run:
            self.stdout.write(self.style.WARNING("\n⚠️  РЕЖИМ ПРОВЕРКИ (dry-run) - изменения не будут сохранены\n"))
        else:
            self.stdout.write(self.style.WARNING("\n⚠️  РЕЖИМ МИГРАЦИИ - изменения будут сохранены в БД\n"))

        # Находим все служебные контакты (amocrm_contact_id < 0)
        stub_contacts = Contact.objects.filter(amocrm_contact_id__lt=0).select_related("company")
        
        if limit > 0:
            stub_contacts = stub_contacts[:limit]
            self.stdout.write(self.style.WARNING(f"Ограничение: обрабатываем только первые {limit} контактов"))

        total_contacts = stub_contacts.count()
        self.stdout.write(f"\nВсего служебных контактов найдено: {total_contacts}")

        migrated_count = 0
        skipped_count = 0
        error_count = 0

        for contact in stub_contacts:
            company = contact.company
            phones = ContactPhone.objects.filter(contact=contact)
            
            if not phones.exists():
                if verbose:
                    self.stdout.write(f"  Пропуск: контакт {contact.id} (компания {company.name}) - нет телефонов")
                skipped_count += 1
                continue

            # Получаем максимальный order для существующих телефонов компании
            max_order = CompanyPhone.objects.filter(company=company).aggregate(m=Max("order")).get("m")
            next_order = int(max_order) + 1 if max_order is not None else 0

            migrated_phones = []
            for phone in phones:
                v = phone.value.strip() if phone.value else ""
                if not v:
                    continue
                
                # Нормализуем телефон
                normalized = _normalize_phone(v) if v else ""
                if not normalized:
                    normalized = v  # Если нормализация не удалась, используем исходное значение
                
                # Проверяем, что такого телефона еще нет
                if (company.phone or "").strip() == normalized:
                    if verbose:
                        self.stdout.write(f"  Пропуск: телефон {v} уже является основным для компании {company.name}")
                    skipped_count += 1
                    continue
                
                if CompanyPhone.objects.filter(company=company, value=normalized).exists():
                    if verbose:
                        self.stdout.write(f"  Пропуск: телефон {v} уже существует в CompanyPhone для компании {company.name}")
                    skipped_count += 1
                    continue
                
                if not dry_run:
                    try:
                        CompanyPhone.objects.create(company=company, value=normalized, order=next_order)
                        migrated_phones.append(v)
                        next_order += 1
                    except Exception as e:
                        self.stdout.write(self.style.ERROR(f"  Ошибка при создании CompanyPhone для компании {company.name}, телефон {v}: {e}"))
                        error_count += 1
                else:
                    migrated_phones.append(v)
                    next_order += 1

            if migrated_phones:
                migrated_count += len(migrated_phones)
                if verbose:
                    self.stdout.write(f"  ✓ Компания {company.name}: перенесено {len(migrated_phones)} телефонов: {', '.join(migrated_phones)}")
                elif not dry_run:
                    self.stdout.write(f"  ✓ Компания {company.name}: перенесено {len(migrated_phones)} телефонов")

        self.stdout.write(self.style.SUCCESS("\n" + "=" * 80))
        self.stdout.write(self.style.SUCCESS("Результаты миграции:"))
        self.stdout.write(f"  Перенесено телефонов: {migrated_count}")
        self.stdout.write(f"  Пропущено: {skipped_count}")
        self.stdout.write(f"  Ошибок: {error_count}")
        self.stdout.write(self.style.SUCCESS("=" * 80))
