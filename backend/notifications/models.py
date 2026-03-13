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

class CrmAnnouncement(models.Model):
    class Type(models.TextChoices):
        INFO = "info", "Информация"
        IMPORTANT = "important", "Важно"
        URGENT = "urgent", "Срочно"

    title = models.CharField("Заголовок", max_length=200)
    body = models.TextField("Текст сообщения")
    announcement_type = models.CharField("Тип", max_length=16, choices=Type.choices, default=Type.INFO)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="created_announcements", verbose_name="Автор")
    created_at = models.DateTimeField("Создано", auto_now_add=True)
    is_active = models.BooleanField("Активно", default=True)
    scheduled_at = models.DateTimeField("Показать с", null=True, blank=True, help_text="Если пусто — показывается сразу")

    class Meta:
        verbose_name = "Объявление CRM"
        verbose_name_plural = "Объявления CRM"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_announcement_type_display()}: {self.title}"

    @property
    def is_published(self):
        from django.utils import timezone
        if not self.is_active:
            return False
        if self.scheduled_at and self.scheduled_at > timezone.now():
            return False
        return True

    def read_count(self):
        return self.reads.count()

    def total_users(self):
        from django.contrib.auth import get_user_model
        User = get_user_model()
        return User.objects.filter(is_active=True).count()


class CrmAnnouncementRead(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="announcement_reads", verbose_name="Пользователь")
    announcement = models.ForeignKey(CrmAnnouncement, on_delete=models.CASCADE, related_name="reads", verbose_name="Объявление")
    read_at = models.DateTimeField("Прочитано", auto_now_add=True)

    class Meta:
        verbose_name = "Прочтение объявления"
        verbose_name_plural = "Прочтения объявлений"
        constraints = [
            models.UniqueConstraint(fields=["user", "announcement"], name="uniq_announcement_read"),
        ]

    def __str__(self):
        return f"{self.user} — {self.announcement_id}"
