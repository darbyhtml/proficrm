from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("companies", "0031_company_worktime_and_employees"),
    ]

    operations = [
        migrations.AddField(
            model_name="company",
            name="work_schedule",
            field=models.TextField(
                blank=True,
                default="",
                help_text="Можно копировать с сайта, вводить вручную. Время автоматически форматируется в формат HH:MM.",
                verbose_name="Режим работы",
            ),
        ),
    ]
