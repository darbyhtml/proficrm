from __future__ import annotations

from datetime import datetime, timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from audit.models import ActivityEvent
from phonebridge.models import CallRequest


class Command(BaseCommand):
    help = "Очистка старых тестовых данных из аналитики (CallRequest и ActivityEvent)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--before-date",
            type=str,
            help="Удалить данные до указанной даты (формат: YYYY-MM-DD). По умолчанию - до начала текущего месяца.",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            help="Удалить ВСЕ данные аналитики (CallRequest с note='UI click' и все ActivityEvent). ВНИМАНИЕ: это удалит все данные!",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Не удалять, только показать статистику того, что будет удалено.",
        )
        parser.add_argument(
            "--only-calls",
            action="store_true",
            help="Удалить только CallRequest, не трогать ActivityEvent.",
        )
        parser.add_argument(
            "--only-events",
            action="store_true",
            help="Удалить только ActivityEvent, не трогать CallRequest.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        only_calls = options["only_calls"]
        only_events = options["only_events"]
        all_data = options["all"]

        if all_data:
            # Удаляем все данные
            before_date = None
            self.stdout.write(self.style.WARNING("ВНИМАНИЕ: Будет удалено ВСЕ данные аналитики!"))
        else:
            # Определяем дату
            before_date_str = options.get("before_date")
            if before_date_str:
                try:
                    before_date = datetime.strptime(before_date_str, "%Y-%m-%d").date()
                    before_date = timezone.make_aware(
                        datetime.combine(before_date, datetime.min.time())
                    )
                except ValueError:
                    self.stdout.write(self.style.ERROR(f"Неверный формат даты: {before_date_str}. Используйте YYYY-MM-DD"))
                    return
            else:
                # По умолчанию - до начала текущего месяца
                now = timezone.now()
                local_now = timezone.localtime(now)
                before_date = local_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                self.stdout.write(
                    self.style.WARNING(f"Используется дата по умолчанию: до начала текущего месяца ({before_date.strftime('%d.%m.%Y %H:%M')})")
                )

        # Подсчет и удаление CallRequest
        if not only_events:
            calls_qs = CallRequest.objects.filter(note="UI click")
            if not all_data:
                calls_qs = calls_qs.filter(created_at__lt=before_date)
            
            calls_count = calls_qs.count()
            
            if calls_count > 0:
                self.stdout.write(
                    self.style.WARNING(f"Найдено CallRequest для удаления: {calls_count}")
                )
                if not dry_run:
                    deleted_calls = calls_qs.delete()[0]
                    self.stdout.write(
                        self.style.SUCCESS(f"Удалено CallRequest: {deleted_calls}")
                    )
                else:
                    self.stdout.write(self.style.WARNING("  [DRY RUN] Не удалено"))
            else:
                self.stdout.write(self.style.SUCCESS("CallRequest для удаления не найдено"))

        # Подсчет и удаление ActivityEvent
        if not only_calls:
            events_qs = ActivityEvent.objects.all()
            if not all_data:
                events_qs = events_qs.filter(created_at__lt=before_date)
            
            events_count = events_qs.count()
            
            if events_count > 0:
                self.stdout.write(
                    self.style.WARNING(f"Найдено ActivityEvent для удаления: {events_count}")
                )
                if not dry_run:
                    deleted_events = events_qs.delete()[0]
                    self.stdout.write(
                        self.style.SUCCESS(f"Удалено ActivityEvent: {deleted_events}")
                    )
                else:
                    self.stdout.write(self.style.WARNING("  [DRY RUN] Не удалено"))
            else:
                self.stdout.write(self.style.SUCCESS("ActivityEvent для удаления не найдено"))

        if dry_run:
            self.stdout.write(self.style.WARNING("\nЭто был DRY RUN. Для реального удаления запустите команду без --dry-run"))

