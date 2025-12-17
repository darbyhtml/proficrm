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
        SENT = "sent", "Отправлено"
        STOPPED = "stopped", "Остановлено"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="mail_campaigns", verbose_name="Создатель")

    name = models.CharField("Название", max_length=200)
    subject = models.CharField("Тема письма", max_length=200)
    body_text = models.TextField("Текст письма (plain)", blank=True, default="")
    body_html = models.TextField("Текст письма (HTML)", blank=True, default="")

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

# Create your models here.
