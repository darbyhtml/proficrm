"""
Полная переиндексация компаний в Typesense.
Запускать после включения SEARCH_ENGINE_BACKEND=typesense или для восстановления индекса.
"""
from __future__ import annotations

import json
from uuid import UUID

from django.core.management.base import BaseCommand

from companies.models import Company
from companies.search_backends.typesense_backend import (
    build_company_document,
    ensure_collection,
    _get_client,
)
from django.conf import settings as s


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

        client = _get_client()
        if not client:
            self.stdout.write(self.style.ERROR("Typesense недоступен. Проверьте TYPESENSE_* в настройках."))
            return

        ensure_collection(client)
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
                indexed += self._import_batch(client, collection, buf)
                buf = []
                self.stdout.write("Готово: %d/%d" % (indexed, total))

        if buf:
            indexed += self._import_batch(client, collection, buf)
        self.stdout.write(self.style.SUCCESS("OK. Проиндексировано: %d" % indexed))

    def _import_batch(self, client, collection: str, docs: list) -> int:
        try:
            # API принимает NDJSON; клиент может принять список и сериализовать сам
            result = client.collections[collection].documents.import_(
                docs, {"action": "upsert"}
            )
            # Ответ — итератор строк (NDJSON) или список dict; успех = success: true
            count = 0
            for item in result:
                if isinstance(item, dict):
                    if item.get("success") is True:
                        count += 1
                    elif item.get("error"):
                        self.stdout.write(
                            self.style.WARNING("Импорт документа: %s" % item.get("error"))
                        )
                else:
                    try:
                        parsed = json.loads(item) if isinstance(item, str) else item
                        if parsed.get("success") is True:
                            count += 1
                    except Exception:
                        pass
            return count
        except TypeError:
            # Часть клиентов ожидает NDJSON-строку
            try:
                ndjson = "\n".join(json.dumps(d, ensure_ascii=False) for d in docs)
                result = client.collections[collection].documents.import_(
                    ndjson, {"action": "upsert"}
                )
                return sum(
                    1 for r in result
                    if isinstance(r, dict) and r.get("success") is True
                )
            except Exception as e:
                self.stdout.write(self.style.WARNING("Ошибка импорта пачки (NDJSON): %s" % e))
                return 0
        except Exception as e:
            self.stdout.write(self.style.WARNING("Ошибка импорта пачки: %s" % e))
            return 0
