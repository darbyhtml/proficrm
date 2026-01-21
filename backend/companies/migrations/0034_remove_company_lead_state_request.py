from django.db import migrations


class Migration(migrations.Migration):
    """
    Удаляем устаревшую механику lead_state и таблицу заявок на смену состояния.

    Сейчас холодный звонок и отметки ведутся на уровне телефонов/контактов, а не на уровне компании.
    """

    dependencies = [
        ("companies", "0033_add_search_indexes"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="company",
            name="lead_state",
        ),
        migrations.DeleteModel(
            name="CompanyLeadStateRequest",
        ),
    ]

