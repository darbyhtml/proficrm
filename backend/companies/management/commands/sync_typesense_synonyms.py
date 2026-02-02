"""
Историческая команда для синхронизации синонимов Typesense.

Typesense полностью отключён; поиск компаний использует только PostgreSQL.
Команда оставлена как no-op для обратной совместимости.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "НЕ ИСПОЛЬЗУЕТСЯ: Typesense отключён, синонимы более не синхронизируются."

    def handle(self, *args, **options):
        self.stdout.write(
            self.style.WARNING(
                "sync_typesense_synonyms: Typesense полностью отключён, команда больше не выполняет действий."
            )
        )
