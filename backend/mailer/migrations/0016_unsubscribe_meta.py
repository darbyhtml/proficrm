from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("mailer", "0015_user_daily_limit_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="unsubscribe",
            name="source",
            field=models.CharField(blank=True, default="", help_text="manual/token/smtp_bz", max_length=24, verbose_name="Источник"),
        ),
        migrations.AddField(
            model_name="unsubscribe",
            name="reason",
            field=models.CharField(blank=True, default="", help_text="bounce/user/unsubscribe (если известно)", max_length=24, verbose_name="Причина"),
        ),
        migrations.AddField(
            model_name="unsubscribe",
            name="last_seen_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="Последнее обновление (из внешних источников)"),
        ),
    ]

