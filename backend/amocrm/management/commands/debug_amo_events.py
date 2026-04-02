"""
Management command для отладки структуры событий из AmoCRM Events API.
Показывает raw JSON первых N событий типа entity_responsible_changed для
указанной компании (по amocrm_company_id).

    python manage.py debug_amo_events --company-id <amo_id> [--limit 3]
"""
import json

from django.core.management.base import BaseCommand, CommandError

from amocrm.client import AmoClient
from ui.models import AmoApiConfig


class Command(BaseCommand):
    help = "Показывает raw JSON событий amoCRM для отладки структуры value_before/value_after"

    def add_arguments(self, parser):
        parser.add_argument(
            "--company-id",
            type=int,
            required=True,
            help="amocrm_company_id компании (ID в amoCRM, не наш UUID)",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=3,
            help="Сколько событий показать (по умолчанию 3)",
        )
        parser.add_argument(
            "--event-type",
            type=str,
            default="entity_responsible_changed",
            help="Тип события (по умолчанию entity_responsible_changed)",
        )

    def handle(self, *args, **options):
        amo_id: int = options["company_id"]
        limit: int = options["limit"]
        event_type: str = options["event_type"]

        cfg = AmoApiConfig.load()
        if not cfg.domain:
            raise CommandError("AmoCRM domain не настроен.")

        try:
            client = AmoClient(cfg)
        except Exception as e:
            raise CommandError(f"Ошибка создания клиента: {e}")

        self.stdout.write(f"Запрашиваем события для company_id={amo_id}, тип={event_type}...")

        try:
            events = client.get_all_pages(
                "/api/v4/events",
                params={
                    "filter[entity_type][]": "company",
                    "filter[entity_id]": [str(amo_id)],
                    "filter[type][]": event_type,
                },
                embedded_key="events",
                limit=50,
                max_pages=1,
            )
        except Exception as e:
            raise CommandError(f"Ошибка запроса: {e}")

        if not events:
            self.stdout.write(self.style.WARNING("Событий не найдено."))
            return

        self.stdout.write(self.style.SUCCESS(f"Найдено событий: {len(events)}, показываем первые {limit}:\n"))

        for idx, ev in enumerate(events[:limit], 1):
            self.stdout.write(f"{'=' * 60}")
            self.stdout.write(f"Событие #{idx}")
            self.stdout.write(f"{'=' * 60}")
            self.stdout.write(json.dumps(ev, ensure_ascii=False, indent=2))
            self.stdout.write("")
