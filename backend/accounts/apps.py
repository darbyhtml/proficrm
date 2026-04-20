from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "accounts"
    verbose_name = "Пользователи и подразделения"

    def ready(self) -> None:
        # Подключаем post_save signal для синхронизации is_staff с role.
        # Импорт внутри ready(), чтобы избежать циклических импортов.
        from . import signals
