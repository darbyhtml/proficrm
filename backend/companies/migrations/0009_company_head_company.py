from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("companies", "0008_companynote_pins"),
    ]

    operations = [
        migrations.AddField(
            model_name="company",
            name="head_company",
            field=models.ForeignKey(
                blank=True,
                help_text="Если эта карточка — филиал/подразделение клиента, выберите головную организацию.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="client_branches",
                to="companies.company",
                verbose_name="Головная организация",
            ),
        ),
    ]


