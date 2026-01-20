"""
Django management command для ручного ввода данных о квоте smtp.bz.
Используется, если API недоступен или не работает.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from mailer.models import SmtpBzQuota


class Command(BaseCommand):
    help = "Вручную устанавливает данные о квоте smtp.bz (если API недоступен)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--emails-limit",
            type=int,
            help="Лимит писем в месяц (например, 15000)",
        )
        parser.add_argument(
            "--emails-available",
            type=int,
            help="Доступно писем (например, 14794)",
        )
        parser.add_argument(
            "--max-per-hour",
            type=int,
            default=100,
            help="Максимум писем в час (по умолчанию 100)",
        )
        parser.add_argument(
            "--tariff-name",
            type=str,
            default="",
            help="Название тарифа (например, FREE)",
        )

    def handle(self, *args, **options):
        quota = SmtpBzQuota.load()
        
        if options.get("emails_limit"):
            quota.emails_limit = options["emails_limit"]
        
        if options.get("emails_available") is not None:
            quota.emails_available = options["emails_available"]
        
        if options.get("max_per_hour"):
            quota.max_per_hour = options["max_per_hour"]
        
        if options.get("tariff_name"):
            quota.tariff_name = options["tariff_name"]
        
        quota.last_synced_at = timezone.now()
        quota.sync_error = ""
        quota.save()
        
        self.stdout.write(self.style.SUCCESS("✅ Данные о квоте обновлены вручную"))
        self.stdout.write(f"   Тариф: {quota.tariff_name or '—'}")
        self.stdout.write(f"   Доступно писем: {quota.emails_available} / {quota.emails_limit}")
        self.stdout.write(f"   Лимит в час: {quota.max_per_hour}")
        self.stdout.write(f"   Обновлено: {quota.last_synced_at}")
