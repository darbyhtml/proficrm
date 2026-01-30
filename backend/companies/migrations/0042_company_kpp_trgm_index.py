# Добавляет GIN триграммный индекс по КПП для быстрого поиска по части номера (см. лучшие практики поиска)
from django.contrib.postgres.indexes import GinIndex, OpClass
from django.db import migrations
from django.db.models.functions import Upper


class Migration(migrations.Migration):

    dependencies = [
        ("companies", "0041_add_annual_contract_fields"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="company",
            index=GinIndex(
                OpClass(Upper("kpp"), name="gin_trgm_ops"),
                name="cmp_kpp_trgm_gin_idx",
            ),
        ),
    ]
