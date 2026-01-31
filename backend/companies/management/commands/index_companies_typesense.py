"""
Полная переиндексация компаний в Typesense.
Запускать после включения SEARCH_ENGINE_BACKEND=typesense или для восстановления индекса.
"""
from __future__ import annotations

from uuid import UUID

from django.conf import settings as s
from django.core.management.base import BaseCommand

from companies.models import Company
from companies.search_backends.typesense_backend import (
    build_company_document,
    ensure_collection,
    _typesense_available,
    _typesense_import_documents,
)


class Command(BaseCommand):
    help = "Переиндексировать все компании в Typesense (полная загрузка)."

    def add_arguments(self, parser):
        parser.add_argument("--chunk", type=int, default=200, help="Размер пачки (по умолчанию 200).")
        parser.add_argument("--company-id", type=str, default="", help="UUID одной компании (индексировать только её).")

    def handle(self, *args, **options):
        backend = (getattr(s, "SEARCH_ENGINE_BACKEND", "postgres") or "postgres").strip().lower()
        if backend != "typesense":
            self.stdout.write(self.style.WARNING("SEARCH_ENGINE_BACKEND не typesense — индексация пропущена."))
            return

        if not _typesense_available():
            self.stdout.write(self.style.ERROR("Typesense недоступен. Проверьте TYPESENSE_* в настройках."))
            return

        ensure_collection()
        collection = getattr(s, "TYPESENSE_COLLECTION_COMPANIES", "companies")
        chunk_size = max(1, int(options.get("chunk") or 200))
        company_id_raw = (options.get("company_id") or "").strip()

        qs = Company.objects.all().order_by("id").prefetch_related(
            "phones", "emails",
            "contacts__phones", "contacts__emails",
            "notes", "tasks",
        )
        if company_id_raw:
            try:
                cid = UUID(company_id_raw)
                qs = qs.filter(id=cid)
            except ValueError:
                self.stdout.write(self.style.ERROR("Некорректный --company-id (ожидается UUID)."))
                return

        total = qs.count()
        if total == 0:
            self.stdout.write("Нет компаний для индексации.")
            return

        self.stdout.write(f"Компаний к индексации: {total}")

        indexed = 0
        buf = []
        for company in qs.iterator(chunk_size=1000):
            try:
                doc = build_company_document(company)
                buf.append(doc)
            except Exception as e:
                self.stdout.write(self.style.WARNING("Ошибка сборки документа %s: %s" % (company.id, e)))
                continue
            if len(buf) >= chunk_size:
                count, errors = _typesense_import_documents(collection, buf)
                indexed += count
                for err in errors:
                    self.stdout.write(self.style.WARNING("Импорт документа: %s" % err))
                buf = []
                self.stdout.write("Готово: %d/%d" % (indexed, total))

        if buf:
            count, errors = _typesense_import_documents(collection, buf)
            indexed += count
            for err in errors:
                self.stdout.write(self.style.WARNING("Импорт документа: %s" % err))
        self.stdout.write(self.style.SUCCESS("OK. Проиндексировано: %d" % indexed))
