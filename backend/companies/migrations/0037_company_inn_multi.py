from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("companies", "0036_rename_search_indexes_short_names"),
    ]

    operations = [
        migrations.AlterField(
            model_name="company",
            name="inn",
            field=models.CharField("ИНН", max_length=255, blank=True, default="", db_index=True),
        ),
    ]

