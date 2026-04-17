"""F5: UserAbsence — учёт отпуска/отгула/больничного.

Используется в messenger.services.auto_assign_conversation для исключения
отсутствующих менеджеров из кандидатов на новый диалог.

Миграция additive — ничего не удаляется.
"""
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0014_remove_duplicate_magiclink_index"),
    ]

    operations = [
        migrations.CreateModel(
            name="UserAbsence",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("start_date", models.DateField(verbose_name="Начало (включительно)")),
                (
                    "end_date",
                    models.DateField(
                        help_text=(
                            "День возвращения — последний день отсутствия, "
                            "не первый день работы."
                        ),
                        verbose_name="Окончание (включительно)",
                    ),
                ),
                (
                    "type",
                    models.CharField(
                        choices=[
                            ("vacation", "Отпуск"),
                            ("sick", "Больничный"),
                            ("dayoff", "Отгул"),
                            ("other", "Другое"),
                        ],
                        default="vacation",
                        max_length=16,
                        verbose_name="Тип",
                    ),
                ),
                (
                    "note",
                    models.CharField(blank=True, default="", max_length=255, verbose_name="Комментарий"),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Создано")),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=models.deletion.SET_NULL,
                        related_name="created_absences",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Кто создал",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="absences",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Сотрудник",
                    ),
                ),
            ],
            options={
                "verbose_name": "Отсутствие сотрудника",
                "verbose_name_plural": "Отсутствия сотрудников",
                "ordering": ["-start_date"],
            },
        ),
        migrations.AddIndex(
            model_name="userabsence",
            index=models.Index(
                fields=["user", "end_date"],
                name="user_absence_user_end_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="userabsence",
            constraint=models.CheckConstraint(
                condition=models.Q(end_date__gte=models.F("start_date")),
                name="user_absence_end_after_start",
            ),
        ),
    ]
