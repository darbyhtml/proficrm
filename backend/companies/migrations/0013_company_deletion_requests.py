from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0003_alter_user_role"),
        ("companies", "0012_company_cold_call"),
    ]

    operations = [
        migrations.CreateModel(
            name="CompanyDeletionRequest",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("company_id_snapshot", models.UUIDField(db_index=True, verbose_name="ID компании (снимок)")),
                ("company_name_snapshot", models.CharField(blank=True, default="", max_length=255, verbose_name="Название компании (снимок)")),
                ("note", models.TextField(blank=True, default="", verbose_name="Примечание (почему удалить)")),
                ("status", models.CharField(choices=[("pending", "Ожидает решения"), ("cancelled", "Отклонено"), ("approved", "Подтверждено")], db_index=True, default="pending", max_length=16, verbose_name="Статус")),
                ("decision_note", models.TextField(blank=True, default="", verbose_name="Комментарий решения")),
                ("decided_at", models.DateTimeField(blank=True, null=True, verbose_name="Когда решили")),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="Создано")),
                ("company", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="deletion_requests", to="companies.company", verbose_name="Компания")),
                ("decided_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="company_delete_decisions", to=settings.AUTH_USER_MODEL, verbose_name="Кто решил")),
                ("requested_by", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="company_delete_requests", to=settings.AUTH_USER_MODEL, verbose_name="Кто запросил")),
                ("requested_by_branch", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to="accounts.branch", verbose_name="Филиал автора (снимок)")),
            ],
            options={
                "indexes": [
                    models.Index(fields=["company_id_snapshot", "status"], name="companies_co_company__f24e1e_idx"),
                    models.Index(fields=["status", "created_at"], name="companies_co_status__514c62_idx"),
                ],
            },
        ),
    ]


