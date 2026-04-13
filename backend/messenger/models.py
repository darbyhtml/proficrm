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
        null=True,
        blank=True,
        help_text="Пусто = общий (глобальный) inbox: филиал диалога определяется по GeoIP и правилам маршрутизации.",
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
    last_activity_at = models.DateTimeField(
        "Последняя активность",
        null=True,
        blank=True,
        db_index=True,
        help_text="Обновляется при создании входящего сообщения.",
    )
    blocked = models.BooleanField(
        "Заблокирован",
        default=False,
        db_index=True,
        help_text="Заблокированный контакт. Используется для mute диалогов.",
    )

    class Meta:
        verbose_name = "Контакт"
        verbose_name_plural = "Контакты"
        indexes = [
            models.Index(fields=["external_id"]),
            models.Index(fields=["email"]),
            models.Index(fields=["phone"]),
            models.Index(fields=["last_activity_at"]),
            models.Index(fields=["blocked"]),
        ]

    def __str__(self) -> str:
        return self.name or self.email or self.phone or str(self.id)
    
    def clean(self):
        """
        Валидации по образцу Chatwoot (если нужна мультитенантность).
        """
        # TODO: Добавить валидации email (case-insensitive уникальность)
        # TODO: Добавить валидации phone (формат E.164)
        super().clean()


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
    assignee_assigned_at = models.DateTimeField(
        "Когда назначен оператор",
        null=True,
        blank=True,
        db_index=True,
        help_text="Время последнего назначения; используется для эскалации по таймауту.",
    )
    assignee_opened_at = models.DateTimeField(
        "Когда оператор впервые открыл диалог",
        null=True,
        blank=True,
        help_text="После открытия эскалация по таймауту не выполняется.",
    )
    assignee_last_read_at = models.DateTimeField(
        "Когда оператор последний раз просматривал диалог",
        null=True,
        blank=True,
        db_index=True,
        help_text="Для подсчёта непрочитанных входящих сообщений.",
    )
    # ВАЖНО: branch определяется строго из inbox.branch и не редактируется вручную.
    branch = models.ForeignKey(
        "accounts.Branch",
        verbose_name="Филиал",
        on_delete=models.CASCADE,
        related_name="messenger_conversations",
        editable=False,
    )
    client_region = models.CharField(
        "Регион клиента",
        max_length=128,
        blank=True,
        default="",
        db_index=True,
        help_text="Определён по GeoIP / pre-chat анкете / компании клиента",
    )

    class RegionSource(models.TextChoices):
        GEOIP = "geoip", "GeoIP"
        FORM = "form", "Анкета"
        COMPANY = "company", "Компания"
        UNKNOWN = "", "Не определён"

    client_region_source = models.CharField(
        "Источник региона",
        max_length=16,
        choices=RegionSource.choices,
        blank=True,
        default=RegionSource.UNKNOWN,
    )
    region = models.ForeignKey(
        "companies.Region",
        verbose_name="Регион",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="messenger_conversations",
    )
    last_activity_at = models.DateTimeField(
        "Время последней активности",
        null=True,
        blank=True,
        db_index=True,
        help_text="Обновляется при каждом сообщении. Fallback на created_at.",
    )
    waiting_since = models.DateTimeField(
        "Когда начал ждать ответа",
        null=True,
        blank=True,
        db_index=True,
        help_text="Устанавливается при создании диалога или входящем сообщении. Очищается при первом ответе.",
    )
    first_reply_created_at = models.DateTimeField(
        "Время первого ответа оператора",
        null=True,
        blank=True,
        db_index=True,
        help_text="Используется для метрик времени первого ответа.",
    )
    contact_last_seen_at = models.DateTimeField(
        "Когда контакт последний раз видел диалог",
        null=True,
        blank=True,
    )
    agent_last_seen_at = models.DateTimeField(
        "Когда агент последний раз видел диалог",
        null=True,
        blank=True,
    )
    snoozed_until = models.DateTimeField(
        "Отложен до",
        null=True,
        blank=True,
    )
    identifier = models.CharField(
        "Идентификатор",
        max_length=255,
        blank=True,
        null=True,
        help_text="Идентификатор из внешней системы.",
    )
    additional_attributes = models.JSONField(
        "Дополнительные атрибуты",
        default=dict,
        blank=True,
        help_text="Метаданные: referer, browser, OS, IP и т.д.",
    )
    custom_attributes = models.JSONField(
        "Кастомные атрибуты",
        default=dict,
        blank=True,
        help_text="Кастомные атрибуты для гибкости.",
    )
    created_at = models.DateTimeField("Создано", auto_now_add=True, db_index=True)
    rating_score = models.PositiveSmallIntegerField(
        "Оценка (1–5 или 0–10 NPS)",
        null=True,
        blank=True,
        db_index=True,
        help_text="Оценка от контакта после закрытия диалога.",
    )
    rating_comment = models.TextField("Комментарий к оценке", blank=True, default="")
    rated_at = models.DateTimeField("Когда оценено", null=True, blank=True)
    labels = models.ManyToManyField(
        "messenger.ConversationLabel",
        verbose_name="Метки",
        blank=True,
        related_name="conversations",
    )

    class Meta:
        verbose_name = "Диалог"
        verbose_name_plural = "Диалоги"
        indexes = [
            # Базовые индексы
            models.Index(fields=["branch", "status"]),
            models.Index(fields=["created_at"]),
            models.Index(fields=["last_activity_at"]),
            models.Index(fields=["waiting_since"]),
            models.Index(fields=["first_reply_created_at"]),
            # Составные индексы для производительности (по образцу Chatwoot)
            models.Index(fields=["inbox", "status", "assignee"], name="msg_conv_inbox_st_assign_idx"),
            models.Index(fields=["status", "priority"], name="msg_conv_status_priority_idx"),
            models.Index(fields=["branch", "status", "assignee"], name="msg_conv_branch_st_assign_idx"),
            models.Index(fields=["contact", "inbox", "status"], name="msg_conv_cont_inbox_st_idx"),
            # Индекс для сортировки по waiting_since (уже есть в базовых, но добавляем для явности)
        ]

    def __str__(self) -> str:
        return f"Conversation #{self.pk} ({self.inbox})"

    def clean(self):
        """
        Инварианты безопасности:
        - для inbox с филиалом: branch диалога совпадает с inbox.branch;
        - для глобального inbox (inbox.branch_id is None): branch задаётся маршрутизацией при создании;
        - после создания диалога нельзя менять inbox (а значит, и branch).
        """
        if not self.inbox_id:
            raise ValidationError("Inbox обязателен для диалога.")
        # При наличии PK проверяем неизменяемость inbox.
        if self.pk:
            old = type(self).objects.only("inbox_id", "branch_id").get(pk=self.pk)
            if old.inbox_id != self.inbox_id:
                raise ValidationError("Нельзя изменить inbox существующего диалога.")
        inbox_branch_id = self.inbox.branch_id
        if inbox_branch_id is not None:
            # Inbox с филиалом: branch диалога должен совпадать с inbox.
            if self.branch_id != inbox_branch_id:
                raise ValidationError("Филиал диалога должен совпадать с филиалом inbox.")
        else:
            # Глобальный inbox: branch задаётся при создании из маршрутизации; должен быть заполнен.
            if not self.branch_id:
                raise ValidationError("Для глобального inbox филиал диалога должен быть задан из правил маршрутизации.")

    def save(self, *args, **kwargs):
        """
        Проставляет branch из inbox.branch для не-глобального inbox.
        Для глобального inbox (inbox.branch_id is None) branch не перезаписывается (устанавливается при создании).
        
        По образцу Chatwoot: устанавливает waiting_since при создании диалога и отправляет события.
        """
        is_new = self.pk is None
        old_status = None
        old_assignee_id = None
        
        # Сохраняем старые значения для событий
        if not is_new:
            try:
                old = type(self).objects.only("status", "assignee_id").get(pk=self.pk)
                old_status = old.status
                old_assignee_id = old.assignee_id
            except type(self).DoesNotExist:
                pass
        
        if self.inbox_id:
            inbox_branch_id = self.inbox.branch_id
            if self.pk:
                old = type(self).objects.only("inbox_id", "branch_id").get(pk=self.pk)
                if old.inbox_id != self.inbox_id:
                    raise ValidationError("Нельзя изменить inbox существующего диалога.")
                if inbox_branch_id is not None and old.branch_id != inbox_branch_id:
                    raise ValidationError("Нельзя изменить филиал существующего диалога.")
            if inbox_branch_id is not None:
                self.branch_id = inbox_branch_id
        
        # Устанавливаем waiting_since при создании (по образцу Chatwoot)
        if is_new and not self.waiting_since:
            self.waiting_since = timezone.now()
        
        # Инициализируем JSON поля если пустые
        if not self.additional_attributes:
            self.additional_attributes = {}
        if not self.custom_attributes:
            self.custom_attributes = {}
        
        super().save(*args, **kwargs)
        
        # Отправка событий через Event Dispatcher (по образцу Chatwoot)
        from .dispatchers import get_dispatcher, Events
        
        dispatcher = get_dispatcher()
        now = timezone.now()
        
        if is_new:
            # Событие создания диалога
            dispatcher.dispatch(
                Events.CONVERSATION_CREATED,
                now,
                {"conversation": self}
            )
        else:
            # События обновления
            if old_status != self.status:
                dispatcher.dispatch(
                    Events.CONVERSATION_STATUS_CHANGED,
                    now,
                    {"conversation": self, "old_status": old_status}
                )
                
                # Специфичные события по статусу
                if self.status == self.Status.OPEN:
                    dispatcher.dispatch(
                        Events.CONVERSATION_OPENED,
                        now,
                        {"conversation": self}
                    )
                elif self.status == self.Status.RESOLVED:
                    dispatcher.dispatch(
                        Events.CONVERSATION_RESOLVED,
                        now,
                        {"conversation": self}
                    )
                elif self.status == self.Status.CLOSED:
                    dispatcher.dispatch(
                        Events.CONVERSATION_CLOSED,
                        now,
                        {"conversation": self}
                    )
            
            if old_assignee_id != self.assignee_id:
                dispatcher.dispatch(
                    Events.ASSIGNEE_CHANGED,
                    now,
                    {"conversation": self, "old_assignee_id": old_assignee_id}
                )
            
            # Общее событие обновления
            dispatcher.dispatch(
                Events.CONVERSATION_UPDATED,
                now,
                {"conversation": self}
            )
    
    def last_activity_at_fallback(self):
        """
        Fallback на created_at если last_activity_at не задан (по образцу Chatwoot).
        """
        return self.last_activity_at or self.created_at


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
    body = models.TextField(
        "Текст сообщения",
        blank=True,
        default="",
        help_text="Максимум 150,000 символов (как в Chatwoot).",
    )
    processed_message_content = models.TextField(
        "Обработанный контент",
        blank=True,
        default="",
        help_text="Обработанный контент (после фильтрации, форматирования). Максимум 150,000 символов.",
    )
    content_attributes = models.JSONField(
        "Атрибуты контента",
        default=dict,
        blank=True,
        help_text="Структурированные данные: in_reply_to, deleted, translations и т.д.",
    )
    external_source_ids = models.JSONField(
        "ID внешних источников",
        default=dict,
        blank=True,
        help_text="ID во внешних системах (Slack, Telegram и т.д.).",
    )
    source_id = models.TextField(
        "ID источника",
        blank=True,
        null=True,
        db_index=True,
        help_text="ID источника для дедупликации.",
    )
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
    read_at = models.DateTimeField(
        "Прочитано (контакт)",
        null=True,
        blank=True,
        help_text="Для исходящих: когда контакт увидел сообщение (виджет).",
    )

    # Временный ID из фронтенда (не сохраняется в БД)
    echo_id = None
    
    # Лимиты (по образцу Chatwoot)
    NUMBER_OF_PERMITTED_ATTACHMENTS = 15
    MAX_CONTENT_LENGTH = 150000  # Максимум символов в сообщении
    MESSAGE_PER_MINUTE_LIMIT = 20  # Максимум сообщений в минуту на диалог

    class Meta:
        verbose_name = "Сообщение"
        verbose_name_plural = "Сообщения"
        ordering = ["created_at", "id"]
        indexes = [
            # Базовые индексы
            models.Index(fields=["conversation", "direction", "created_at"]),
            models.Index(fields=["source_id"]),
            # Составные индексы для производительности (по образцу Chatwoot)
            models.Index(fields=["sender_contact", "direction", "created_at"], name="msg_msg_cont_dir_crt_idx"),
            models.Index(fields=["sender_user", "direction", "created_at"], name="msg_msg_user_dir_crt_idx"),
        ]

    def __str__(self) -> str:
        return f"Message #{self.pk} in conv {self.conversation_id}"

    def clean(self):
        """
        Инварианты по direction/sender и защита от флуда (по образцу Chatwoot).
        """
        # Существующие валидации direction/sender
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
        
        # Защита от флуда (по образцу Chatwoot)
        if self.conversation_id:
            from datetime import timedelta
            recent_count = Message.objects.filter(
                conversation_id=self.conversation_id,
                created_at__gte=timezone.now() - timedelta(minutes=1)
            ).exclude(pk=self.pk if self.pk else None).count()
            if recent_count >= self.MESSAGE_PER_MINUTE_LIMIT:
                raise ValidationError("Too many messages")
        
        # Валидация длины контента
        if len(self.body) > self.MAX_CONTENT_LENGTH:
            raise ValidationError(f"Message content is too long (maximum is {self.MAX_CONTENT_LENGTH} characters)")
        if self.processed_message_content and len(self.processed_message_content) > self.MAX_CONTENT_LENGTH:
            raise ValidationError(f"Processed message content is too long (maximum is {self.MAX_CONTENT_LENGTH} characters)")
        
        # Валидация вложений (проверяется при сохранении через сигнал или в save())
    
    def save(self, *args, **kwargs):
        """
        Обновление processed_message_content и last_activity_at диалога (по образцу Chatwoot).
        Отправка событий через Event Dispatcher.
        """
        is_new = self.pk is None
        
        # Обработка контента
        if not self.processed_message_content and self.body:
            self.processed_message_content = self.body[:self.MAX_CONTENT_LENGTH]
        
        # Сохраняем created_at до super().save() для использования после сохранения
        created_at_before = self.created_at
        
        super().save(*args, **kwargs)
        
        # Используем created_at после сохранения (может быть установлен auto_now_add)
        created_at_used = self.created_at or timezone.now()
        
        # Обновить last_activity_at диалога с защитой от race condition (по образцу Chatwoot)
        # Используем update с F() для атомарного обновления, чтобы избежать race condition
        from django.db.models import F
        Conversation.objects.filter(pk=self.conversation_id).update(
            last_activity_at=created_at_used
        )
        
        # Обновить waiting_since логику (по образцу Chatwoot)
        self._update_waiting_since(created_at_used)
        
        # Обновить first_reply_created_at (по образцу Chatwoot)
        self._update_first_reply(created_at_used)
        
        # Отправка событий через Event Dispatcher (по образцу Chatwoot)
        from .dispatchers import get_dispatcher, Events
        
        dispatcher = get_dispatcher()
        now = timezone.now()
        
        if is_new:
            # Событие создания сообщения
            dispatcher.dispatch(
                Events.MESSAGE_CREATED,
                now,
                {"message": self}
            )
            
            # Проверка первого ответа (уже обработано в _update_first_reply)
            if self._is_human_response():
                conversation = self.conversation
                if conversation.first_reply_created_at == created_at_used:
                    dispatcher.dispatch(
                        Events.FIRST_REPLY_CREATED,
                        now,
                        {"message": self}
                    )
                
                if self.direction == self.Direction.OUT:
                    dispatcher.dispatch(
                        Events.REPLY_CREATED,
                        now,
                        {"message": self}
                    )
        else:
            # Событие обновления сообщения
            dispatcher.dispatch(
                Events.MESSAGE_UPDATED,
                now,
                {"message": self}
            )
    
    def _update_waiting_since(self, created_at_used):
        """Обновление waiting_since по образцу Chatwoot."""
        conversation = self.conversation
        
        if self.direction == self.Direction.IN:
            # Входящее сообщение: устанавливаем waiting_since если пусто
            if not conversation.waiting_since:
                Conversation.objects.filter(pk=conversation.pk).update(
                    waiting_since=created_at_used
                )
        elif self.direction == self.Direction.OUT:
            # Исходящее сообщение: очищаем waiting_since если это человеческий ответ
            if self._is_human_response() and conversation.waiting_since:
                Conversation.objects.filter(pk=conversation.pk).update(
                    waiting_since=None
                )
    
    def _is_human_response(self):
        """Проверка, что это человеческий ответ (по образцу Chatwoot)."""
        # Проверки:
        # 1. Исходящее сообщение
        # 2. От пользователя (не бот)
        # 3. Нет automation_rule_id в content_attributes
        if self.direction != self.Direction.OUT:
            return False
        
        if not self.sender_user_id:
            return False
        
        # Проверка на automation_rule_id (если будет)
        if self.content_attributes and self.content_attributes.get('automation_rule_id'):
            return False
        
        return True
    
    def _update_first_reply(self, created_at_used):
        """Обновление first_reply_created_at по образцу Chatwoot."""
        if not self._is_human_response():
            return
        
        conversation = self.conversation
        
        # Проверяем, что это первый ответ
        if conversation.first_reply_created_at:
            return
        
        # Проверяем, что нет других исходящих сообщений от пользователей
        other_outgoing = Message.objects.filter(
            conversation=conversation,
            direction=self.Direction.OUT,
            sender_user__isnull=False,
        ).exclude(pk=self.pk).exists()
        
        if not other_outgoing:
            Conversation.objects.filter(pk=conversation.pk).update(
                first_reply_created_at=created_at_used,
                waiting_since=None  # Очищаем waiting_since
            )


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
        
        # Валидация лимита вложений (по образцу Chatwoot)
        if self.message_id:
            attachment_count = MessageAttachment.objects.filter(
                message_id=self.message_id
            ).exclude(pk=self.pk if self.pk else None).count()
            if attachment_count >= Message.NUMBER_OF_PERMITTED_ATTACHMENTS:
                from django.core.exceptions import ValidationError
                raise ValidationError(
                    f"Too many attachments (maximum {Message.NUMBER_OF_PERMITTED_ATTACHMENTS})"
                )
        
        super().save(*args, **kwargs)


