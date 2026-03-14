from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("ui", "0008_uiuserpreference_company_detail_view_mode"),
    ]

    operations = [
        migrations.AddField(
            model_name="uiuserpreference",
            name="tasks_per_page",
            field=models.PositiveSmallIntegerField(
                choices=[(10, "10"), (25, "25"), (50, "50"), (100, "100")],
                default=25,
                verbose_name="Строк на странице (задачи)",
            ),
        ),
        migrations.AddField(
            model_name="uiuserpreference",
            name="default_task_tab",
            field=models.CharField(
                choices=[
                    ("all", "Все"),
                    ("mine", "Мои"),
                    ("overdue", "Просроченные"),
                    ("today", "Сегодня"),
                ],
                default="all",
                max_length=20,
                verbose_name="Вкладка задач по умолчанию",
            ),
        ),
    ]
