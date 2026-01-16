from __future__ import annotations

import uuid
import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.core.files.storage import default_storage


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
    encryption_enabled = models.BooleanField("Шифрование включено", default=True, help_text="Использует ли устройство EncryptedSharedPreferences")

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
        UNKNOWN = "unknown", "Не удалось определить"  # Новое: для случаев, когда результат не определён

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
    
    # Enum классы для валидации
    class CallDirection(models.TextChoices):
        OUTGOING = "outgoing", "Исходящий"
        INCOMING = "incoming", "Входящий"
        MISSED = "missed", "Пропущенный"
        UNKNOWN = "unknown", "Неизвестно"
    
    class ResolveMethod(models.TextChoices):
        OBSERVER = "observer", "Определено через ContentObserver"
        RETRY = "retry", "Определено через повторные проверки"
        UNKNOWN = "unknown", "Неизвестно"
    
    class ActionSource(models.TextChoices):
        CRM_UI = "crm_ui", "Команда из CRM"
        NOTIFICATION = "notification", "Нажатие на уведомление"
        HISTORY = "history", "Нажатие из истории звонков"
        UNKNOWN = "unknown", "Неизвестно"
    
    # Новые поля для расширенной аналитики (ЭТАП 3: добавлены в БД)
    call_ended_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Время окончания звонка"
    )
    direction = models.CharField(
        max_length=16,
        choices=CallDirection.choices,
        null=True,
        blank=True,
        db_index=True,
        verbose_name="Направление звонка"
    )
    resolve_method = models.CharField(
        max_length=16,
        choices=ResolveMethod.choices,
        null=True,
        blank=True,
        db_index=True,
        verbose_name="Метод определения результата"
    )
    attempts_count = models.IntegerField(
        null=True,
        blank=True,
        verbose_name="Количество попыток определения"
    )
    action_source = models.CharField(
        max_length=16,
        choices=ActionSource.choices,
        null=True,
        blank=True,
        db_index=True,
        verbose_name="Источник действия пользователя"
    )

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


class MobileAppBuild(models.Model):
    """
    Версия мобильного приложения (APK) для скачивания.
    Только production версии, staging не показываем.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    env = models.CharField(max_length=16, default="production", db_index=True, help_text="Только production")
    version_name = models.CharField(max_length=32, verbose_name="Версия (name)")
    version_code = models.IntegerField(verbose_name="Версия (code)")
    file = models.FileField(upload_to="mobile_apps/", verbose_name="APK файл")
    sha256 = models.CharField(max_length=64, blank=True, verbose_name="SHA256 хеш")
    uploaded_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата загрузки")
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="uploaded_app_builds",
        verbose_name="Загрузил",
    )
    is_active = models.BooleanField(default=True, db_index=True, verbose_name="Активна", help_text="Показывать в списке")

    class Meta:
        ordering = ["-uploaded_at"]
        indexes = [
            models.Index(fields=["env", "is_active", "-uploaded_at"]),
        ]

    def save(self, *args, **kwargs):
        # Вычисляем SHA256 при сохранении
        if self.file and not self.sha256:
            self.sha256 = self._calculate_sha256()
        super().save(*args, **kwargs)

    def _calculate_sha256(self) -> str:
        """Вычислить SHA256 хеш файла."""
        if not self.file:
            return ""
        try:
            hash_sha256 = hashlib.sha256()
            self.file.seek(0)
            for chunk in self.file.chunks():
                hash_sha256.update(chunk)
            self.file.seek(0)
            return hash_sha256.hexdigest()
        except Exception:
            return ""

    def get_file_size(self) -> int:
        """Получить размер файла в байтах."""
        if self.file and hasattr(self.file, "size"):
            return self.file.size
        return 0

    def get_file_size_display(self) -> str:
        """Получить размер файла в читаемом формате."""
        size = self.get_file_size()
        for unit in ["Б", "КБ", "МБ", "ГБ"]:
            if size < 1024.0:
                return f"{size:.1f} {unit}"
            size /= 1024.0
        return f"{size:.1f} ТБ"

    def __str__(self) -> str:
        return f"{self.version_name} ({self.version_code}) - {self.uploaded_at.strftime('%Y-%m-%d')}"


class MobileAppQrToken(models.Model):
    """
    Одноразовый токен для QR-логина в мобильное приложение.
    TTL: 5 минут, одноразовый.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="qr_tokens")
    token = models.CharField(max_length=128, unique=True, db_index=True, verbose_name="Токен")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создан")
    expires_at = models.DateTimeField(verbose_name="Истекает")
    used_at = models.DateTimeField(null=True, blank=True, verbose_name="Использован")
    ip_address = models.GenericIPAddressField(null=True, blank=True, verbose_name="IP адрес")
    user_agent = models.CharField(max_length=255, blank=True, default="", verbose_name="User-Agent")

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["token"]),
            models.Index(fields=["user", "-created_at"]),
            models.Index(fields=["expires_at"]),
        ]

    def save(self, *args, **kwargs):
        # Автоматически устанавливаем expires_at при создании
        if not self.expires_at:
            self.expires_at = timezone.now() + timedelta(minutes=5)
        super().save(*args, **kwargs)

    def is_valid(self) -> bool:
        """Проверить, валиден ли токен."""
        if self.used_at is not None:
            return False
        if timezone.now() > self.expires_at:
            return False
        return True

    def mark_as_used(self) -> None:
        """Пометить токен как использованный."""
        if self.used_at is None:
            self.used_at = timezone.now()
            self.save(update_fields=["used_at"])

    @classmethod
    def generate_token(cls) -> str:
        """Сгенерировать случайный токен (base64url-safe, 64 байта)."""
        return secrets.token_urlsafe(64)

    def __str__(self) -> str:
        return f"QRToken({self.user.username}, {self.token[:16]}..., expires={self.expires_at})"

