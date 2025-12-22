from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("companies", "0010_company_contract_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="company",
            name="activity_kind",
            field=models.CharField(blank=True, db_index=True, default="", max_length=255, verbose_name="Вид деятельности"),
        ),
    ]


