from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("companies", "0040_company_search_index"),
    ]

    operations = [
        migrations.AddField(
            model_name="contracttype",
            name="is_annual",
            field=models.BooleanField(
                default=False,
                help_text="Если отмечено, договор действует на определенную сумму, а не до даты. Для годовых договоров поле 'Действует до' не отображается.",
                verbose_name="Годовой договор",
            ),
        ),
        migrations.AddField(
            model_name="company",
            name="contract_amount",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text="Сумма договора (только для годовых договоров)",
                max_digits=12,
                null=True,
                verbose_name="Сумма договора",
            ),
        ),
    ]
