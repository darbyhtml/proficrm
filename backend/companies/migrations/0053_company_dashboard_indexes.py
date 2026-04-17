"""Композитные индексы для дашборда.

1. (responsible, updated_at) — для dashboard_poll EXISTS:
   каждые 30 секунд для каждого пользователя.
2. (responsible, contract_until) — для блока «договоры» с фильтром
   `responsible=user AND contract_until BETWEEN today AND today+30`.

Оба индекса существенно ускоряют горячие пути дашборда при росте
количества компаний у одного менеджера (> 500).
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("companies", "0052_task_indexes_and_constraints"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="company",
            index=models.Index(
                fields=["responsible", "updated_at"],
                name="cmp_resp_updated_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="company",
            index=models.Index(
                fields=["responsible", "contract_until"],
                name="cmp_resp_contract_until_idx",
            ),
        ),
    ]
