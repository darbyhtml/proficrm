"""
Историческая команда для индексации компаний в Typesense.

Typesense полностью отключён; поиск компаний использует только PostgreSQL FTS
через CompanySearchService / CompanySearchIndex.

Команда оставлена как no-op, чтобы не ломать старые скрипты деплоя.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "НЕ ИСПОЛЬЗУЕТСЯ: Typesense отключён, поиск компаний реализован через PostgreSQL."

    def add_arguments(self, parser):
        # Аргументы оставлены для обратной совместимости CLI, но не влияют на поведение.
        parser.add_argument("--chunk", type=int, default=200, help="(устарело) Размер пачки для Typesense.")
        parser.add_argument("--company-id", type=str, default="", help="(устарело) UUID одной компании.")

    def handle(self, *args, **options):
        self.stdout.write(
            self.style.WARNING(
                "index_companies_typesense: Typesense полностью отключён, команда больше не выполняет действий. "
                "Поиск компаний использует только PostgreSQL FTS (CompanySearchIndex)."
            )
        )
