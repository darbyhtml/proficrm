from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("companies", "0004_company_created_by"),
    ]

    operations = [
        migrations.AddField(
            model_name="company",
            name="phone",
            field=models.CharField(blank=True, db_index=True, default="", max_length=50, verbose_name="Телефон (основной)"),
        ),
        migrations.AddField(
            model_name="company",
            name="email",
            field=models.EmailField(blank=True, db_index=True, default="", max_length=254, verbose_name="Email (основной)"),
        ),
        migrations.AddField(
            model_name="company",
            name="contact_name",
            field=models.CharField(blank=True, default="", max_length=255, verbose_name="Контакт (ФИО)"),
        ),
        migrations.AddField(
            model_name="company",
            name="contact_position",
            field=models.CharField(blank=True, default="", max_length=255, verbose_name="Контакт (должность)"),
        ),
    ]