class ContactInbox(models.Model):
    """
    Связь контакта с конкретным inbox (по образцу Chatwoot).
    
    Один контакт может быть в нескольких inbox (мультитенантность).
    Хранит source_id (идентификатор контакта в inbox) и pubsub_token (для WebSocket).
    """
    
    contact = models.ForeignKey(
        Contact,
        verbose_name="Контакт",
        on_delete=models.CASCADE,
        related_name="contact_inboxes",
    )
    
    inbox = models.ForeignKey(
        Inbox,
        verbose_name="Inbox",
        on_delete=models.CASCADE,
        related_name="contact_inboxes",
    )
    
    source_id = models.TextField(
        "ID источника",
        help_text="Идентификатор контакта в inbox (например, visitor_id для виджета).",
    )
    
    pubsub_token = models.CharField(
        "PubSub токен",
        max_length=64,
        unique=True,
        blank=True,
        help_text="Токен для WebSocket подключения (генерируется автоматически).",
    )
    
    created_at = models.DateTimeField("Создано", auto_now_add=True)
    
    class Meta:
        verbose_name = "Связь контакта с inbox"
        verbose_name_plural = "Связи контактов с inbox"
        unique_together = [('inbox', 'source_id')]
        indexes = [
            models.Index(fields=['inbox', 'source_id']),
            models.Index(fields=['pubsub_token']),
        ]
    
    def save(self, *args, **kwargs):
        """Генерировать pubsub_token автоматически (по образцу Chatwoot)."""
        if not self.pubsub_token:
            self.pubsub_token = secrets.token_urlsafe(32)
        super().save(*args, **kwargs)
    
    def __str__(self) -> str:
        return f"{self.contact} / {self.inbox}"


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


