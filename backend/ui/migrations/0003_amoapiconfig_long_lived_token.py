from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("ui", "0002_amoapiconfig"),
    ]

    operations = [
        migrations.AddField(
            model_name="amoapiconfig",
            name="long_lived_token",
            field=models.TextField(blank=True, default="", verbose_name="Долгосрочный токен (если используете)"),
        ),
    ]


