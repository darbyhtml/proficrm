"""
Data migration: создаёт запись GlobalMailAccount (id=1) если она отсутствует.

Зачем: GlobalMailAccount — singleton, загружается через .load() который делает
get_or_create(id=1). Без явного создания при migrate запись отсутствует до первого
обращения — Celery Beat может запустить send_pending_emails до инициализации,
что приводит к silent failure (is_enabled=False + пустой пароль).

После migration: запись существует с smtp.bz дефолтами, is_enabled=False.
Администратор заходит в /admin/mailer/globalmailaccount/ и:
  1. Вводит smtp_username и пароль
  2. Ставит is_enabled=True
"""
from django.db import migrations


def seed_global_mail_account(apps, schema_editor):
    GlobalMailAccount = apps.get_model("mailer", "GlobalMailAccount")
    db_alias = schema_editor.connection.alias
    GlobalMailAccount.objects.using(db_alias).get_or_create(
        id=1,
        defaults={
            "smtp_host": "connect.smtp.bz",
            "smtp_port": 587,
            "use_starttls": True,
            "smtp_username": "",
            "smtp_password_enc": "",
            "from_email": "",
            "from_name": "CRM ПРОФИ",
            "is_enabled": False,
            "rate_per_minute": 1,
            "rate_per_day": 15000,
            "per_user_daily_limit": 100,
            "smtp_bz_api_key_enc": "",
        },
    )


def noop(apps, schema_editor):
    # Откат не удаляет запись — данные могут быть введены администратором
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("mailer", "0030_alter_campaignrecipient_last_error_and_more"),
    ]

    operations = [
        migrations.RunPython(seed_global_mail_account, noop),
    ]
