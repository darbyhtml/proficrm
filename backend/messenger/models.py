from __future__ import annotations

import secrets
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class Inbox(models.Model):
    name = models.CharField("Название", max_length=255)
    branch = models.ForeignKey(
        "accounts.Branch",
        verbose_name="Филиал",
        on_delete=models.CASCADE,
        related_name="inboxes",
    )
    is_active = models.BooleanField("Активен", default=True, db_index=True)
    widget_token = models.CharField(
        "Токен виджета",
        max_length=64,
        unique=True,
        help_text="Секретный токен для подключения виджета с сайта. Генерируется автоматически при создании, если не указан.",
    )
    settings = models.JSONField("Настройки", default=dict, blank=True)
    created_at = models.DateTimeField("Создано", auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "Inbox"
        verbose_name_plural = "Inboxes"

    def __str__(self) -> str:
        return self.name

    def clean(self):
        """
        Инварианты безопасности:
        - branch задаётся только при создании и не может быть изменён позже,
          чтобы не "перетягивать" существующие диалоги в другой филиал.
        """
        if self.pk:
            old = type(self).objects.only("branch_id").get(pk=self.pk)
            if old.branch_id != self.branch_id:
                raise ValidationError("Нельзя изменить филиал Inbox после создания.")

    def save(self, *args, **kwargs):
        # Генерируем widget_token автоматически, если он не задан.
        if not self.widget_token:
            self.widget_token = secrets.token_urlsafe(32)
        # Гарантируем инвариант через clean() и логику выше.
        self.full_clean()
        super().save(*args, **kwargs)


class Channel(models.Model):
    class Type(models.TextChoices):
        WEBSITE = "website", "Сайт"
        TELEGRAM = "telegram", "Telegram"
        WHATSAPP = "whatsapp", "WhatsApp"
        VK = "vk", "VK"
        EMAIL = "email", "Email"

    type = models.CharField("Тип", max_length=32, choices=Type.choices)
    inbox = models.ForeignKey(
        Inbox,
        verbose_name="Inbox",
        on_delete=models.CASCADE,
        related_name="channels",
    )
    config = models.JSONField("Конфигурация", default=dict, blank=True)
    is_active = models.BooleanField("Активен", default=True, db_index=True)

    class Meta:
        verbose_name = "Канал"
        verbose_name_plural = "Каналы"

    def __str__(self) -> str:
        return f"{self.get_type_display()} / {self.inbox}"


class Contact(models.Model):
    """
    Контакт посетителя/клиента в контексте messenger.

    Может быть не привязан к Company, поэтому используем собственную сущность.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    external_id = models.CharField(
        "Внешний ID",
        max_length=255,
        blank=True,
        default="",
        help_text="Идентификатор во внешней системе (visitor_id, Telegram user_id и т.п.)",
    )
    name = models.CharField("Имя", max_length=255, blank=True, default="")
    email = models.EmailField("Email", max_length=254, blank=True, default="")
    phone = models.CharField("Телефон", max_length=50, blank=True, default="")
    region_detected = models.ForeignKey(
        "companies.Region",
        verbose_name="Определённый регион",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="messenger_contacts",
    )
    created_at = models.DateTimeField("Создано", auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "Контакт"
        verbose_name_plural = "Контакты"
        indexes = [
            models.Index(fields=["external_id"]),
            models.Index(fields=["email"]),
            models.Index(fields=["phone"]),
        ]

    def __str__(self) -> str:
        return self.name or self.email or self.phone or str(self.id)


class Conversation(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", "Открыт"
        PENDING = "pending", "В ожидании"
        RESOLVED = "resolved", "Решён"
        CLOSED = "closed", "Закрыт"

    class Priority(models.IntegerChoices):
        LOW = 10, "Низкий"
        NORMAL = 20, "Обычный"
        HIGH = 30, "Высокий"

    inbox = models.ForeignKey(
        Inbox,
        verbose_name="Inbox",
        on_delete=models.CASCADE,
        related_name="conversations",
    )
    contact = models.ForeignKey(
        Contact,
        verbose_name="Контакт",
        on_delete=models.CASCADE,
        related_name="conversations",
    )
    status = models.CharField(
        "Статус",
        max_length=16,
        choices=Status.choices,
        default=Status.OPEN,
        db_index=True,
    )
    priority = models.IntegerField(
        "Приоритет",
        choices=Priority.choices,
        default=Priority.NORMAL,
        db_index=True,
    )
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Назначенный оператор",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="assigned_conversations",
    )
    # ВАЖНО: branch определяется строго из inbox.branch и не редактируется вручную.
    branch = models.ForeignKey(
        "accounts.Branch",
        verbose_name="Филиал",
        on_delete=models.CASCADE,
        related_name="messenger_conversations",
        editable=False,
    )
    region = models.ForeignKey(
        "companies.Region",
        verbose_name="Регион",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="messenger_conversations",
    )
    last_message_at = models.DateTimeField(
        "Время последнего сообщения",
        null=True,
        blank=True,
        db_index=True,
    )
    created_at = models.DateTimeField("Создано", auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "Диалог"
        verbose_name_plural = "Диалоги"
        indexes = [
            models.Index(fields=["branch", "status"]),
            models.Index(fields=["created_at"]),
            models.Index(fields=["last_message_at"]),
        ]

    def __str__(self) -> str:
        return f"Conversation #{self.pk} ({self.inbox})"

    def clean(self):
        """
        Инварианты безопасности:
        - branch всегда совпадает с inbox.branch;
        - после создания диалога нельзя менять inbox (а значит, и branch).
        """
        if not self.inbox_id:
            raise ValidationError("Inbox обязателен для диалога.")
        # При наличии PK проверяем неизменяемость inbox.
        if self.pk:
            old = type(self).objects.only("inbox_id", "branch_id").get(pk=self.pk)
            if old.inbox_id != self.inbox_id:
                raise ValidationError("Нельзя изменить inbox существующего диалога.")
        # branch должен соответствовать inbox.branch (либо будет выставлен автоматически в save()).
        if self.branch_id and self.branch_id != self.inbox.branch_id:
            raise ValidationError("Филиал диалога должен совпадать с филиалом inbox.")

    def save(self, *args, **kwargs):
        """
        Автоматически проставляет branch из inbox.branch и запрещает его изменение.
        Также запрещает изменение inbox после создания диалога.
        """
        if self.inbox_id:
            inbox_branch_id = self.inbox.branch_id
            if self.pk:
                old = type(self).objects.only("inbox_id", "branch_id").get(pk=self.pk)
                if old.inbox_id != self.inbox_id:
                    # Жёстко запрещаем смену inbox для уже существующего диалога.
                    raise ValidationError("Нельзя изменить inbox существующего диалога.")
                if old.branch_id != inbox_branch_id:
                    # Защита от ручного изменения филиала inbox в БД.
                    raise ValidationError("Нельзя изменить филиал существующего диалога.")
            self.branch_id = inbox_branch_id
        super().save(*args, **kwargs)


class Message(models.Model):
    class Direction(models.TextChoices):
        IN = "in", "Входящее"
        OUT = "out", "Исходящее"
        INTERNAL = "internal", "Внутренняя заметка"

    id = models.BigAutoField(primary_key=True)
    conversation = models.ForeignKey(
        Conversation,
        verbose_name="Диалог",
        on_delete=models.CASCADE,
        related_name="messages",
    )
    direction = models.CharField(
        "Направление",
        max_length=16,
        choices=Direction.choices,
        db_index=True,
    )
    body = models.TextField("Текст сообщения", blank=True, default="")
    sender_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Пользователь-отправитель",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="sent_messages",
    )
    sender_contact = models.ForeignKey(
        Contact,
        verbose_name="Контакт-отправитель",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="sent_messages",
    )
    created_at = models.DateTimeField("Создано", auto_now_add=True, db_index=True)
    delivered_at = models.DateTimeField("Доставлено", null=True, blank=True)

    class Meta:
        verbose_name = "Сообщение"
        verbose_name_plural = "Сообщения"
        ordering = ["created_at", "id"]

    def __str__(self) -> str:
        return f"Message #{self.pk} in conv {self.conversation_id}"

    def clean(self):
        """
        Инварианты по direction/sender:
        - IN (входящее): sender_contact обязателен, sender_user запрещён;
        - OUT/INTERNAL: sender_user обязателен, sender_contact запрещён.
        """
        errors = {}
        if self.direction == self.Direction.IN:
            if not self.sender_contact_id:
                errors["sender_contact"] = "Для входящего сообщения обязателен отправитель-контакт."
            if self.sender_user_id:
                errors["sender_user"] = "Для входящего сообщения пользователь-отправитель не должен быть установлен."
        elif self.direction in (self.Direction.OUT, self.Direction.INTERNAL):
            if not self.sender_user_id:
                errors["sender_user"] = "Для исходящего или внутреннего сообщения обязателен пользователь-отправитель."
            if self.sender_contact_id:
                errors["sender_contact"] = "Для исходящего или внутреннего сообщения отправитель-контакт не должен быть установлен."
        if errors:
            raise ValidationError(errors)


class MessageAttachment(models.Model):
    """
    Вложения сообщений вынесены в отдельную модель для масштабируемости.
    """

    message = models.ForeignKey(
        Message,
        verbose_name="Сообщение",
        on_delete=models.CASCADE,
        related_name="attachments",
    )
    file = models.FileField("Файл", upload_to="messenger/attachments/%Y/%m/%d/")
    original_name = models.CharField("Имя файла", max_length=255, blank=True, default="")
    content_type = models.CharField("MIME тип", max_length=120, blank=True, default="")
    size = models.BigIntegerField("Размер (байт)", default=0)
    created_at = models.DateTimeField("Загружено", auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "Вложение сообщения"
        verbose_name_plural = "Вложения сообщений"

    def save(self, *args, **kwargs):
        f = self.file
        if f:
            if not self.original_name:
                self.original_name = (getattr(f, "name", "") or "").split("/")[-1].split("\\")[-1]
            if not self.size:
                try:
                    self.size = int(getattr(f, "size", 0) or 0)
                except Exception:
                    self.size = 0
            if not self.content_type:
                self.content_type = (getattr(f, "content_type", "") or "").strip()[:120]
        super().save(*args, **kwargs)


class RoutingRule(models.Model):
    name = models.CharField("Название", max_length=255)
    regions = models.ManyToManyField(
        "companies.Region",
        verbose_name="Регионы",
        related_name="messenger_routing_rules",
        blank=True,
    )
    branch = models.ForeignKey(
        "accounts.Branch",
        verbose_name="Филиал",
        on_delete=models.CASCADE,
        related_name="messenger_routing_rules",
    )
    inbox = models.ForeignKey(
        Inbox,
        verbose_name="Inbox",
        on_delete=models.CASCADE,
        related_name="routing_rules",
    )
    priority = models.IntegerField("Приоритет", default=100, db_index=True)
    is_fallback = models.BooleanField(
        "Фолбэк-правило",
        default=False,
        help_text="Используется, если не найдено ни одного подходящего правила по региону.",
    )
    is_active = models.BooleanField("Активно", default=True, db_index=True)

    class Meta:
        verbose_name = "Правило маршрутизации"
        verbose_name_plural = "Правила маршрутизации"
        ordering = ["priority", "id"]

    def __str__(self) -> str:
        return self.name


class CannedResponse(models.Model):
    title = models.CharField("Название", max_length=255)
    body = models.TextField("Текст ответа")
    branch = models.ForeignKey(
        "accounts.Branch",
        verbose_name="Филиал",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="canned_responses",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Создатель",
        on_delete=models.CASCADE,
        related_name="created_canned_responses",
    )
    created_at = models.DateTimeField("Создано", auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "Шаблон ответа"
        verbose_name_plural = "Шаблоны ответов"

    def __str__(self) -> str:
        return self.title


class AgentProfile(models.Model):
    class Status(models.TextChoices):
        ONLINE = "online", "Онлайн"
        OFFLINE = "offline", "Офлайн"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        verbose_name="Пользователь",
        on_delete=models.CASCADE,
        related_name="agent_profile",
    )
    avatar_url = models.URLField("URL аватара", max_length=500, blank=True, default="")
    display_name = models.CharField("Отображаемое имя", max_length=255, blank=True, default="")
    status = models.CharField(
        "Статус",
        max_length=16,
        choices=Status.choices,
        default=Status.OFFLINE,
        db_index=True,
    )
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "Профиль оператора"
        verbose_name_plural = "Профили операторов"

    def __str__(self) -> str:
        return self.display_name or str(self.user)

