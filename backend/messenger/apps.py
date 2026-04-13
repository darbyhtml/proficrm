from django.apps import AppConfig


class MessengerConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "messenger"
    verbose_name = "Мессенджер"

    def ready(self):
        # Подключаем сигналы (автоназначение диалога и т.п.).
        from messenger import signals  # noqa: F401
