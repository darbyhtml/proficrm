from django.db import migrations, models
import django.db.models.deletion
from django.conf import settings


class Migration(migrations.Migration):
    dependencies = [
        ("notifications", "0001_initial"),
        ("companies", "0010_company_contract_fields"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="CompanyContractReminder",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("contract_until", models.DateField(verbose_name="Действует до")),
                ("days_before", models.PositiveSmallIntegerField(verbose_name="За сколько дней")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Создано")),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="contract_reminders", to="companies.company", verbose_name="Компания")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="contract_reminders", to=settings.AUTH_USER_MODEL, verbose_name="Пользователь")),
            ],
            options={
                "verbose_name": "Напоминание по договору",
                "verbose_name_plural": "Напоминания по договорам",
            },
        ),
        migrations.AddIndex(
            model_name="companycontractreminder",
            index=models.Index(fields=["user", "created_at"], name="contractrem_u_created_idx"),
        ),
        migrations.AddIndex(
            model_name="companycontractreminder",
            index=models.Index(fields=["user", "company", "contract_until"], name="contractrem_u_c_until_idx"),
        ),
        migrations.AddConstraint(
            model_name="companycontractreminder",
            constraint=models.UniqueConstraint(fields=("user", "company", "contract_until", "days_before"), name="uniq_contract_reminder"),
        ),
    ]


