from django.apps import AppConfig


class MailerConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = 'mailer'
    verbose_name = "Почта и рассылки"

    def ready(self):
        from django.db.models.signals import post_migrate
        post_migrate.connect(_create_singletons, sender=self)


def _create_singletons(sender, **kwargs):
    """
    Создаёт записи-синглтоны после каждого запуска migrate.
    Гарантирует, что GlobalMailAccount(id=1) и SmtpBzQuota(id=1) существуют
    на любой новой установке без ручного вмешательства.
    """
    try:
        from mailer.models import GlobalMailAccount, SmtpBzQuota
        GlobalMailAccount.objects.get_or_create(id=1)
        SmtpBzQuota.objects.get_or_create(id=1)
    except Exception:
        # Таблицы ещё не созданы (первые миграции) — игнорируем.
        pass
