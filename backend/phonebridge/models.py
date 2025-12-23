from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models


class PhoneDevice(models.Model):
    """
    Привязка Android-устройства к пользователю (по device_id, который генерируется на клиенте).
    Для MVP (APK вручную) устройство периодически опрашивает CRM и получает команды на звонок.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="phone_devices")

    device_id = models.CharField(max_length=64, db_index=True)
    device_name = models.CharField(max_length=120, blank=True, default="")
    platform = models.CharField(max_length=16, default="android")

    # На будущее: если решим добавить FCM push.
    fcm_token = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = (("user", "device_id"),)
        indexes = [
            models.Index(fields=["user", "device_id"]),
        ]

    def __str__(self) -> str:
        return f"{self.user_id}:{self.platform}:{self.device_id}"


class CallRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Ожидает"
        DELIVERED = "delivered", "Доставлено"
        CONSUMED = "consumed", "Получено"
        CANCELLED = "cancelled", "Отменено"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="call_requests",
        verbose_name="Кому (чей телефон)",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_call_requests",
        verbose_name="Кто инициировал",
    )

    company = models.ForeignKey("companies.Company", null=True, blank=True, on_delete=models.SET_NULL)
    contact = models.ForeignKey("companies.Contact", null=True, blank=True, on_delete=models.SET_NULL)

    phone_raw = models.CharField(max_length=64)
    note = models.CharField(max_length=255, blank=True, default="")
    is_cold_call = models.BooleanField(default=False, db_index=True)

    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    consumed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "status", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"CallRequest({self.user_id}, {self.phone_raw}, {self.status})"


