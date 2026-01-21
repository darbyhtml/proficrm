from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("companies", "0035_rename_companies_c_value_idx_companies_c_value_a17e5d_idx_and_more"),
    ]

    operations = [
        # Company trigram indexes (created in 0033_add_search_indexes)
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
        # Related tables trigram indexes
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
    ]

