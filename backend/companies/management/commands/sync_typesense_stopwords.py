"""
Синхронизирует стоп-слова Typesense (орг. формы ООО, ИП и т.д.) для поиска компаний.
Запускать после index_companies_typesense или при смене списка стоп-слов.
"""
from __future__ import annotations

from django.conf import settings as s
from django.core.management.base import BaseCommand

from companies.search_backends.typesense_backend import (
    STOPWORDS_SET_ID,
    RUSSIAN_ORG_STOPWORDS,
    ensure_stopwords,
    _get_client,
)


class Command(BaseCommand):
    help = "Синхронизировать стоп-слова Typesense для коллекции компаний (ru_org)."

    def handle(self, *args, **options):
        backend = (getattr(s, "SEARCH_ENGINE_BACKEND", "postgres") or "postgres").strip().lower()
        if backend != "typesense":
            self.stdout.write(self.style.WARNING("SEARCH_ENGINE_BACKEND не typesense — команда пропущена."))
            return

        client = _get_client()
        if not client:
            self.stdout.write(self.style.ERROR("Typesense недоступен. Проверьте TYPESENSE_* в настройках."))
            return

        ensure_stopwords(client)
        self.stdout.write(
            self.style.SUCCESS("Стоп-слова обновлены: %s (%d слов)" % (STOPWORDS_SET_ID, len(RUSSIAN_ORG_STOPWORDS)))
        )
