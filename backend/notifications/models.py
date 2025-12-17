from __future__ import annotations

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

    class Meta:
        verbose_name = "Уведомление"
        verbose_name_plural = "Уведомления"
        indexes = [
            models.Index(fields=["user", "is_read", "created_at"]),
            models.Index(fields=["user", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.user}: {self.title}"

# Create your models here.
