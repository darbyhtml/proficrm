from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):
    dependencies = [
        ("companies", "0005_company_primary_contacts"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="CompanyDocument",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("title", models.CharField(blank=True, default="", max_length=255, verbose_name="Название")),
                ("file", models.FileField(upload_to="company_docs/%Y/%m/%d/", verbose_name="Файл")),
                ("original_name", models.CharField(blank=True, default="", max_length=255, verbose_name="Оригинальное имя файла")),
                ("ext", models.CharField(blank=True, db_index=True, default="", max_length=16, verbose_name="Расширение")),
                ("size", models.BigIntegerField(default=0, verbose_name="Размер (байт)")),
                ("content_type", models.CharField(blank=True, default="", max_length=120, verbose_name="MIME тип")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "company",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="documents", to="companies.company", verbose_name="Компания"),
                ),
                (
                    "uploaded_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="uploaded_company_documents",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Кто загрузил",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="companydocument",
            index=models.Index(fields=["company", "created_at"], name="companydoc_company_created_idx"),
        ),
    ]


