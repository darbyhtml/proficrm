"""
Management command для переключения режима Policy.

Использование:
  python manage.py set_policy_mode --mode observe_only
  python manage.py set_policy_mode --mode enforce

Это безопасный способ переключить PolicyConfig.mode без Django admin UI.
Удобно для deployment-скриптов и миграций данных.
"""

from django.core.management.base import BaseCommand, CommandError

from policy.models import PolicyConfig


class Command(BaseCommand):
    help = "Переключает режим PolicyConfig: observe_only (только логировать) или enforce (блокировать)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--mode",
            choices=[PolicyConfig.Mode.OBSERVE_ONLY, PolicyConfig.Mode.ENFORCE],
            required=True,
            help="Режим: observe_only или enforce",
        )

    def handle(self, *args, **options):
        mode = options["mode"]
        cfg = PolicyConfig.load()
        old_mode = cfg.mode

        if old_mode == mode:
            self.stdout.write(
                self.style.WARNING(f"PolicyConfig уже в режиме '{mode}'. Без изменений.")
            )
            return

        cfg.mode = mode
        cfg.save(update_fields=["mode", "updated_at"])

        self.stdout.write(self.style.SUCCESS(f"PolicyConfig: '{old_mode}' → '{mode}'"))
        if mode == PolicyConfig.Mode.ENFORCE:
            self.stdout.write(
                self.style.WARNING(
                    "⚠️  ENFORCE активен: запрещённые действия теперь возвращают HTTP 403."
                )
            )
