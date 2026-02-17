"""
Команда для эскалации диалогов мессенджера по таймауту.

Запускать по cron каждую минуту (или каждые 2–5 минут):
  python manage.py escalate_messenger_conversations

Находит диалоги, где оператор назначен, но ещё не открыл диалог,
и с момента назначения прошло >= MESSENGER_ESCALATION_TIMEOUT_SECONDS (по умолчанию 240 = 4 мин).
Переназначает их следующему оператору филиала (round-robin с учётом нагрузки).
"""

from django.conf import settings
from django.core.management.base import BaseCommand

from messenger.services import get_conversations_eligible_for_escalation, escalate_conversation


class Command(BaseCommand):
    help = "Эскалировать диалоги мессенджера: переназначить тем, где оператор не открыл диалог в течение N минут."

    def add_arguments(self, parser):
        parser.add_argument(
            "--timeout",
            type=int,
            default=None,
            help="Таймаут в секундах (по умолчанию — из MESSENGER_ESCALATION_TIMEOUT_SECONDS).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Только показать диалоги, которые были бы эскалированы, без переназначения.",
        )

    def handle(self, *args, **options):
        timeout = options.get("timeout")
        if timeout is None:
            timeout = getattr(settings, "MESSENGER_ESCALATION_TIMEOUT_SECONDS", 240)
        dry_run = options.get("dry_run", False)

        qs = get_conversations_eligible_for_escalation(timeout_seconds=timeout)
        conversations = list(qs.select_related("assignee", "branch", "inbox")[:500])

        if not conversations:
            self.stdout.write("Нет диалогов для эскалации.")
            return

        self.stdout.write(f"Найдено диалогов для эскалации: {len(conversations)} (таймаут {timeout} с)")

        if dry_run:
            for c in conversations:
                self.stdout.write(
                    f"  [dry-run] conversation_id={c.id} assignee={c.assignee_id} branch={c.branch_id}"
                )
            return

        escalated = 0
        for conversation in conversations:
            old_assignee_id = conversation.assignee_id
            new_assignee = escalate_conversation(conversation)
            if new_assignee:
                escalated += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  conversation_id={conversation.id} переназначен с {old_assignee_id} на {new_assignee.id} ({new_assignee})"
                    )
                )
            else:
                self.stdout.write(
                    self.style.WARNING(
                        f"  conversation_id={conversation.id} — кандидатов для переназначения нет, оставлен текущему оператору"
                    )
                )

        self.stdout.write(self.style.SUCCESS(f"Эскалировано диалогов: {escalated}"))
