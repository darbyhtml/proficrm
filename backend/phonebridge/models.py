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
    app_version = models.CharField(max_length=32, blank=True, default="")

    # На будущее: если решим добавить FCM push.
    fcm_token = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    last_poll_code = models.IntegerField(null=True, blank=True)
    last_poll_at = models.DateTimeField(null=True, blank=True)
    last_ip = models.GenericIPAddressField(null=True, blank=True)
    last_error_code = models.CharField(max_length=64, blank=True, default="")
    last_error_message = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        unique_together = (("user", "device_id"),)
        indexes = [
            models.Index(fields=["user", "device_id"]),
            models.Index(fields=["last_seen_at"]),
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

    # Данные о фактическом звонке (отправляются из Android приложения)
    class CallStatus(models.TextChoices):
        CONNECTED = "connected", "Дозвонился"
        NO_ANSWER = "no_answer", "Не дозвонился"
        BUSY = "busy", "Занято"
        REJECTED = "rejected", "Отклонен"
        MISSED = "missed", "Пропущен"

    call_status = models.CharField(
        max_length=16,
        choices=CallStatus.choices,
        null=True,
        blank=True,
        db_index=True,
        verbose_name="Статус звонка",
    )
    call_started_at = models.DateTimeField(null=True, blank=True, verbose_name="Время начала звонка")
    call_duration_seconds = models.IntegerField(null=True, blank=True, verbose_name="Длительность звонка (секунды)")

    class Meta:
        indexes = [
            models.Index(fields=["user", "status", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"CallRequest({self.user_id}, {self.phone_raw}, {self.status})"


class PhoneTelemetry(models.Model):
    class Type(models.TextChoices):
        LATENCY = "latency", "Latency"
        ERROR = "error", "Error"
        AUTH = "auth", "Auth"
        QUEUE = "queue", "Queue"
        OTHER = "other", "Other"

    device = models.ForeignKey(PhoneDevice, null=True, blank=True, on_delete=models.SET_NULL, related_name="telemetry")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="phone_telemetry")
    ts = models.DateTimeField("Время события")
    type = models.CharField("Тип", max_length=32, choices=Type.choices, default=Type.OTHER)
    endpoint = models.CharField("Endpoint", max_length=128, blank=True, default="")
    http_code = models.IntegerField("HTTP-код", null=True, blank=True)
    value_ms = models.IntegerField("Значение (мс)", null=True, blank=True)
    extra = models.JSONField("Доп. данные", blank=True, default=dict)

    class Meta:
        indexes = [
            models.Index(fields=["user", "ts"]),
            models.Index(fields=["endpoint", "ts"]),
        ]
        ordering = ["-ts"]

    def __str__(self) -> str:
        return f"Telemetry({self.user_id}, {self.type}, {self.endpoint}, {self.http_code})"


class PhoneLogBundle(models.Model):
    device = models.ForeignKey(PhoneDevice, null=True, blank=True, on_delete=models.SET_NULL, related_name="log_bundles")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="phone_log_bundles")
    ts = models.DateTimeField("Время")
    level_summary = models.CharField("Уровень", max_length=64, blank=True, default="")
    source = models.CharField("Источник", max_length=64, blank=True, default="")
    payload = models.TextField("Данные")

    class Meta:
        indexes = [
            models.Index(fields=["user", "ts"]),
            models.Index(fields=["source", "ts"]),
        ]
        ordering = ["-ts"]

    def __str__(self) -> str:
        return f"LogBundle({self.user_id}, {self.level_summary}, {self.source})"


