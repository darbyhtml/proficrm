# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("mailer", "0012_merge_20260119_1200"),
    ]

    operations = [
        migrations.AlterField(
            model_name="campaign",
            name="status",
            field=models.CharField(
                choices=[
                    ("draft", "Черновик"),
                    ("ready", "Готово к отправке"),
                    ("sending", "Отправляется"),
                    ("paused", "На паузе"),
                    ("sent", "Отправлено"),
                    ("stopped", "Остановлено"),
                ],
                default="draft",
                max_length=16,
                verbose_name="Статус",
            ),
        ),
    ]
