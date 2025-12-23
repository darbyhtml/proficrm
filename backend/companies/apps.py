from django.apps import AppConfig


class CompaniesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = 'companies'
    verbose_name = "Компании и контакты"

    def ready(self):
        # noqa: F401
        from . import signals  # type: ignore
