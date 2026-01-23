"""
Команда для проверки телефонов конкретной компании.
Показывает, где находятся телефоны: в CompanyPhone, ContactPhone, или в основном поле.
"""
from django.core.management.base import BaseCommand
from django.db.models import Q

from companies.models import Company, CompanyPhone, Contact, ContactPhone


class Command(BaseCommand):
    help = "Проверка телефонов компании по ID или amocrm_company_id"

    def add_arguments(self, parser):
        parser.add_argument(
            "--company-id",
            type=str,
            help="UUID компании",
        )
        parser.add_argument(
            "--amocrm-id",
            type=int,
            help="ID компании в AMO CRM",
        )

    def handle(self, *args, **options):
        company_id = options.get("company_id")
        amocrm_id = options.get("amocrm_id")

        if not company_id and not amocrm_id:
            self.stdout.write(self.style.ERROR("Укажите --company-id или --amocrm-id"))
            return

        # Находим компанию
        if company_id:
            try:
                company = Company.objects.get(id=company_id)
            except Company.DoesNotExist:
                self.stdout.write(self.style.ERROR(f"Компания с ID {company_id} не найдена"))
                return
        else:
            try:
                company = Company.objects.get(amocrm_company_id=amocrm_id)
            except Company.DoesNotExist:
                self.stdout.write(self.style.ERROR(f"Компания с amocrm_company_id {amocrm_id} не найдена"))
                return

        self.stdout.write(self.style.SUCCESS("=" * 80))
        self.stdout.write(self.style.SUCCESS(f"Компания: {company.name}"))
        self.stdout.write(f"ID: {company.id}")
        self.stdout.write(f"AMO ID: {company.amocrm_company_id}")
        self.stdout.write(self.style.SUCCESS("=" * 80))

        # Основной телефон
        self.stdout.write("\n📞 ОСНОВНОЙ ТЕЛЕФОН:")
        if company.phone:
            self.stdout.write(f"  ✓ {company.phone}")
        else:
            self.stdout.write(self.style.WARNING("  — (пусто)"))

        # Дополнительные телефоны (CompanyPhone)
        self.stdout.write("\n📱 ДОПОЛНИТЕЛЬНЫЕ ТЕЛЕФОНЫ (CompanyPhone):")
        company_phones = CompanyPhone.objects.filter(company=company).order_by("order", "value")
        if company_phones.exists():
            for phone in company_phones:
                self.stdout.write(f"  ✓ {phone.value} (order={phone.order}, id={phone.id})")
        else:
            self.stdout.write(self.style.WARNING("  — (нет)"))

        # Телефоны в обычных контактах
        self.stdout.write("\n👤 ТЕЛЕФОНЫ В КОНТАКТАХ (ContactPhone):")
        contacts = Contact.objects.filter(company=company)
        contact_phones_count = 0
        for contact in contacts:
            phones = ContactPhone.objects.filter(contact=contact)
            if phones.exists():
                for phone in phones:
                    contact_phones_count += 1
                    contact_info = f"{contact.first_name} {contact.last_name}".strip() or f"Контакт #{contact.id}"
                    self.stdout.write(f"  ✓ {phone.value} ({contact_info}, amocrm_contact_id={contact.amocrm_contact_id})")
        
        if contact_phones_count == 0:
            self.stdout.write(self.style.WARNING("  — (нет)"))

        # Служебные контакты (stub contacts)
        self.stdout.write("\n🔧 СЛУЖЕБНЫЕ КОНТАКТЫ (stub, amocrm_contact_id < 0):")
        stub_contacts = Contact.objects.filter(company=company, amocrm_contact_id__lt=0)
        stub_phones_count = 0
        for contact in stub_contacts:
            phones = ContactPhone.objects.filter(contact=contact)
            if phones.exists():
                for phone in phones:
                    stub_phones_count += 1
                    self.stdout.write(f"  ⚠️  {phone.value} (stub contact #{contact.id}, amocrm_contact_id={contact.amocrm_contact_id})")
        
        if stub_phones_count == 0:
            self.stdout.write(self.style.WARNING("  — (нет)"))
        else:
            self.stdout.write(self.style.WARNING(f"\n⚠️  ВНИМАНИЕ: Найдено {stub_phones_count} телефонов в служебных контактах!"))
            self.stdout.write(self.style.WARNING("  Эти телефоны нужно перенести в CompanyPhone командой:"))
            self.stdout.write(self.style.WARNING("  python manage.py migrate_amo_phones_to_company_phones"))

        # Итого
        self.stdout.write(self.style.SUCCESS("\n" + "=" * 80))
        total_phones = (1 if company.phone else 0) + company_phones.count() + contact_phones_count
        self.stdout.write(f"ИТОГО телефонов: {total_phones}")
        self.stdout.write(self.style.SUCCESS("=" * 80))
