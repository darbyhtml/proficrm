"""
Синхронизирует синонимы Typesense (сокращения адресов: ул→улица, пр→проспект и т.д.) для поиска компаний.
Запускать после index_companies_typesense или при смене списка синонимов.
"""
from __future__ import annotations

from django.conf import settings as s
from django.core.management.base import BaseCommand

from companies.search_backends.typesense_backend import (
    ensure_collection,
    ensure_synonyms,
    _get_client,
)


class Command(BaseCommand):
    help = "Синхронизировать синонимы Typesense для коллекции компаний (ул→улица, пр→проспект и т.д.)."

    def handle(self, *args, **options):
        backend = (getattr(s, "SEARCH_ENGINE_BACKEND", "postgres") or "postgres").strip().lower()
        if backend != "typesense":
            self.stdout.write(self.style.WARNING("SEARCH_ENGINE_BACKEND не typesense — команда пропущена."))
            return

        client = _get_client()
        if not client:
            self.stdout.write(self.style.ERROR("Typesense недоступен. Проверьте TYPESENSE_* в настройках."))
            return

        ensure_collection(client)
        count = ensure_synonyms(client)
        self.stdout.write(
            self.style.SUCCESS("Синонимы обновлены: загружено %d групп (ул/пр-т/наб/пер/ш и т.д.)." % count)
        )
