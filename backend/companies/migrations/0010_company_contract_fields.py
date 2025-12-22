from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("companies", "0009_company_head_company"),
    ]

    operations = [
        migrations.AddField(
            model_name="company",
            name="contract_type",
            field=models.CharField(
                blank=True,
                choices=[
                    ("frame", "Рамочный"),
                    ("tender", "Тендер"),
                    ("legal", "Юр. лицо"),
                    ("individual", "Физ. лицо"),
                ],
                db_index=True,
                default="",
                max_length=16,
                verbose_name="Вид договора",
            ),
        ),
        migrations.AddField(
            model_name="company",
            name="contract_until",
            field=models.DateField(blank=True, db_index=True, null=True, verbose_name="Действует до"),
        ),
    ]


