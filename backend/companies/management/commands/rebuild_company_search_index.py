from __future__ import annotations

from uuid import UUID

from django.core.management.base import BaseCommand
from django.db import connection, transaction

from companies.models import Company, CompanySearchIndex
from companies.search_index import build_company_index_payload


class Command(BaseCommand):
    help = "Перестроить CompanySearchIndex (полный поиск компаний)."

    def add_arguments(self, parser):
        parser.add_argument("--company-id", type=str, default="", help="UUID компании (перестроить только одну).")
        parser.add_argument("--chunk", type=int, default=200, help="Размер чанка (по умолчанию 200).")

    def handle(self, *args, **options):
        if connection.vendor != "postgresql":
            self.stdout.write(self.style.WARNING("Не PostgreSQL — CompanySearchIndex пропускаем."))
            return

        company_id_raw = (options.get("company_id") or "").strip()
        chunk = int(options.get("chunk") or 200)
        if chunk <= 0:
            chunk = 200

        qs = Company.objects.all().order_by("id")
        if company_id_raw:
            try:
                cid = UUID(company_id_raw)
            except Exception:
                raise SystemExit("Некорректный --company-id (ожидается UUID).")
            qs = qs.filter(id=cid)

        total = qs.order_by().count()
        self.stdout.write(f"Компаний к индексации: {total}")

        idx = 0
        ids_buffer: list[UUID] = []
        for company_id in qs.values_list("id", flat=True).iterator(chunk_size=2000):
            ids_buffer.append(company_id)
            if len(ids_buffer) >= chunk:
                idx += self._process_chunk(ids_buffer)
                ids_buffer = []
                self.stdout.write(f"Готово: {idx}/{total}")

        if ids_buffer:
            idx += self._process_chunk(ids_buffer)
            self.stdout.write(f"Готово: {idx}/{total}")

        self.stdout.write(self.style.SUCCESS("OK"))

    def _process_chunk(self, ids: list[UUID]) -> int:
        companies = (
            Company.objects.filter(id__in=ids)
            .prefetch_related(
                "phones",
                "emails",
                "contacts__phones",
                "contacts__emails",
                "notes",
                "tasks",
            )
        )
        companies_by_id = {c.id: c for c in companies}

        with transaction.atomic():
            for cid in ids:
                c = companies_by_id.get(cid)
                if not c:
                    CompanySearchIndex.objects.filter(company_id=cid).delete()
                    continue
                payload = build_company_index_payload(c)
                obj, _ = CompanySearchIndex.objects.get_or_create(company=c)
                obj.t_ident = payload["t_ident"]
                obj.t_name = payload["t_name"]
                obj.t_contacts = payload["t_contacts"]
                obj.t_other = payload["t_other"]
                obj.plain_text = payload["plain_text"]
                obj.digits = payload["digits"]
                obj.save(update_fields=["t_ident", "t_name", "t_contacts", "t_other", "plain_text", "digits"])

        return len(ids)

