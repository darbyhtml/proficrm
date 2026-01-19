from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("companies", "0030_add_phone_comments"),
    ]

    operations = [
        migrations.AddField(
            model_name="company",
            name="employees_count",
            field=models.PositiveIntegerField(blank=True, null=True, verbose_name="Численность сотрудников"),
        ),
        migrations.AddField(
            model_name="company",
            name="workday_start",
            field=models.TimeField(blank=True, null=True, verbose_name="Рабочее время: с"),
        ),
        migrations.AddField(
            model_name="company",
            name="workday_end",
            field=models.TimeField(blank=True, null=True, verbose_name="Рабочее время: до"),
        ),
        migrations.AddField(
            model_name="company",
            name="work_timezone",
            field=models.CharField(blank=True, default="", max_length=64, verbose_name="Часовой пояс"),
        ),
    ]

