from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("companies", "0045_companysphere_is_important"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="CompanyDeal",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("program", models.TextField(blank=True, default="", verbose_name="Программа обучения")),
                (
                    "price_per_person",
                    models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True, verbose_name="Стоимость за человека"),
                ),
                ("listeners_count", models.PositiveIntegerField(blank=True, null=True, verbose_name="Количество слушателей")),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="Создано")),
                (
                    "company",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="deals", to="companies.company", verbose_name="Компания"),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="company_deals",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Автор",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="companydeal",
            index=models.Index(fields=["company", "created_at"], name="cmp_deal_co_created_idx"),
        ),
    ]

