"""
Backfill: заполняет CompanyHistoryEvent из существующих ActivityEvent.

Копирует:
  - ActivityEvent(verb=CREATE, entity_type=company)  → CompanyHistoryEvent(event_type=created)
  - ActivityEvent(verb=UPDATE, message="Изменён ответственный компании")
                                                      → CompanyHistoryEvent(event_type=assigned)

Запускать один раз после деплоя, когда миграция 0049 уже применена.

    python manage.py backfill_company_history [--dry-run] [--batch-size 500]
"""
import uuid

from django.core.management.base import BaseCommand
from django.db import transaction

from audit.models import ActivityEvent
from companies.models import Company, CompanyHistoryEvent


def _try_uuid(val) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(val))
    except (ValueError, AttributeError, TypeError):
        return None


class Command(BaseCommand):
    help = "Бэкфилл CompanyHistoryEvent из существующих ActivityEvent"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=False,
            help="Только посчитать, ничего не писать в БД",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=500,
            help="Размер пачки для bulk_create (по умолчанию 500)",
        )

    def handle(self, *args, **options):
        dry_run: bool = options["dry_run"]
        batch_size: int = options["batch_size"]

        if dry_run:
            self.stdout.write(self.style.WARNING("=== DRY-RUN: данные в БД не изменяются ==="))

        # Кэшируем ID существующих компаний для быстрой проверки
        self.stdout.write("Загружаем ID всех компаний...")
        existing_company_ids: set[uuid.UUID] = set(
            Company.objects.values_list("id", flat=True)
        )
        self.stdout.write(f"  Всего компаний в БД: {len(existing_company_ids)}")

        created_total = 0
        skipped_total = 0
        missing_company = 0

        # --- 1. Событие "создание компании" ---
        self.stdout.write("\nОбрабатываем события создания компаний...")
        create_qs = (
            ActivityEvent.objects
            .filter(verb="create", entity_type="company")
            .select_related("actor")
            .order_by("created_at")
        )
        create_count = create_qs.count()
        self.stdout.write(f"  Найдено ActivityEvent (create): {create_count}")

        batch: list[CompanyHistoryEvent] = []

        for ev in create_qs.iterator(chunk_size=batch_size):
            company_uuid = _try_uuid(ev.entity_id)
            if company_uuid is None or company_uuid not in existing_company_ids:
                missing_company += 1
                continue

            # Проверяем дубликат по (company, source, event_type)
            if CompanyHistoryEvent.objects.filter(
                company_id=company_uuid,
                source=CompanyHistoryEvent.Source.LOCAL,
                event_type=CompanyHistoryEvent.EventType.CREATED,
            ).exists():
                skipped_total += 1
                continue

            batch.append(CompanyHistoryEvent(
                company_id=company_uuid,
                event_type=CompanyHistoryEvent.EventType.CREATED,
                source=CompanyHistoryEvent.Source.LOCAL,
                actor=ev.actor,
                actor_name=str(ev.actor) if ev.actor else "",
                occurred_at=ev.created_at,
            ))
            created_total += 1

            if len(batch) >= batch_size:
                if not dry_run:
                    with transaction.atomic():
                        CompanyHistoryEvent.objects.bulk_create(batch, ignore_conflicts=True)
                batch = []

        if batch and not dry_run:
            with transaction.atomic():
                CompanyHistoryEvent.objects.bulk_create(batch, ignore_conflicts=True)
        batch = []

        self.stdout.write(
            f"  Создание: будет создано {created_total}, "
            f"пропущено (дубль) {skipped_total}, "
            f"пропущено (компания удалена) {missing_company}"
        )

        # --- 2. Событие "передача ответственного" ---
        self.stdout.write("\nОбрабатываем события передачи ответственного...")
        transfer_qs = (
            ActivityEvent.objects
            .filter(verb="update", entity_type="company", message="Изменён ответственный компании")
            .select_related("actor")
            .order_by("created_at")
        )
        transfer_count = transfer_qs.count()
        self.stdout.write(f"  Найдено ActivityEvent (transfer): {transfer_count}")

        transfer_created = 0
        transfer_skipped = 0
        transfer_missing = 0

        for ev in transfer_qs.iterator(chunk_size=batch_size):
            company_uuid = _try_uuid(ev.entity_id)
            if company_uuid is None or company_uuid not in existing_company_ids:
                transfer_missing += 1
                continue

            meta = ev.meta or {}
            from_name = meta.get("from", "") or ""
            to_name = meta.get("to", "") or ""

            # Дедупликация: если уже есть событие с той же датой для этой компании/source
            if CompanyHistoryEvent.objects.filter(
                company_id=company_uuid,
                source=CompanyHistoryEvent.Source.LOCAL,
                event_type=CompanyHistoryEvent.EventType.ASSIGNED,
                occurred_at=ev.created_at,
            ).exists():
                transfer_skipped += 1
                continue

            batch.append(CompanyHistoryEvent(
                company_id=company_uuid,
                event_type=CompanyHistoryEvent.EventType.ASSIGNED,
                source=CompanyHistoryEvent.Source.LOCAL,
                actor=ev.actor,
                actor_name=str(ev.actor) if ev.actor else "",
                from_user_name=from_name[:255],
                to_user_name=to_name[:255],
                occurred_at=ev.created_at,
            ))
            transfer_created += 1

            if len(batch) >= batch_size:
                if not dry_run:
                    with transaction.atomic():
                        CompanyHistoryEvent.objects.bulk_create(batch, ignore_conflicts=True)
                batch = []

        if batch and not dry_run:
            with transaction.atomic():
                CompanyHistoryEvent.objects.bulk_create(batch, ignore_conflicts=True)

        self.stdout.write(
            f"  Передача: будет создано {transfer_created}, "
            f"пропущено (дубль) {transfer_skipped}, "
            f"пропущено (компания удалена) {transfer_missing}"
        )

        # --- 3. Синтетическое "создана" для компаний без такого события ---
        self.stdout.write("\nДобавляем синтетическое событие «Создана» для остальных компаний...")

        # Компании, у которых уже есть "created" event
        already_have_created: set[uuid.UUID] = set(
            CompanyHistoryEvent.objects.filter(
                event_type=CompanyHistoryEvent.EventType.CREATED,
            ).values_list("company_id", flat=True)
        )

        # В dry-run включаем и те, что только что добавили (они ещё не в БД)
        if dry_run:
            for ev in batch:  # batch пустой, но на случай остатка
                already_have_created.add(ev.company_id)

        companies_missing_created = (
            Company.objects
            .exclude(id__in=already_have_created)
            .only("id", "created_at")
            .order_by("created_at")
        )
        synthetic_count = 0
        batch = []

        for company in companies_missing_created.iterator(chunk_size=batch_size):
            if company.created_at is None:
                continue
            batch.append(CompanyHistoryEvent(
                company_id=company.id,
                event_type=CompanyHistoryEvent.EventType.CREATED,
                source=CompanyHistoryEvent.Source.LOCAL,
                actor=None,
                actor_name="",
                occurred_at=company.created_at,
            ))
            synthetic_count += 1

            if len(batch) >= batch_size:
                if not dry_run:
                    with transaction.atomic():
                        CompanyHistoryEvent.objects.bulk_create(batch, ignore_conflicts=True)
                batch = []

        if batch and not dry_run:
            with transaction.atomic():
                CompanyHistoryEvent.objects.bulk_create(batch, ignore_conflicts=True)

        self.stdout.write(
            f"  Синтетических «Создана»: {'будет создано' if dry_run else 'создано'} {synthetic_count}"
        )

        # --- Итог ---
        total = created_total + transfer_created + synthetic_count
        self.stdout.write("")
        if dry_run:
            self.stdout.write(
                self.style.SUCCESS(
                    f"[DRY-RUN] Итого событий для создания: {total} "
                    f"({created_total} с автором + {synthetic_count} синтетических + {transfer_created} передача)"
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Готово! Создано событий: {total} "
                    f"({created_total} с автором + {synthetic_count} синтетических + {transfer_created} передача)"
                )
            )
