from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("companies", "0020_clear_cold_flags_for_warm_companies"),
    ]

    operations = [
        migrations.AddField(
            model_name="companynote",
            name="external_source",
            field=models.CharField(blank=True, db_index=True, default="", max_length=32, verbose_name="Внешний источник"),
        ),
        migrations.AddField(
            model_name="companynote",
            name="external_uid",
            field=models.CharField(blank=True, db_index=True, default="", max_length=120, verbose_name="Внешний UID"),
        ),
    ]


