# Generated manually

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("companies", "0027_companyemail"),
    ]

    operations = [
        migrations.CreateModel(
            name="CompanyPhone",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("value", models.CharField("Телефон", max_length=50, db_index=True)),
                ("order", models.IntegerField("Порядок", default=0, db_index=True)),
                (
                    "company",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="phones",
                        to="companies.company",
                        verbose_name="Компания",
                    ),
                ),
            ],
            options={
                "ordering": ["order", "value"],
            },
        ),
        migrations.AddIndex(
            model_name="companyphone",
            index=models.Index(fields=["value"], name="companies_c_phone_value_idx"),
        ),
        migrations.AddIndex(
            model_name="companyphone",
            index=models.Index(fields=["company", "order"], name="companies_c_phone_company_order_idx"),
        ),
    ]

