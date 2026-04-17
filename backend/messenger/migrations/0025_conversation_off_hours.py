# Generated manually for F5 (2026-04-18) — off-hours request feature.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("messenger", "0024_conversation_branch_protect_status_check"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # 1. Снять старый CheckConstraint (без waiting_offline).
        migrations.RemoveConstraint(
            model_name="conversation",
            name="conversation_valid_status",
        ),
        # 2. Новые поля off-hours.
        migrations.AddField(
            model_name="conversation",
            name="off_hours_channel",
            field=models.CharField(
                blank=True,
                choices=[
                    ("call", "Звонок"),
                    ("messenger", "Мессенджер"),
                    ("email", "Email"),
                    ("other", "Другое"),
                ],
                default="",
                help_text="Выбранный клиентом способ связи: звонок/мессенджер/email/другое",
                max_length=16,
                verbose_name="Предпочтительный канал связи",
            ),
        ),
        migrations.AddField(
            model_name="conversation",
            name="off_hours_contact",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Телефон/email/никнейм — как клиент хочет, чтобы связались",
                max_length=255,
                verbose_name="Контакт для связи",
            ),
        ),
        migrations.AddField(
            model_name="conversation",
            name="off_hours_note",
            field=models.TextField(
                blank=True,
                default="",
                verbose_name="Комментарий клиента",
            ),
        ),
        migrations.AddField(
            model_name="conversation",
            name="off_hours_requested_at",
            field=models.DateTimeField(
                blank=True,
                db_index=True,
                null=True,
                verbose_name="Когда оставлена off-hours заявка",
            ),
        ),
        migrations.AddField(
            model_name="conversation",
            name="contacted_back_at",
            field=models.DateTimeField(
                blank=True,
                null=True,
                verbose_name="Когда менеджер отметил «Я связался»",
            ),
        ),
        migrations.AddField(
            model_name="conversation",
            name="contacted_back_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="contacted_back_conversations",
                to=settings.AUTH_USER_MODEL,
                verbose_name="Кто нажал «Я связался»",
            ),
        ),
        # 3. Обновлённый CheckConstraint с новым статусом.
        migrations.AddConstraint(
            model_name="conversation",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    (
                        "status__in",
                        [
                            "open",
                            "pending",
                            "waiting_offline",
                            "resolved",
                            "closed",
                        ],
                    )
                ),
                name="conversation_valid_status",
            ),
        ),
    ]
