import uuid
from django.conf import settings
from django.db import models


class MailAccount(models.Model):
    """
    SMTP аккаунт пользователя (например, Яндекс 587 STARTTLS).
    Храним app-password шифрованным (Fernet).
    """
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="mail_account", verbose_name="Пользователь")

    smtp_host = models.CharField("SMTP host", max_length=255, default="smtp.yandex.ru")
    smtp_port = models.PositiveIntegerField("SMTP port", default=587)
    use_starttls = models.BooleanField("STARTTLS", default=True)

    smtp_username = models.CharField("Логин SMTP", max_length=255, default="")
    smtp_password_enc = models.TextField("Пароль (зашифрован)", blank=True, default="")

    from_email = models.EmailField("Email отправителя", blank=True, default="")
    from_name = models.CharField("Имя отправителя", max_length=120, blank=True, default="")
    reply_to = models.EmailField("Reply-To", blank=True, default="")

    is_enabled = models.BooleanField("Включено", default=False)
    rate_per_minute = models.PositiveIntegerField("Лимит писем в минуту", default=20)
    rate_per_day = models.PositiveIntegerField("Лимит писем в день", default=500)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    def set_password(self, password: str):
        from mailer.crypto import encrypt_str
        self.smtp_password_enc = encrypt_str(password or "")

    def get_password(self) -> str:
        from mailer.crypto import decrypt_str
        return decrypt_str(self.smtp_password_enc or "")

    def __str__(self) -> str:
        return f"{self.user} ({self.from_email or self.smtp_username})"


class GlobalMailAccount(models.Model):
    """
    Глобальные SMTP-настройки (одни на всю CRM). Редактируются администратором.
    Пароль хранится шифрованным (Fernet), как и в MailAccount.
    """
    # smtp.bz defaults (recommended: 587 STARTTLS; alt: 2525)
    smtp_host = models.CharField("SMTP host", max_length=255, default="connect.smtp.bz")
    smtp_port = models.PositiveIntegerField("SMTP port", default=587)
    use_starttls = models.BooleanField("STARTTLS", default=True)

    smtp_username = models.CharField("Логин SMTP", max_length=255, default="")
    smtp_password_enc = models.TextField("Пароль (зашифрован)", blank=True, default="")

    # Единый адрес отправителя (From) для всей CRM
    from_email = models.EmailField("Email отправителя (From)", blank=True, default="no-reply@groupprofi.ru")
    # Опционально: дефолтное имя отправителя (если у пользователя не задано ФИО)
    from_name = models.CharField("Имя отправителя (по умолчанию)", max_length=120, blank=True, default="CRM ПРОФИ")
    is_enabled = models.BooleanField("Включено", default=False)

    # Глобальные лимиты (если нужно быстро ограничить отправку всей системой)
    # Для FREE smtp.bz: 100/час → 1/мин (плюс небольшой запас на retry)
    rate_per_minute = models.PositiveIntegerField("Лимит писем в минуту", default=1)
    # В тарифе указан общий лимит писем (обычно месячный). Оставляем большим, но можно снизить.
    rate_per_day = models.PositiveIntegerField("Лимит писем в день", default=15000)
    # Дополнительный лимит: сколько писем в день может отправить ОДИН менеджер
    per_user_daily_limit = models.PositiveIntegerField("Лимит писем в день на менеджера", default=100)
    
    # API smtp.bz для получения информации о тарифе и квоте
    smtp_bz_api_key = models.CharField("API ключ smtp.bz", max_length=255, blank=True, default="", help_text="API ключ для получения информации о тарифе и квоте")
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    @classmethod
    def load(cls) -> "GlobalMailAccount":
        obj, _ = cls.objects.get_or_create(id=1)
        return obj

    def set_password(self, password: str):
        from mailer.crypto import encrypt_str
        self.smtp_password_enc = encrypt_str(password or "")

    def get_password(self) -> str:
        from mailer.crypto import decrypt_str
        return decrypt_str(self.smtp_password_enc or "")

    def __str__(self) -> str:
        return f"Global SMTP ({self.smtp_username or self.smtp_host})"


