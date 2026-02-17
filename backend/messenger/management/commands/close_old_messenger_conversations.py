"""
Команда для политики хранения Messenger: автозакрытие старых диалогов.

Рекомендуемый запуск по cron 1 раз в день:
  python manage.py close_old_messenger_conversations

По умолчанию закрывает (status=closed) диалоги со статусом RESOLVED,
у которых last_message_at < now - MESSENGER_RETENTION_RESOLVED_TO_CLOSED_DAYS.
"""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from messenger.models import Conversation
from messenger.integrations import notify_conversation_closed


class Command(BaseCommand):
    help = "Автозакрытие (архивация) старых диалогов messenger по политике хранения."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=None,
            help="Сколько дней хранить RESOLVED до перевода в CLOSED (по умолчанию из settings).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Только показать количество диалогов, которые были бы закрыты.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=5000,
            help="Ограничение на количество диалогов за запуск (по умолчанию 5000).",
        )

    def handle(self, *args, **options):
        days = options.get("days")
        if days is None:
            days = int(getattr(settings, "MESSENGER_RETENTION_RESOLVED_TO_CLOSED_DAYS", 90))
        dry_run = bool(options.get("dry_run"))
        limit = int(options.get("limit") or 5000)

        if days <= 0:
            self.stdout.write(self.style.WARNING("days <= 0 — ничего не делаем."))
            return

        cutoff = timezone.now() - timedelta(days=days)
        from django.db.models import Q

        qs = Conversation.objects.filter(
            status=Conversation.Status.RESOLVED
        ).filter(
            Q(last_message_at__lt=cutoff) | Q(last_message_at__isnull=True, created_at__lt=cutoff)
        ).order_by("id")

        total = qs.count()
        self.stdout.write(f"Найдено RESOLVED диалогов старше {days} дн.: {total}")

        if dry_run or total == 0:
            return

        ids = list(qs.values_list("id", flat=True)[:limit])
        conversations = list(Conversation.objects.filter(id__in=ids))
        updated = Conversation.objects.filter(id__in=ids).update(status=Conversation.Status.CLOSED)
        self.stdout.write(self.style.SUCCESS(f"Закрыто (CLOSED): {updated}"))

        # Webhook: массовое закрытие диалогов по политике хранения
        for conv in conversations:
            try:
                conv.status = Conversation.Status.CLOSED
                notify_conversation_closed(conv)
            except Exception:
                # Логгер в integrations уже пишет предупреждение; здесь просто продолжаем
                continue

