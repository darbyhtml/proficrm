from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tasksapp", "0004_default_task_types"),
    ]

    operations = [
        migrations.AddField(
            model_name="tasktype",
            name="icon",
            field=models.CharField(
                verbose_name="Иконка",
                max_length=32,
                blank=True,
                default="",
                help_text="Логический код иконки (phone, mail, alert и т.п.)",
            ),
        ),
        migrations.AddField(
            model_name="tasktype",
            name="color",
            field=models.CharField(
                verbose_name="Цвет",
                max_length=32,
                blank=True,
                default="",
                help_text="CSS-класс/токен цвета бейджа (например, badge-blue, badge-green)",
            ),
        ),
    ]

