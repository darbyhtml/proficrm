from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("companies", "0011_company_activity_kind"),
    ]

    operations = [
        migrations.AddField(
            model_name="company",
            name="is_cold_call",
            field=models.BooleanField(db_index=True, default=False, verbose_name="Холодный звонок"),
        ),
    ]


