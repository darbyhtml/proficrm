"""
Очистка orphan-контактов (без привязки к Company).

На проде по состоянию 2026-04-20 — 343 таких контакта:
- 45 полностью пустых (нет ни phone, ни email) — гарантированный мусор
- 298 с данными, но без активности в ActivityEvent за 180 дней
- 86% создано в массовом импорте из AmoCRM (январь 2026)

Режимы:
- dry-run      — только посчитать и разбить по категориям
- export-csv   — выгрузить все orphan-контакты в CSV
- delete-empty — удалить 45 пустых (без phone/email) — безопасно
- delete-all   — удалить ВСЕ orphan-контакты (требует --confirm)

См. docs/runbooks/30-orphan-contacts-cleanup.md для полного процесса.
"""
from __future__ import annotations

import csv
import sys

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from companies.models import Contact, ContactEmail, ContactPhone


class Command(BaseCommand):
    help = "Управление orphan-контактами (без company_id)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--mode",
            choices=["dry-run", "export-csv", "delete-empty", "delete-all"],
            required=True,
            help=(
                "dry-run: посчитать и разбить по категориям; "
                "export-csv: выгрузить в stdout CSV (одна строка на контакт); "
                "delete-empty: удалить только без phone/email (безопасно); "
                "delete-all: удалить всех orphan-контактов (требует --confirm)"
            ),
        )
        parser.add_argument(
            "--confirm",
            action="store_true",
            help="Обязательно для delete-empty / delete-all",
        )

    def handle(self, *args, mode, confirm, **kw):
        orphans = Contact.objects.filter(company_id__isnull=True)
        total = orphans.count()
        self.stdout.write(f"Всего orphan-контактов (company_id IS NULL): {total}")

        if total == 0:
            self.stdout.write(self.style.SUCCESS("Нечего делать."))
            return

        if mode == "dry-run":
            self._dry_run(orphans)
            return

        if mode == "export-csv":
            self._export_csv(orphans)
            return

        if not confirm:
            raise CommandError(
                "Для режимов delete-empty и delete-all требуется --confirm. "
                "Сначала запустите dry-run или export-csv."
            )

        if mode == "delete-empty":
            self._delete_empty(orphans)
            return

        if mode == "delete-all":
            self._delete_all(orphans)
            return

    def _dry_run(self, orphans):
        with_phone = 0
        with_email = 0
        totally_empty = 0
        for c in orphans.only("id"):
            has_phone = ContactPhone.objects.filter(contact_id=c.id).exists()
            has_email = ContactEmail.objects.filter(contact_id=c.id).exists()
            if has_phone:
                with_phone += 1
            if has_email:
                with_email += 1
            if not has_phone and not has_email:
                totally_empty += 1
        self.stdout.write(f"  с телефоном: {with_phone}")
        self.stdout.write(f"  с email:     {with_email}")
        self.stdout.write(self.style.WARNING(f"  полностью пустых (кандидаты на удаление): {totally_empty}"))

    def _export_csv(self, orphans):
        writer = csv.writer(sys.stdout)
        writer.writerow(
            ["id", "first_name", "last_name", "position", "phones", "emails", "created_at"]
        )
        for c in orphans.order_by("-created_at"):
            phones = ", ".join(
                ContactPhone.objects.filter(contact_id=c.id).values_list("value", flat=True)
            )
            emails = ", ".join(
                ContactEmail.objects.filter(contact_id=c.id).values_list("value", flat=True)
            )
            writer.writerow(
                [
                    str(c.id),
                    c.first_name,
                    c.last_name,
                    getattr(c, "position", ""),
                    phones,
                    emails,
                    c.created_at.isoformat() if c.created_at else "",
                ]
            )

    def _delete_empty(self, orphans):
        empty_ids = []
        for c in orphans.only("id"):
            if not ContactPhone.objects.filter(contact_id=c.id).exists() and not ContactEmail.objects.filter(contact_id=c.id).exists():
                empty_ids.append(c.id)
        if not empty_ids:
            self.stdout.write("Пустых orphan-контактов не найдено.")
            return
        with transaction.atomic():
            deleted, details = Contact.objects.filter(id__in=empty_ids).delete()
        self.stdout.write(self.style.SUCCESS(f"Удалено пустых orphan-контактов: {deleted}"))
        self.stdout.write(f"Подробности: {details}")

    def _delete_all(self, orphans):
        with transaction.atomic():
            deleted, details = orphans.delete()
        self.stdout.write(self.style.SUCCESS(f"Удалено orphan-контактов: {deleted}"))
        self.stdout.write(f"Подробности: {details}")
