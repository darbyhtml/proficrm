from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("ui", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="AmoApiConfig",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("domain", models.CharField(blank=True, default="", max_length=255, verbose_name="Домен amoCRM")),
                ("client_id", models.CharField(blank=True, default="", max_length=255, verbose_name="OAuth Client ID")),
                ("client_secret", models.CharField(blank=True, default="", max_length=255, verbose_name="OAuth Client Secret")),
                ("redirect_uri", models.CharField(blank=True, default="", max_length=500, verbose_name="Redirect URI")),
                ("access_token", models.TextField(blank=True, default="", verbose_name="Access token")),
                ("refresh_token", models.TextField(blank=True, default="", verbose_name="Refresh token")),
                ("token_type", models.CharField(blank=True, default="Bearer", max_length=32, verbose_name="Token type")),
                ("expires_at", models.DateTimeField(blank=True, null=True, verbose_name="Token expires at")),
                ("last_error", models.TextField(blank=True, default="", verbose_name="Последняя ошибка")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Обновлено")),
            ],
            options={
                "verbose_name": "Интеграция amoCRM",
                "verbose_name_plural": "Интеграция amoCRM",
            },
        ),
    ]


