from __future__ import annotations

import json

from django.conf import settings
from django.db import models


class Notification(models.Model):
    class Kind(models.TextChoices):
        INFO = "info", "Инфо"
        TASK = "task", "Задача"
        COMPANY = "company", "Компания"
        SYSTEM = "system", "Система"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications", verbose_name="Пользователь")
    kind = models.CharField("Тип", max_length=16, choices=Kind.choices, default=Kind.INFO)
    title = models.CharField("Заголовок", max_length=200)
    body = models.TextField("Текст", blank=True, default="")
    url = models.CharField("Ссылка", max_length=300, blank=True, default="")

    is_read = models.BooleanField("Прочитано", default=False)
    created_at = models.DateTimeField("Создано", auto_now_add=True)
    payload = models.JSONField("Дополнительные данные", null=True, blank=True, default=dict)

    class Meta:
        verbose_name = "Уведомление"
        verbose_name_plural = "Уведомления"
        indexes = [
            models.Index(fields=["user", "is_read", "created_at"]),
            models.Index(fields=["user", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.user}: {self.title}"


class CompanyContractReminder(models.Model):
    """
    Дедупликация напоминаний по окончанию договора:
    чтобы не слать одно и то же уведомление каждый запрос/день.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="contract_reminders", verbose_name="Пользователь")
    company = models.ForeignKey("companies.Company", on_delete=models.CASCADE, related_name="contract_reminders", verbose_name="Компания")
    contract_until = models.DateField("Действует до")
    days_before = models.PositiveSmallIntegerField("За сколько дней")
    created_at = models.DateTimeField("Создано", auto_now_add=True)

    class Meta:
        verbose_name = "Напоминание по договору"
        verbose_name_plural = "Напоминания по договорам"
        constraints = [
            models.UniqueConstraint(fields=["user", "company", "contract_until", "days_before"], name="uniq_contract_reminder"),
        ]
        indexes = [
            models.Index(fields=["user", "created_at"], name="contractrem_u_created_idx"),
            models.Index(fields=["user", "company", "contract_until"], name="contractrem_u_c_until_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.user} {self.company_id} {self.contract_until} -{self.days_before}d"

# Create your models here.
