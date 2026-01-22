from __future__ import annotations

from django.conf import settings
from django.db import models


class PolicyConfig(models.Model):
    """
    Глобальная конфигурация политики.
    Храним одной строкой (singleton) через load().
    """

    class Mode(models.TextChoices):
        OBSERVE_ONLY = "observe_only", "Наблюдение (не блокировать, только логировать)"
        ENFORCE = "enforce", "Применять (блокировать запрещённые действия)"

    mode = models.CharField(max_length=32, choices=Mode.choices, default=Mode.OBSERVE_ONLY, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def load(cls) -> "PolicyConfig":
        obj, _ = cls.objects.get_or_create(id=1, defaults={"mode": cls.Mode.OBSERVE_ONLY})
        return obj

    def __str__(self) -> str:
        return f"PolicyConfig(mode={self.mode})"


class PolicyRule(models.Model):
    """
    Правило доступа.

    MVP: pages + actions, настраиваемое для ролей и/или пользователей.
    """

    class SubjectType(models.TextChoices):
        ROLE = "role", "Роль"
        USER = "user", "Пользователь"

    class Effect(models.TextChoices):
        ALLOW = "allow", "Разрешить"
        DENY = "deny", "Запретить"

    class ResourceType(models.TextChoices):
        PAGE = "page", "Страница"
        ACTION = "action", "Действие"

    enabled = models.BooleanField(default=True, db_index=True)
    priority = models.IntegerField(default=100, db_index=True, help_text="Меньше = выше приоритет")

    subject_type = models.CharField(max_length=16, choices=SubjectType.choices, db_index=True)
    role = models.CharField(max_length=32, blank=True, default="", db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="policy_rules",
    )

    resource_type = models.CharField(max_length=16, choices=ResourceType.choices, db_index=True)
    resource = models.CharField(max_length=120, db_index=True)
    effect = models.CharField(max_length=8, choices=Effect.choices, db_index=True)

    # Пока не используем активно, но закладываем для будущих условий (branch_match, scope и т.д.)
    conditions = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["enabled", "resource_type", "resource"]),
            models.Index(fields=["subject_type", "role"]),
            models.Index(fields=["subject_type", "user"]),
            models.Index(fields=["priority"]),
        ]

    def __str__(self) -> str:
        subj = self.role if self.subject_type == self.SubjectType.ROLE else f"user:{self.user_id}"
        return f"{self.effect} {self.resource_type}:{self.resource} for {self.subject_type}:{subj}"

