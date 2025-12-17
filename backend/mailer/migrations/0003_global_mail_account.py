from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("mailer", "0002_unsubscribetoken_mailaccount_rate_per_day_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="GlobalMailAccount",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("smtp_host", models.CharField(default="smtp.yandex.ru", max_length=255, verbose_name="SMTP host")),
                ("smtp_port", models.PositiveIntegerField(default=587, verbose_name="SMTP port")),
                ("use_starttls", models.BooleanField(default=True, verbose_name="STARTTLS")),
                ("smtp_username", models.CharField(default="", max_length=255, verbose_name="Логин SMTP")),
                ("smtp_password_enc", models.TextField(blank=True, default="", verbose_name="Пароль (зашифрован)")),
                ("from_name", models.CharField(blank=True, default="CRM ПРОФИ", max_length=120, verbose_name="Имя отправителя (по умолчанию)")),
                ("is_enabled", models.BooleanField(default=False, verbose_name="Включено")),
                ("rate_per_minute", models.PositiveIntegerField(default=20, verbose_name="Лимит писем в минуту")),
                ("rate_per_day", models.PositiveIntegerField(default=500, verbose_name="Лимит писем в день")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Обновлено")),
            ],
        ),
    ]


