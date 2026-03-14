from django.db import migrations




def _rename_indexes_pg(apps, schema_editor):
    """ALTER INDEX IF EXISTS работает только в PostgreSQL; для SQLite — пропускаем."""
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute("ALTER INDEX IF EXISTS company_name_upper_trgm_gin_idx RENAME TO cmp_name_trgm_gin_idx;")
    schema_editor.execute("ALTER INDEX IF EXISTS company_legal_name_upper_trgm_gin_idx RENAME TO cmp_legal_trgm_gin_idx;")
    schema_editor.execute("ALTER INDEX IF EXISTS company_address_upper_trgm_gin_idx RENAME TO cmp_addr_trgm_gin_idx;")
    schema_editor.execute("ALTER INDEX IF EXISTS company_inn_upper_trgm_gin_idx RENAME TO cmp_inn_trgm_gin_idx;")
    schema_editor.execute("ALTER INDEX IF EXISTS company_phone_upper_trgm_gin_idx RENAME TO cmp_phone_trgm_gin_idx;")
    schema_editor.execute("ALTER INDEX IF EXISTS company_email_upper_trgm_gin_idx RENAME TO cmp_email_trgm_gin_idx;")
    schema_editor.execute("ALTER INDEX IF EXISTS companyemail_value_upper_trgm_gin_idx RENAME TO cmp_emailval_trgm_gin_idx;")
    schema_editor.execute("ALTER INDEX IF EXISTS companyphone_value_upper_trgm_gin_idx RENAME TO cmp_phoneval_trgm_gin_idx;")
    schema_editor.execute("ALTER INDEX IF EXISTS contact_first_name_upper_trgm_gin_idx RENAME TO ct_first_trgm_gin_idx;")
    schema_editor.execute("ALTER INDEX IF EXISTS contact_last_name_upper_trgm_gin_idx RENAME TO ct_last_trgm_gin_idx;")
    schema_editor.execute("ALTER INDEX IF EXISTS contactemail_value_upper_trgm_gin_idx RENAME TO ct_emailval_trgm_gin_idx;")
    schema_editor.execute("ALTER INDEX IF EXISTS contactphone_value_upper_trgm_gin_idx RENAME TO ct_phoneval_trgm_gin_idx;")


class Migration(migrations.Migration):
    dependencies = [
        ("companies", "0035_rename_companies_c_value_idx_companies_c_value_a17e5d_idx_and_more"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(_rename_indexes_pg, migrations.RunPython.noop),
            ],
            state_operations=[
                migrations.RenameIndex(
                    model_name="company",
                    old_name="company_name_upper_trgm_gin_idx",
                    new_name="cmp_name_trgm_gin_idx",
                ),
                migrations.RenameIndex(
                    model_name="company",
                    old_name="company_legal_name_upper_trgm_gin_idx",
                    new_name="cmp_legal_trgm_gin_idx",
                ),
                migrations.RenameIndex(
                    model_name="company",
                    old_name="company_address_upper_trgm_gin_idx",
                    new_name="cmp_addr_trgm_gin_idx",
                ),
                migrations.RenameIndex(
                    model_name="company",
                    old_name="company_inn_upper_trgm_gin_idx",
                    new_name="cmp_inn_trgm_gin_idx",
                ),
                migrations.RenameIndex(
                    model_name="company",
                    old_name="company_phone_upper_trgm_gin_idx",
                    new_name="cmp_phone_trgm_gin_idx",
                ),
                migrations.RenameIndex(
                    model_name="company",
                    old_name="company_email_upper_trgm_gin_idx",
                    new_name="cmp_email_trgm_gin_idx",
                ),
                migrations.RenameIndex(
                    model_name="companyemail",
                    old_name="companyemail_value_upper_trgm_gin_idx",
                    new_name="cmp_emailval_trgm_gin_idx",
                ),
                migrations.RenameIndex(
                    model_name="companyphone",
                    old_name="companyphone_value_upper_trgm_gin_idx",
                    new_name="cmp_phoneval_trgm_gin_idx",
                ),
                migrations.RenameIndex(
                    model_name="contact",
                    old_name="contact_first_name_upper_trgm_gin_idx",
                    new_name="ct_first_trgm_gin_idx",
                ),
                migrations.RenameIndex(
                    model_name="contact",
                    old_name="contact_last_name_upper_trgm_gin_idx",
                    new_name="ct_last_trgm_gin_idx",
                ),
                migrations.RenameIndex(
                    model_name="contactemail",
                    old_name="contactemail_value_upper_trgm_gin_idx",
                    new_name="ct_emailval_trgm_gin_idx",
                ),
                migrations.RenameIndex(
                    model_name="contactphone",
                    old_name="contactphone_value_upper_trgm_gin_idx",
                    new_name="ct_phoneval_trgm_gin_idx",
                ),
            ],
        ),
    ]