class Unsubscribe(models.Model):
    email = models.EmailField("Email", unique=True)
    source = models.CharField(
        "Источник",
        max_length=24,
        blank=True,
        default="",
        help_text="manual/token/smtp_bz",
    )
    reason = models.CharField(
        "Причина",
        max_length=24,
        blank=True,
        default="",
        help_text="bounce/user/unsubscribe (если известно)",
    )
    last_seen_at = models.DateTimeField("Последнее обновление (из внешних источников)", null=True, blank=True)
    created_at = models.DateTimeField("Когда", auto_now_add=True)

    def __str__(self) -> str:
        return self.email


class UnsubscribeToken(models.Model):
    """
    Токенизированная отписка: безопаснее, чем /unsubscribe/<email>/.
    """
    token = models.CharField("Токен", max_length=64, unique=True, db_index=True)
    email = models.EmailField("Email", db_index=True)
    created_at = models.DateTimeField("Когда", auto_now_add=True)

    def __str__(self) -> str:
        return self.email


class Campaign(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Черновик"
        READY = "ready", "Готово к отправке"
        SENDING = "sending", "Отправляется"
        PAUSED = "paused", "На паузе"
        SENT = "sent", "Отправлено"
        STOPPED = "stopped", "Остановлено"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="mail_campaigns", verbose_name="Создатель")

    name = models.CharField("Название", max_length=200)
    subject = models.CharField("Тема письма", max_length=200)
    body_text = models.TextField("Текст письма (plain)", blank=True, default="")
    body_html = models.TextField("Текст письма (HTML)", blank=True, default="")
    sender_name = models.CharField("Имя отправителя", max_length=120, blank=True, default="")
    attachment = models.FileField("Вложение", upload_to="campaign_attachments/%Y/%m/", blank=True, null=True, help_text="Файл, который будет прикреплен ко всем письмам кампании")
    attachment_original_name = models.CharField(
        "Оригинальное имя вложения",
        max_length=255,
        blank=True,
        default="",
        help_text="Имя файла при загрузке (используется при отправке, чтобы не переименовывать вложение).",
    )

    # Снэпшот фильтра (пока просто сохраняем параметры)
    filter_meta = models.JSONField("Фильтр", default=dict, blank=True)

    status = models.CharField("Статус", max_length=16, choices=Status.choices, default=Status.DRAFT)

    created_at = models.DateTimeField("Создано", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    def __str__(self) -> str:
        return self.name


class CampaignRecipient(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "В очереди"
        SENT = "sent", "Отправлено"
        FAILED = "failed", "Ошибка"
        UNSUBSCRIBED = "unsubscribed", "Отписался"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="recipients", verbose_name="Кампания")

    email = models.EmailField("Email")
    contact_id = models.UUIDField("ID контакта", null=True, blank=True)
    company_id = models.UUIDField("ID компании", null=True, blank=True)

    status = models.CharField("Статус", max_length=16, choices=Status.choices, default=Status.PENDING)
    last_error = models.CharField("Ошибка", max_length=255, blank=True, default="")

    created_at = models.DateTimeField("Создано", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        unique_together = ("campaign", "email")
        indexes = [
            models.Index(fields=["campaign", "status"]),
            models.Index(fields=["email"]),
        ]

    def __str__(self) -> str:
        return f"{self.email} ({self.campaign})"


class SendLog(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="send_logs", verbose_name="Кампания")
    recipient = models.ForeignKey(CampaignRecipient, null=True, blank=True, on_delete=models.SET_NULL, related_name="send_logs", verbose_name="Получатель")
    account = models.ForeignKey(MailAccount, null=True, blank=True, on_delete=models.SET_NULL, related_name="send_logs", verbose_name="Аккаунт")

    provider = models.CharField("Провайдер", max_length=50, default="smtp")
    message_id = models.CharField("Message-ID", max_length=255, blank=True, default="")
    status = models.CharField("Статус", max_length=32, default="sent")
    error = models.TextField("Ошибка", blank=True, default="")
    created_at = models.DateTimeField("Когда", auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.campaign} {self.status}"

# cooldown на повторное использование email после "очистки" кампании
class EmailCooldown(models.Model):
    email = models.EmailField("Email", db_index=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="email_cooldowns")
    until_at = models.DateTimeField("Нельзя использовать до", db_index=True)
    created_at = models.DateTimeField("Создано", auto_now_add=True)

    class Meta:
        unique_together = ("email", "created_by")
        indexes = [
            models.Index(fields=["created_by", "until_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.email} until {self.until_at}"


class SmtpBzQuota(models.Model):
    """
    Информация о тарифе и квоте smtp.bz, полученная через API.
    Обновляется периодически через Celery задачу.
    """
    # Singleton: только одна запись
    id = models.IntegerField(primary_key=True, default=1, editable=False)
    
    # Информация о тарифе
    tariff_name = models.CharField("Название тарифа", max_length=50, blank=True, default="")
    tariff_renewal_date = models.DateField("Дата продления тарифа", null=True, blank=True)
    
    # Квота
    emails_available = models.PositiveIntegerField("Доступно писем", default=0)
    emails_limit = models.PositiveIntegerField("Лимит писем", default=0)
    
    # Лимиты по времени
    sent_per_hour = models.PositiveIntegerField("Отправлено за час", default=0)
    max_per_hour = models.PositiveIntegerField("Максимум в час", default=100)
    
    # Метаданные
    last_synced_at = models.DateTimeField("Последняя синхронизация", null=True, blank=True)
    sync_error = models.TextField("Ошибка синхронизации", blank=True, default="")
    
    created_at = models.DateTimeField("Создано", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)
    
    class Meta:
        verbose_name = "Квота smtp.bz"
        verbose_name_plural = "Квота smtp.bz"
    
    @classmethod
    def load(cls) -> "SmtpBzQuota":
        obj, _ = cls.objects.get_or_create(id=1)
        return obj
    
    def __str__(self) -> str:
        return f"smtp.bz: {self.emails_available}/{self.emails_limit} писем"


class CampaignQueue(models.Model):
    """
    Очередь рассылок. Кампании выполняются последовательно, чтобы не превышать лимиты.
    """
    class Status(models.TextChoices):
        PENDING = "pending", "В очереди"
        PROCESSING = "processing", "Обрабатывается"
        COMPLETED = "completed", "Завершена"
        CANCELLED = "cancelled", "Отменена"
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    campaign = models.OneToOneField(Campaign, on_delete=models.CASCADE, related_name="queue_entry", verbose_name="Кампания")
    
    status = models.CharField("Статус", max_length=16, choices=Status.choices, default=Status.PENDING)
    priority = models.IntegerField("Приоритет", default=0, help_text="Чем выше число, тем выше приоритет")
    
    # Время постановки в очередь и начала обработки
    queued_at = models.DateTimeField("Поставлено в очередь", auto_now_add=True)
    started_at = models.DateTimeField("Начало обработки", null=True, blank=True)
    completed_at = models.DateTimeField("Завершено", null=True, blank=True)

    # Отложенное продолжение: не обрабатывать до указанного времени (автодосыл на след. день/час)
    deferred_until = models.DateTimeField("Продолжить не ранее", null=True, blank=True)
    defer_reason = models.CharField(
        "Причина отложения",
        max_length=24,
        blank=True,
        default="",
        help_text="daily_limit, quota_exhausted, outside_hours, rate_per_hour, transient_error",
    )
    # ENTERPRISE: Счетчик последовательных transient ошибок для circuit breaker
    consecutive_transient_errors = models.IntegerField(
        "Последовательные transient ошибки",
        default=0,
        help_text="Используется для автоматической паузы при множественных ошибках SMTP"
    )

    class Meta:
        ordering = ["-priority", "queued_at"]
        indexes = [
            models.Index(fields=["status", "priority", "queued_at"]),
        ]
    
    def __str__(self) -> str:
        return f"{self.campaign.name} ({self.get_status_display()})"


class UserDailyLimitStatus(models.Model):
    """
    Отслеживание статуса дневного лимита пользователя для уведомлений.
    """
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="daily_limit_status", verbose_name="Пользователь")
    last_limit_reached_date = models.DateField("Дата последнего достижения лимита", null=True, blank=True, help_text="Дата, когда пользователь в последний раз достиг дневного лимита")
    last_notified_date = models.DateField("Дата последнего уведомления", null=True, blank=True, help_text="Дата, когда было отправлено последнее уведомление об обновлении лимита")
    
    updated_at = models.DateTimeField("Обновлено", auto_now=True)
    
    class Meta:
        verbose_name = "Статус дневного лимита пользователя"
        verbose_name_plural = "Статусы дневных лимитов пользователей"
        indexes = [
            models.Index(fields=["user", "last_limit_reached_date"]),
        ]
    
    def __str__(self) -> str:
        return f"{self.user} (лимит достигнут: {self.last_limit_reached_date or 'никогда'})"
