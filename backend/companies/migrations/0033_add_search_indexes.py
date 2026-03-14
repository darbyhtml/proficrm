# Generated manually for search performance optimization
# Добавляет GIN индексы с триграммами для быстрого поиска по текстовым полям
# PostgreSQL-only: на SQLite операции пропускаются через RunPython с проверкой vendor.

from django.contrib.postgres.indexes import GinIndex, OpClass
from django.db import migrations
from django.db.models.functions import Upper


def _create_trgm_extensions(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")
    schema_editor.execute("CREATE EXTENSION IF NOT EXISTS btree_gin;")


def _create_trgm_indexes(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    stmts = [
        "CREATE INDEX IF NOT EXISTS company_name_upper_trgm_gin_idx ON companies_company USING gin (upper(name) gin_trgm_ops);",
        "CREATE INDEX IF NOT EXISTS company_legal_name_upper_trgm_gin_idx ON companies_company USING gin (upper(legal_name) gin_trgm_ops);",
        "CREATE INDEX IF NOT EXISTS company_address_upper_trgm_gin_idx ON companies_company USING gin (upper(address) gin_trgm_ops);",
        "CREATE INDEX IF NOT EXISTS company_inn_upper_trgm_gin_idx ON companies_company USING gin (upper(inn) gin_trgm_ops);",
        "CREATE INDEX IF NOT EXISTS company_phone_upper_trgm_gin_idx ON companies_company USING gin (upper(phone) gin_trgm_ops);",
        "CREATE INDEX IF NOT EXISTS company_email_upper_trgm_gin_idx ON companies_company USING gin (upper(email) gin_trgm_ops);",
        "CREATE INDEX IF NOT EXISTS companyphone_value_upper_trgm_gin_idx ON companies_companyphone USING gin (upper(value) gin_trgm_ops);",
        "CREATE INDEX IF NOT EXISTS companyemail_value_upper_trgm_gin_idx ON companies_companyemail USING gin (upper(value) gin_trgm_ops);",
        "CREATE INDEX IF NOT EXISTS contactphone_value_upper_trgm_gin_idx ON companies_contactphone USING gin (upper(value) gin_trgm_ops);",
        "CREATE INDEX IF NOT EXISTS contactemail_value_upper_trgm_gin_idx ON companies_contactemail USING gin (upper(value) gin_trgm_ops);",
        "CREATE INDEX IF NOT EXISTS contact_first_name_upper_trgm_gin_idx ON companies_contact USING gin (upper(first_name) gin_trgm_ops);",
        "CREATE INDEX IF NOT EXISTS contact_last_name_upper_trgm_gin_idx ON companies_contact USING gin (upper(last_name) gin_trgm_ops);",
    ]
    for stmt in stmts:
        schema_editor.execute(stmt)


def _drop_trgm_indexes(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    names = [
        "company_name_upper_trgm_gin_idx", "company_legal_name_upper_trgm_gin_idx",
        "company_address_upper_trgm_gin_idx", "company_inn_upper_trgm_gin_idx",
        "company_phone_upper_trgm_gin_idx", "company_email_upper_trgm_gin_idx",
        "companyphone_value_upper_trgm_gin_idx", "companyemail_value_upper_trgm_gin_idx",
        "contactphone_value_upper_trgm_gin_idx", "contactemail_value_upper_trgm_gin_idx",
        "contact_first_name_upper_trgm_gin_idx", "contact_last_name_upper_trgm_gin_idx",
    ]
    for name in names:
        schema_editor.execute(f"DROP INDEX IF EXISTS {name};")


class Migration(migrations.Migration):

    dependencies = [
        ("companies", "0032_company_work_schedule"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(_create_trgm_extensions, migrations.RunPython.noop),
                migrations.RunPython(_create_trgm_indexes, _drop_trgm_indexes),
            ],
            state_operations=[
                migrations.AddIndex(model_name="company", index=GinIndex(OpClass(Upper("name"), name="gin_trgm_ops"), name="company_name_upper_trgm_gin_idx")),
                migrations.AddIndex(model_name="company", index=GinIndex(OpClass(Upper("legal_name"), name="gin_trgm_ops"), name="company_legal_name_upper_trgm_gin_idx")),
                migrations.AddIndex(model_name="company", index=GinIndex(OpClass(Upper("address"), name="gin_trgm_ops"), name="company_address_upper_trgm_gin_idx")),
                migrations.AddIndex(model_name="company", index=GinIndex(OpClass(Upper("inn"), name="gin_trgm_ops"), name="company_inn_upper_trgm_gin_idx")),
                migrations.AddIndex(model_name="company", index=GinIndex(OpClass(Upper("phone"), name="gin_trgm_ops"), name="company_phone_upper_trgm_gin_idx")),
                migrations.AddIndex(model_name="company", index=GinIndex(OpClass(Upper("email"), name="gin_trgm_ops"), name="company_email_upper_trgm_gin_idx")),
                migrations.AddIndex(model_name="companyphone", index=GinIndex(OpClass(Upper("value"), name="gin_trgm_ops"), name="companyphone_value_upper_trgm_gin_idx")),
                migrations.AddIndex(model_name="companyemail", index=GinIndex(OpClass(Upper("value"), name="gin_trgm_ops"), name="companyemail_value_upper_trgm_gin_idx")),
                migrations.AddIndex(model_name="contactphone", index=GinIndex(OpClass(Upper("value"), name="gin_trgm_ops"), name="contactphone_value_upper_trgm_gin_idx")),
                migrations.AddIndex(model_name="contactemail", index=GinIndex(OpClass(Upper("value"), name="gin_trgm_ops"), name="contactemail_value_upper_trgm_gin_idx")),
                migrations.AddIndex(model_name="contact", index=GinIndex(OpClass(Upper("first_name"), name="gin_trgm_ops"), name="contact_first_name_upper_trgm_gin_idx")),
                migrations.AddIndex(model_name="contact", index=GinIndex(OpClass(Upper("last_name"), name="gin_trgm_ops"), name="contact_last_name_upper_trgm_gin_idx")),
            ],
        ),
    ]
