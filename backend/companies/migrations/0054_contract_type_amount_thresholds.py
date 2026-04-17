"""ContractType: настраиваемые пороги суммы для годовых договоров.

Ранее 25 000 / 70 000 были захардкожены в шаблонах (`company_detail.html`)
и в `companies/services._get_annual_contract_alert`. Теперь — поля на типе
договора, настраиваются через админку.

Дефолты сохраняют текущее production-поведение.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("companies", "0053_company_dashboard_indexes"),
    ]

    operations = [
        migrations.AddField(
            model_name="contracttype",
            name="amount_danger_threshold",
            field=models.DecimalField(
                decimal_places=2,
                max_digits=12,
                null=True,
                blank=True,
                default=25000,
                help_text=(
                    "Если сумма годового договора МЕНЬШЕ этой — показываем "
                    "красный алерт. Только для годовых."
                ),
                verbose_name="Красный порог суммы (₽)",
            ),
        ),
        migrations.AddField(
            model_name="contracttype",
            name="amount_warn_threshold",
            field=models.DecimalField(
                decimal_places=2,
                max_digits=12,
                null=True,
                blank=True,
                default=70000,
                help_text=(
                    "Если сумма МЕНЬШЕ этой (но БОЛЬШЕ красного порога) — "
                    "показываем жёлтый алерт. Только для годовых."
                ),
                verbose_name="Жёлтый порог суммы (₽)",
            ),
        ),
    ]
