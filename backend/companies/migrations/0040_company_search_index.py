from django.contrib.postgres.operations import UnaccentExtension, TrigramExtension
from django.contrib.postgres.indexes import GinIndex, OpClass
from django.contrib.postgres.search import SearchVectorField
from django.db import migrations, models
from django.db.models.functions import Upper


TSV_TRIGGER_SQL = """
CREATE OR REPLACE FUNCTION companies_companysearchindex_tsv_update()
RETURNS trigger AS $$
BEGIN
  -- Вектора по группам (вес задаём через rank-мультипликаторы в запросе)
  NEW.vector_a := setweight(to_tsvector('russian', unaccent(coalesce(NEW.t_ident, ''))), 'A');
  NEW.vector_b := setweight(to_tsvector('russian', unaccent(coalesce(NEW.t_name, ''))), 'B');
  NEW.vector_c := setweight(to_tsvector('russian', unaccent(coalesce(NEW.t_contacts, ''))), 'C');
  NEW.vector_d := setweight(to_tsvector('russian', unaccent(coalesce(NEW.t_other, ''))), 'D');
  RETURN NEW;
END
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS companies_companysearchindex_tsv_update_trg ON companies_companysearchindex;
CREATE TRIGGER companies_companysearchindex_tsv_update_trg
BEFORE INSERT OR UPDATE OF t_ident, t_name, t_contacts, t_other
ON companies_companysearchindex
FOR EACH ROW
EXECUTE FUNCTION companies_companysearchindex_tsv_update();
"""


TSV_TRIGGER_REVERSE_SQL = """
DROP TRIGGER IF EXISTS companies_companysearchindex_tsv_update_trg ON companies_companysearchindex;
DROP FUNCTION IF EXISTS companies_companysearchindex_tsv_update();
"""


class Migration(migrations.Migration):

    dependencies = [
        ("companies", "0039_contract_type_to_foreign_key"),
    ]

    operations = [
        # На некоторых окружениях pg_trgm уже включён (0033), но повторная операция безопасна.
        TrigramExtension(),
        UnaccentExtension(),
        migrations.CreateModel(
            name="CompanySearchIndex",
            fields=[
                ("company", models.OneToOneField(on_delete=models.deletion.CASCADE, primary_key=True, related_name="search_index", serialize=False, to="companies.company")),
                ("t_ident", models.TextField(blank=True, default="")),
                ("t_name", models.TextField(blank=True, default="")),
                ("t_contacts", models.TextField(blank=True, default="")),
                ("t_other", models.TextField(blank=True, default="")),
                ("plain_text", models.TextField(blank=True, default="")),
                ("digits", models.TextField(blank=True, default="")),
                ("vector_a", SearchVectorField(null=True)),
                ("vector_b", SearchVectorField(null=True)),
                ("vector_c", SearchVectorField(null=True)),
                ("vector_d", SearchVectorField(null=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "indexes": [
                    GinIndex(fields=["vector_a"], name="cmp_si_va_gin_idx"),
                    GinIndex(fields=["vector_b"], name="cmp_si_vb_gin_idx"),
                    GinIndex(fields=["vector_c"], name="cmp_si_vc_gin_idx"),
                    GinIndex(fields=["vector_d"], name="cmp_si_vd_gin_idx"),
                    GinIndex(OpClass(Upper("plain_text"), name="gin_trgm_ops"), name="cmp_si_plain_trgm_idx"),
                    GinIndex(OpClass("digits", name="gin_trgm_ops"), name="cmp_si_digits_trgm_idx"),
                ],
            },
        ),
        migrations.RunSQL(sql=TSV_TRIGGER_SQL, reverse_sql=TSV_TRIGGER_REVERSE_SQL),
    ]