class ConversationLabel(models.Model):
    """Метка/тег для диалога (по образцу Chatwoot labels)."""

    COLORS = [
        ("#EF4444", "Красный"),
        ("#F59E0B", "Жёлтый"),
        ("#10B981", "Зелёный"),
        ("#3B82F6", "Синий"),
        ("#8B5CF6", "Фиолетовый"),
        ("#EC4899", "Розовый"),
        ("#6B7280", "Серый"),
    ]

    title = models.CharField("Название", max_length=64, unique=True)
    color = models.CharField("Цвет", max_length=7, default="#3B82F6")
    created_at = models.DateTimeField("Создано", auto_now_add=True)

    class Meta:
        verbose_name = "Метка диалога"
        verbose_name_plural = "Метки диалогов"
        ordering = ["title"]

    def __str__(self) -> str:
        return self.title


class AgentProfile(models.Model):
    class Status(models.TextChoices):
        ONLINE = "online", "Онлайн"
        AWAY = "away", "Отошёл"
        BUSY = "busy", "Занят"
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


class PushSubscription(models.Model):
    """
    Browser Push подписки операторов (Web Push API + VAPID).
    Аналог Chatwoot notification_subscriptions.
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="push_subscriptions",
    )
    endpoint = models.URLField("Push endpoint", max_length=500, unique=True)
    p256dh = models.CharField("p256dh key", max_length=200)
    auth = models.CharField("Auth secret", max_length=100)
    created_at = models.DateTimeField("Создано", auto_now_add=True)
    is_active = models.BooleanField("Активна", default=True)

    class Meta:
        verbose_name = "Push-подписка"
        verbose_name_plural = "Push-подписки"
        indexes = [
            models.Index(fields=["user", "is_active"]),
        ]

    def __str__(self) -> str:
        return f"Push: {self.user} ({self.endpoint[:50]}...)"


class Campaign(models.Model):
    """
    Проактивные кампании (аналог Chatwoot ongoing campaigns).
    Показывают сообщение посетителю после N секунд на странице с URL-match.
    Вся логика триггера — на клиенте (как у Chatwoot).
    """
    class Status(models.TextChoices):
        ACTIVE = "active", "Активна"
        DISABLED = "disabled", "Отключена"

    inbox = models.ForeignKey(
        Inbox, on_delete=models.CASCADE, related_name="campaigns",
    )
    title = models.CharField("Название", max_length=255)
    message = models.TextField("Сообщение")
    url_pattern = models.CharField(
        "URL-паттерн", max_length=500, blank=True, default="*",
        help_text="Wildcard-паттерн URL страницы (например, */pricing*)",
    )
    time_on_page = models.PositiveIntegerField(
        "Секунд на странице", default=10,
        help_text="Через сколько секунд показать сообщение",
    )
    status = models.CharField(
        "Статус", max_length=16, choices=Status.choices, default=Status.ACTIVE,
    )
    only_during_business_hours = models.BooleanField(
        "Только в рабочее время", default=False,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Кампания"
        verbose_name_plural = "Кампании"
        indexes = [
            models.Index(fields=["inbox", "status"]),
        ]

    def __str__(self) -> str:
        return self.title


class AutomationRule(models.Model):
    """
    Правила автоматизации (аналог Chatwoot automation_rules).
    Event-driven: при событии проверяются условия → выполняются действия.
    """
    class EventName(models.TextChoices):
        CONVERSATION_CREATED = "conversation_created", "Диалог создан"
        MESSAGE_CREATED = "message_created", "Сообщение создано"
        CONVERSATION_UPDATED = "conversation_updated", "Диалог обновлён"

    inbox = models.ForeignKey(
        Inbox, on_delete=models.CASCADE, related_name="automation_rules",
        null=True, blank=True,
        help_text="Если пусто — правило для всех инбоксов",
    )
    name = models.CharField("Название", max_length=255)
    description = models.TextField("Описание", blank=True, default="")
    event_name = models.CharField(
        "Событие", max_length=32, choices=EventName.choices,
    )
    conditions = models.JSONField(
        "Условия", default=list,
        help_text='[{"attribute_key": "status", "filter_operator": "equal_to", "values": ["open"]}]',
    )
    actions = models.JSONField(
        "Действия", default=list,
        help_text='[{"action_name": "assign_agent", "action_params": [42]}]',
    )
    is_active = models.BooleanField("Активно", default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Правило автоматизации"
        verbose_name_plural = "Правила автоматизации"
        indexes = [
            models.Index(fields=["event_name", "is_active"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.event_name})"


class ReportingEvent(models.Model):
    """
    События для аналитики (аналог Chatwoot reporting_events).
    Создаются при ключевых событиях для агрегации в дашборде.
    """
    class EventType(models.TextChoices):
        FIRST_RESPONSE = "first_response", "Первый ответ"
        REPLY_TIME = "reply_time", "Время ответа"
        CONVERSATION_RESOLVED = "conversation_resolved", "Решён"
        CONVERSATION_OPENED = "conversation_opened", "Открыт"

    name = models.CharField("Тип", max_length=32, choices=EventType.choices)
    value = models.FloatField("Значение (секунды)", default=0)
    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name="reporting_events",
        null=True, blank=True,
    )
    inbox = models.ForeignKey(
        Inbox, on_delete=models.SET_NULL, null=True, blank=True,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True,
        help_text="Оператор (для метрик по агентам)",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Событие аналитики"
        verbose_name_plural = "События аналитики"
        indexes = [
            models.Index(fields=["name", "created_at"]),
            models.Index(fields=["name", "inbox", "created_at"]),
            models.Index(fields=["name", "user", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.name}: {self.value:.1f}s"


class Macro(models.Model):
    """
    Макросы оператора (аналог Chatwoot macros).
    Один клик = несколько действий (назначить + метка + ответ + статус).
    Могут быть личные (user) или общие (user=null).
    """
    name = models.CharField("Название", max_length=255)
    actions = models.JSONField(
        "Действия", default=list,
        help_text='[{"action_name": "assign_agent", "action_params": [42]}, {"action_name": "send_message", "action_params": ["Спасибо!"]}]',
    )
    visibility = models.CharField(
        "Видимость", max_length=16,
        choices=[("personal", "Личный"), ("global", "Общий")],
        default="personal",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="messenger_macros",
        null=True, blank=True,
        help_text="Владелец макроса (null = общий)",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Макрос"
        verbose_name_plural = "Макросы"
        indexes = [
            models.Index(fields=["user", "visibility"]),
        ]

    def __str__(self) -> str:
        return self.name


class ConversationTransfer(models.Model):
    """Лог передачи диалога между операторами/филиалами."""

    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="transfers",
        verbose_name="Диалог",
    )
    from_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="messenger_transfers_from",
        verbose_name="От кого",
    )
    to_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="messenger_transfers_to",
        verbose_name="Кому",
    )
    from_branch = models.ForeignKey(
        "accounts.Branch",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="messenger_transfers_out",
        verbose_name="Филиал-источник",
    )
    to_branch = models.ForeignKey(
        "accounts.Branch",
        on_delete=models.PROTECT,
        related_name="messenger_transfers_in",
        verbose_name="Филиал-получатель",
    )
    reason = models.TextField("Причина передачи")
    cross_branch = models.BooleanField("Межфилиальная передача", default=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "Передача диалога"
        verbose_name_plural = "Передачи диалогов"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["conversation", "-created_at"]),
            models.Index(fields=["to_user", "-created_at"]),
        ]

    def __str__(self):
        return f"Transfer #{self.pk}: conv={self.conversation_id} {self.from_user_id}→{self.to_user_id}"
