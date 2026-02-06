"""
Пересчёт поля Company.updated_at из журнала ActivityEvent.

Задача: сделать так, чтобы колонка «Обновлено» в списке компаний отражала время
последней активности по карточке (заметки, контакты, звонки, задачи и т.п.).

После изменения audit.service.log_event новое поведение применяется ко всем
будущим событиям. Эта команда позволяет один раз «подтянуть» исторические данные.

Использование:
    python manage.py rebuild_company_updated_from_activity
    python manage.py rebuild_company_updated_from_activity --dry-run
"""

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Max

from audit.models import ActivityEvent
from companies.models import Company


class Command(BaseCommand):
    help = (
        "Пересчитывает Company.updated_at по последней активности из ActivityEvent "
        "(для всех компаний, у которых есть события с company_id)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Показать, сколько компаний будет обновлено, без фактической записи в БД",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=1000,
            help="Размер батча для bulk_update (по умолчанию: 1000)",
        )

    def handle(self, *args, **options):
        dry_run: bool = options["dry_run"]
        batch_size: int = options["batch_size"]

        self.stdout.write(self.style.SUCCESS("\nПересчёт Company.updated_at из ActivityEvent\n"))
        if dry_run:
            self.stdout.write(self.style.WARNING("Режим DRY-RUN: данные НЕ будут записаны в БД.\n"))

        # Агрегируем по company_id последнюю активность
        events_qs = (
            ActivityEvent.objects.exclude(company_id__isnull=True)
            .values("company_id")
            .annotate(last_event=Max("created_at"))
        )

        total_companies_with_events = events_qs.count()
        if total_companies_with_events == 0:
            self.stdout.write("Не найдено ни одной компании с ActivityEvent.company_id.\n")
            return

        self.stdout.write(f"Компаний с событиями в ActivityEvent: {total_companies_with_events}\n")

        # Загружаем все затронутые компании одним запросом
        company_ids = [row["company_id"] for row in events_qs]
        companies_by_id = Company.objects.in_bulk(company_ids)

        to_update: list[Company] = []
        updated_count = 0

        for row in events_qs:
            company_id = row["company_id"]
            last_event = row["last_event"]
            company = companies_by_id.get(company_id)
            if not company or not last_event:
                continue

            # Обновляем только если последняя активность новее текущего updated_at
            if not company.updated_at or last_event > company.updated_at:
                company.updated_at = last_event
                to_update.append(company)

                # Покажем несколько примеров в начале
                if updated_count < 10:
                    self.stdout.write(
                        f"  Компания {company_id}: updated_at будет установлено в {last_event} "
                        f"(было {company.updated_at})"
                    )

                updated_count += 1

                # Пакетное сохранение
                if len(to_update) >= batch_size:
                    if not dry_run:
                        with transaction.atomic():
                            Company.objects.bulk_update(to_update, ["updated_at"], batch_size=batch_size)
                    to_update.clear()

        # Хвост батча
        if to_update and not dry_run:
            with transaction.atomic():
                Company.objects.bulk_update(to_update, ["updated_at"], batch_size=batch_size)

        self.stdout.write(
            self.style.SUCCESS(
                f"\nГотово. Компаний, для которых updated_at новее по ActivityEvent: {updated_count}"
            )
        )
        if dry_run:
            self.stdout.write(self.style.WARNING("Это был DRY-RUN. Для применения изменений запустите без --dry-run.\n"))

