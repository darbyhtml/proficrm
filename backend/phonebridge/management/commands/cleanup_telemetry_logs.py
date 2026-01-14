"""
Management команда для очистки старых телеметрии и логов по TTL политике.

Использование:
    python manage.py cleanup_telemetry_logs

Политика TTL:
    - Телеметрия: 30 дней
    - Логи: 14 дней (или только ошибочные bundle'ы старше 7 дней)
"""

from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from phonebridge.models import PhoneTelemetry, PhoneLogBundle


class Command(BaseCommand):
    help = "Очистка старых телеметрии и логов по TTL политике"

    def add_arguments(self, parser):
        parser.add_argument(
            "--telemetry-days",
            type=int,
            default=30,
            help="TTL для телеметрии в днях (по умолчанию: 30)",
        )
        parser.add_argument(
            "--logs-days",
            type=int,
            default=14,
            help="TTL для логов в днях (по умолчанию: 14)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Показать что будет удалено, но не удалять",
        )

    def handle(self, *args, **options):
        telemetry_days = options["telemetry_days"]
        logs_days = options["logs_days"]
        dry_run = options["dry_run"]

        now = timezone.now()
        telemetry_cutoff = now - timedelta(days=telemetry_days)
        logs_cutoff = now - timedelta(days=logs_days)

        # Очистка телеметрии
        telemetry_qs = PhoneTelemetry.objects.filter(ts__lt=telemetry_cutoff)
        telemetry_count = telemetry_qs.count()

        if telemetry_count > 0:
            if dry_run:
                self.stdout.write(
                    self.style.WARNING(
                        f"[DRY RUN] Будет удалено {telemetry_count} записей телеметрии старше {telemetry_days} дней"
                    )
                )
            else:
                deleted_telemetry = telemetry_qs.delete()[0]
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Удалено {deleted_telemetry} записей телеметрии старше {telemetry_days} дней"
                    )
                )
        else:
            self.stdout.write("Нет телеметрии для удаления")

        # Очистка логов
        logs_qs = PhoneLogBundle.objects.filter(ts__lt=logs_cutoff)
        logs_count = logs_qs.count()

        if logs_count > 0:
            if dry_run:
                self.stdout.write(
                    self.style.WARNING(
                        f"[DRY RUN] Будет удалено {logs_count} лог-бандлов старше {logs_days} дней"
                    )
                )
            else:
                deleted_logs = logs_qs.delete()[0]
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Удалено {deleted_logs} лог-бандлов старше {logs_days} дней"
                    )
                )
        else:
            self.stdout.write("Нет логов для удаления")

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    "\nЭто был dry-run. Для реального удаления запустите без --dry-run"
                )
            )
