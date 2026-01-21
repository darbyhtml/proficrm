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
        migrations.SeparateDatabaseAndState(
            database_operations=[
                # На некоторых окружениях поле/таблица могли быть удалены ранее.
                migrations.RunSQL(
                    sql="ALTER TABLE IF EXISTS companies_company DROP COLUMN IF EXISTS lead_state;",
                    reverse_sql=migrations.RunSQL.noop,
                ),
                migrations.RunSQL(
                    sql="DROP TABLE IF EXISTS companies_companyleadstaterequest;",
                    reverse_sql=migrations.RunSQL.noop,
                ),
            ],
            state_operations=[
                migrations.RemoveField(
                    model_name="company",
                    name="lead_state",
                ),
                migrations.DeleteModel(
                    name="CompanyLeadStateRequest",
                ),
            ],
        ),
    ]

