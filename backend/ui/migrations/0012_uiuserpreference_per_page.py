from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("ui", "0011_uiuserpreference_font_scale_widen"),
    ]

    operations = [
        migrations.AlterField(
            model_name="uiuserpreference",
            name="tasks_per_page",
            field=models.PositiveSmallIntegerField(
                choices=[(25, "25"), (50, "50"), (100, "100"), (200, "200")],
                default=25,
                verbose_name="Строк на странице (задачи)",
            ),
        ),
        migrations.AddField(
            model_name="uiuserpreference",
            name="companies_per_page",
            field=models.PositiveSmallIntegerField(
                choices=[(25, "25"), (50, "50"), (100, "100"), (200, "200")],
                default=25,
                verbose_name="Строк на странице (компании)",
            ),
        ),
    ]
