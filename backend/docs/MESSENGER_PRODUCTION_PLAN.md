# План доведения мессенджера до production-ready состояния

**Основа:** Решения Chatwoot как эталон для всех улучшений.

**Цель:** Довести live-chat до состояния, когда он работает слаженно, грамотно и правильно, как в Chatwoot.

---

## Принципы реализации

1. **Опираемся на Chatwoot** — используем его методы и функции как эталон
2. **Критичность важнее** — сначала критичные улучшения, потом важные
3. **Тестируем после каждого шага** — не накапливаем технический долг
4. **Миграции данных** — аккуратно переносим существующие данные

---

## Фаза 1: Критические улучшения моделей (Приоритет 🔴)

### Задача 1.1: Добавить поля в Conversation

**По образцу Chatwoot:** `app/models/conversation.rb`

#### Шаг 1: Создать миграцию

```python
# backend/messenger/migrations/XXXX_add_critical_fields_to_conversation.py

from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        ('messenger', 'XXXX_previous_migration'),
    ]

    operations = [
        # Переименовать last_message_at → last_activity_at
        migrations.RenameField(
            model_name='conversation',
            old_name='last_message_at',
            new_name='last_activity_at',
        ),
        
        # Добавить waiting_since
        migrations.AddField(
            model_name='conversation',
            name='waiting_since',
            field=models.DateTimeField(
                null=True,
                blank=True,
                db_index=True,
                help_text="Когда диалог начал ждать ответа. Очищается при первом ответе оператора.",
            ),
        ),
        
        # Добавить first_reply_created_at
        migrations.AddField(
            model_name='conversation',
            name='first_reply_created_at',
            field=models.DateTimeField(
                null=True,
                blank=True,
                db_index=True,
                help_text="Время первого ответа оператора. Используется для метрик.",
            ),
        ),
        
        # Добавить contact_last_seen_at
        migrations.AddField(
            model_name='conversation',
            name='contact_last_seen_at',
            field=models.DateTimeField(
                null=True,
                blank=True,
                help_text="Когда контакт последний раз видел диалог (для виджета).",
            ),
        ),
        
        # Добавить agent_last_seen_at
        migrations.AddField(
            model_name='conversation',
            name='agent_last_seen_at',
            field=models.DateTimeField(
                null=True,
                blank=True,
                help_text="Когда агент (не назначенный) последний раз видел диалог.",
            ),
        ),
        
        # Добавить snoozed_until
        migrations.AddField(
            model_name='conversation',
            name='snoozed_until',
            field=models.DateTimeField(
                null=True,
                blank=True,
                help_text="Отложен до указанного времени.",
            ),
        ),
        
        # Добавить identifier
        migrations.AddField(
            model_name='conversation',
            name='identifier',
            field=models.CharField(
                max_length=255,
                blank=True,
                null=True,
                help_text="Идентификатор из внешней системы.",
            ),
        ),
        
        # Добавить additional_attributes (JSONB)
        migrations.AddField(
            model_name='conversation',
            name='additional_attributes',
            field=models.JSONField(
                default=dict,
                blank=True,
                help_text="Метаданные: referer, browser, OS, IP и т.д.",
            ),
        ),
        
        # Добавить custom_attributes (JSONB)
        migrations.AddField(
            model_name='conversation',
            name='custom_attributes',
            field=models.JSONField(
                default=dict,
                blank=True,
                help_text="Кастомные атрибуты для гибкости.",
            ),
        ),
        
        # Миграция данных: заполнить last_activity_at из last_message_at
        migrations.RunPython(
            code=migrate_last_message_to_activity,
            reverse_code=migrations.RunPython.noop,
        ),
        
        # Миграция данных: установить waiting_since для открытых диалогов
        migrations.RunPython(
            code=set_waiting_since_for_open,
            reverse_code=migrations.RunPython.noop,
        ),
    ]


def migrate_last_message_to_activity(apps, schema_editor):
    """Заполнить last_activity_at из last_message_at."""
    Conversation = apps.get_model('messenger', 'Conversation')
    Conversation.objects.filter(
        last_activity_at__isnull=True
    ).update(
        last_activity_at=models.F('last_message_at')
    )


def set_waiting_since_for_open(apps, schema_editor):
    """Установить waiting_since для открытых диалогов без назначения."""
    Conversation = apps.get_model('messenger', 'Conversation')
    from django.utils import timezone
    Conversation.objects.filter(
        status__in=['open', 'pending'],
        assignee_id__isnull=True,
        waiting_since__isnull=True
    ).update(
        waiting_since=models.F('created_at')
    )
```

#### Шаг 2: Обновить модель

```python
# backend/messenger/models.py

class Conversation(models.Model):
    # ... существующие поля ...
    
    # Переименовано из last_message_at
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
    )
    
    additional_attributes = models.JSONField(
        "Дополнительные атрибуты",
        default=dict,
        blank=True,
    )
    
    custom_attributes = models.JSONField(
        "Кастомные атрибуты",
        default=dict,
        blank=True,
    )
    
    def last_activity_at(self):
        """Fallback на created_at если не задан (по образцу Chatwoot)."""
        return self.last_activity_at or self.created_at
```

#### Шаг 3: Обновить логику создания диалога

```python
# backend/messenger/services.py или в модели Conversation

def save(self, *args, **kwargs):
    # Устанавливаем waiting_since при создании (по образцу Chatwoot)
    if not self.pk:
        self.waiting_since = self.created_at or timezone.now()
    
    super().save(*args, **kwargs)
```

---

### Задача 1.2: Добавить поля в Message

**По образцу Chatwoot:** `app/models/message.rb`

#### Шаг 1: Создать миграцию

```python
# backend/messenger/migrations/XXXX_add_critical_fields_to_message.py

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('messenger', 'XXXX_previous_migration'),
    ]

    operations = [
        # Добавить processed_message_content
        migrations.AddField(
            model_name='message',
            name='processed_message_content',
            field=models.TextField(
                blank=True,
                default="",
                help_text="Обработанный контент (после фильтрации, форматирования).",
            ),
        ),
        
        # Добавить content_attributes (JSON)
        migrations.AddField(
            model_name='message',
            name='content_attributes',
            field=models.JSONField(
                default=dict,
                blank=True,
                help_text="Структурированные данные: in_reply_to, deleted, translations и т.д.",
            ),
        ),
        
        # Добавить external_source_ids (JSON)
        migrations.AddField(
            model_name='message',
            name='external_source_ids',
            field=models.JSONField(
                default=dict,
                blank=True,
                help_text="ID во внешних системах (Slack, Telegram и т.д.).",
            ),
        ),
        
        # Добавить source_id
        migrations.AddField(
            model_name='message',
            name='source_id',
            field=models.TextField(
                blank=True,
                null=True,
                db_index=True,
                help_text="ID источника для дедупликации.",
            ),
        ),
    ]
```

#### Шаг 2: Обновить модель с валидациями

```python
# backend/messenger/models.py

from django.core.validators import MaxLengthValidator
from django.core.exceptions import ValidationError
from django.utils import timezone
from datetime import timedelta

class Message(models.Model):
    # ... существующие поля ...
    
    # Максимум 150,000 символов (как в Chatwoot)
    body = models.TextField(
        "Текст сообщения",
        blank=True,
        default="",
        validators=[MaxLengthValidator(150000)],
    )
    
    processed_message_content = models.TextField(
        "Обработанный контент",
        blank=True,
        default="",
        validators=[MaxLengthValidator(150000)],
    )
    
    content_attributes = models.JSONField(
        "Атрибуты контента",
        default=dict,
        blank=True,
    )
    
    external_source_ids = models.JSONField(
        "ID внешних источников",
        default=dict,
        blank=True,
    )
    
    source_id = models.TextField(
        "ID источника",
        blank=True,
        null=True,
        db_index=True,
    )
    
    # Временный ID из фронтенда (не сохраняется в БД)
    echo_id = None
    
    NUMBER_OF_PERMITTED_ATTACHMENTS = 15  # Как в Chatwoot
    
    def clean(self):
        """Валидации по образцу Chatwoot."""
        # Существующие валидации direction/sender
        super().clean()
        
        # Защита от флуда (по образцу Chatwoot)
        if self.conversation_id:
            recent_count = Message.objects.filter(
                conversation_id=self.conversation_id,
                created_at__gte=timezone.now() - timedelta(minutes=1)
            ).count()
            if recent_count >= 20:  # CONVERSATION_MESSAGE_PER_MINUTE_LIMIT
                raise ValidationError("Too many messages")
        
        # Валидация вложений
        if self.pk:
            attachment_count = self.attachments.count()
        else:
            # Для нового сообщения считаем из content_attributes или attachments
            attachment_count = len(self.content_attributes.get('attachments', []))
        
        if attachment_count >= self.NUMBER_OF_PERMITTED_ATTACHMENTS:
            raise ValidationError(f"Too many attachments (maximum {self.NUMBER_OF_PERMITTED_ATTACHMENTS})")
    
    def save(self, *args, **kwargs):
        """Обновление processed_message_content и last_activity_at диалога."""
        # Обработка контента (по образцу Chatwoot)
        if not self.processed_message_content:
            self.processed_message_content = self.body[:150000] if self.body else ""
        
        super().save(*args, **kwargs)
        
        # Обновить last_activity_at диалога (по образцу Chatwoot)
        Conversation.objects.filter(pk=self.conversation_id).update(
            last_activity_at=self.created_at
        )
        
        # Обновить waiting_since логику (по образцу Chatwoot)
        self._update_waiting_since()
        
        # Обновить first_reply_created_at (по образцу Chatwoot)
        self._update_first_reply()
    
    def _update_waiting_since(self):
        """Обновление waiting_since по образцу Chatwoot."""
        conversation = self.conversation
        
        if self.direction == self.Direction.IN:
            # Входящее сообщение: устанавливаем waiting_since если пусто
            if not conversation.waiting_since:
                conversation.waiting_since = self.created_at
                conversation.save(update_fields=['waiting_since'])
        elif self.direction == self.Direction.OUT:
            # Исходящее сообщение: очищаем waiting_since
            # Проверяем, что это человеческий ответ (не бот, не campaign)
            if self._is_human_response() and conversation.waiting_since:
                conversation.waiting_since = None
                conversation.save(update_fields=['waiting_since'])
    
    def _is_human_response(self):
        """Проверка, что это человеческий ответ (по образцу Chatwoot)."""
        # Проверки:
        # 1. Исходящее сообщение
        # 2. От пользователя (не бот)
        # 3. Нет automation_rule_id в content_attributes
        # 4. Нет campaign_id в additional_attributes (если будет)
        if self.direction != self.Direction.OUT:
            return False
        
        if not self.sender_user_id:
            return False
        
        # Проверка на automation_rule_id (если будет)
        if self.content_attributes.get('automation_rule_id'):
            return False
        
        return True
    
    def _update_first_reply(self):
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
            conversation.first_reply_created_at = self.created_at
            conversation.waiting_since = None  # Очищаем waiting_since
            conversation.save(update_fields=['first_reply_created_at', 'waiting_since'])
```

---

### Задача 1.3: Добавить поля в Contact

**По образцу Chatwoot:** `app/models/contact.rb`

#### Шаг 1: Создать миграцию

```python
# backend/messenger/migrations/XXXX_add_fields_to_contact.py

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('messenger', 'XXXX_previous_migration'),
    ]

    operations = [
        # Добавить last_activity_at
        migrations.AddField(
            model_name='contact',
            name='last_activity_at',
            field=models.DateTimeField(
                null=True,
                blank=True,
                db_index=True,
                help_text="Последняя активность контакта.",
            ),
        ),
        
        # Добавить blocked
        migrations.AddField(
            model_name='contact',
            name='blocked',
            field=models.BooleanField(
                default=False,
                db_index=True,
                help_text="Заблокированный контакт.",
            ),
        ),
    ]
```

#### Шаг 2: Обновить модель

```python
# backend/messenger/models.py

class Contact(models.Model):
    # ... существующие поля ...
    
    last_activity_at = models.DateTimeField(
        "Последняя активность",
        null=True,
        blank=True,
        db_index=True,
    )
    
    blocked = models.BooleanField(
        "Заблокирован",
        default=False,
        db_index=True,
    )
    
    def clean(self):
        """Валидации по образцу Chatwoot."""
        # Email: case-insensitive уникальность (если нужна мультитенантность)
        # Phone: формат E.164 (если нужна валидация)
        super().clean()
```

#### Шаг 3: Обновить логику создания сообщений

```python
# backend/messenger/services.py

def record_message(...):
    """Обновить last_activity_at контакта при входящем сообщении."""
    msg = Message.objects.create(...)
    
    # Обновить last_activity_at контакта (по образцу Chatwoot)
    if sender_contact:
        Contact.objects.filter(pk=sender_contact.pk).update(
            last_activity_at=timezone.now()
        )
    
    return msg
```

---

### Задача 1.4: Создать модель ContactInbox

**По образцу Chatwoot:** `app/models/contact_inbox.rb`

#### Шаг 1: Создать модель и миграцию

```python
# backend/messenger/models.py

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
        """Генерировать pubsub_token автоматически."""
        if not self.pubsub_token:
            self.pubsub_token = secrets.token_urlsafe(32)
        super().save(*args, **kwargs)
```

#### Шаг 2: Миграция данных

```python
# backend/messenger/migrations/XXXX_create_contact_inbox.py

def migrate_existing_conversations(apps, schema_editor):
    """Создать ContactInbox для существующих диалогов."""
    Conversation = apps.get_model('messenger', 'Conversation')
    ContactInbox = apps.get_model('messenger', 'ContactInbox')
    import secrets
    
    for conv in Conversation.objects.select_related('contact', 'inbox'):
        # Используем external_id как source_id, или генерируем новый
        source_id = conv.contact.external_id or f"contact_{conv.contact_id}"
        
        ContactInbox.objects.get_or_create(
            contact=conv.contact,
            inbox=conv.inbox,
            defaults={
                'source_id': source_id,
                'pubsub_token': secrets.token_urlsafe(32),
            }
        )
```

---

## Фаза 2: Индексы и производительность (Приоритет 🔴)

### Задача 2.1: Добавить составные индексы

**По образцу Chatwoot:** индексы из `db/migrate/`

#### Шаг 1: Создать миграцию индексов

```python
# backend/messenger/migrations/XXXX_add_composite_indexes.py

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('messenger', 'XXXX_previous_migration'),
    ]

    operations = [
        # Критически важный индекс для списка диалогов
        migrations.AddIndex(
            model_name='conversation',
            index=models.Index(
                fields=['inbox', 'status', 'assignee'],
                name='messenger_conv_inbox_status_assignee_idx',
            ),
        ),
        
        # Индекс для сообщений диалога
        migrations.AddIndex(
            model_name='message',
            index=models.Index(
                fields=['conversation', 'direction', 'created_at'],
                name='messenger_msg_conv_dir_created_idx',
            ),
        ),
        
        # Индекс для статуса и приоритета
        migrations.AddIndex(
            model_name='conversation',
            index=models.Index(
                fields=['status', 'priority'],
                name='messenger_conv_status_priority_idx',
            ),
        ),
        
        # Индекс для waiting_since (после добавления поля)
        migrations.AddIndex(
            model_name='conversation',
            index=models.Index(
                fields=['waiting_since'],
                name='messenger_conv_waiting_since_idx',
            ),
        ),
        
        # Индекс для first_reply_created_at (после добавления поля)
        migrations.AddIndex(
            model_name='conversation',
            index=models.Index(
                fields=['first_reply_created_at'],
                name='messenger_conv_first_reply_idx',
            ),
        ),
    ]
```

---

## Фаза 3: Сервисы и бизнес-логика (Приоритет 🔴)

### Задача 3.1: Переделать Round-Robin на Redis список

**По образцу Chatwoot:** `app/services/auto_assignment/inbox_round_robin_service.rb`

#### Шаг 1: Создать сервис

```python
# backend/messenger/services/round_robin.py

from typing import Optional, List
from django.core.cache import cache
from django.conf import settings
from accounts.models import User
from .models import Inbox


class InboxRoundRobinService:
    """
    Round-Robin сервис для автоназначения (по образцу Chatwoot).
    
    Хранит очередь операторов в Redis как список.
    При назначении оператор перемещается в конец очереди.
    """
    
    ROUND_ROBIN_KEY_PREFIX = "messenger:rr:queue"
    TTL = 60 * 60 * 24 * 7  # 7 дней
    
    def __init__(self, inbox: Inbox):
        self.inbox = inbox
        self.round_robin_key = f"{self.ROUND_ROBIN_KEY_PREFIX}:{inbox.id}"
    
    def clear_queue(self):
        """Очистить очередь (при удалении inbox)."""
        cache.delete(self.round_robin_key)
    
    def add_agent_to_queue(self, user_id: int):
        """Добавить оператора в очередь (при добавлении в inbox)."""
        queue = self._get_queue()
        if user_id not in queue:
            queue.append(user_id)
            self._save_queue(queue)
    
    def remove_agent_from_queue(self, user_id: int):
        """Удалить оператора из очереди (при удалении из inbox)."""
        queue = self._get_queue()
        if user_id in queue:
            queue.remove(user_id)
            self._save_queue(queue)
    
    def reset_queue(self, member_ids: List[int]):
        """Сбросить очередь и заполнить новыми операторами."""
        self.clear_queue()
        for user_id in member_ids:
            self.add_agent_to_queue(user_id)
    
    def available_agent(self, allowed_agent_ids: List[int]) -> Optional[User]:
        """
        Получить следующего доступного оператора из очереди.
        
        Args:
            allowed_agent_ids: Список ID операторов, из которых можно выбирать
                              (например, только онлайн операторы)
        
        Returns:
            User или None
        """
        # Валидация очереди
        if not self._validate_queue():
            # Очередь не соответствует текущим членам — пересоздать
            from .models import InboxMember  # Если будет модель
            # Или получить из настроек inbox
            member_ids = self._get_current_member_ids()
            self.reset_queue(member_ids)
        
        queue = self._get_queue()
        
        # Пересечение очереди и allowed_agent_ids
        available_ids = [uid for uid in queue if uid in allowed_agent_ids]
        
        if not available_ids:
            return None
        
        # Берём первого из доступных
        user_id = available_ids[0]
        
        # Перемещаем в конец очереди (по образцу Chatwoot)
        self._pop_push_to_queue(user_id)
        
        try:
            return User.objects.get(id=user_id)
        except User.DoesNotExist:
            return None
    
    def _get_queue(self) -> List[int]:
        """Получить очередь из Redis."""
        queue = cache.get(self.round_robin_key, [])
        return [int(x) for x in queue] if queue else []
    
    def _save_queue(self, queue: List[int]):
        """Сохранить очередь в Redis."""
        cache.set(self.round_robin_key, queue, timeout=self.TTL)
    
    def _pop_push_to_queue(self, user_id: int):
        """Переместить оператора в конец очереди."""
        queue = self._get_queue()
        if user_id in queue:
            queue.remove(user_id)
        queue.append(user_id)
        self._save_queue(queue)
    
    def _validate_queue(self) -> bool:
        """Проверить, соответствует ли очередь текущим членам inbox."""
        current_member_ids = set(self._get_current_member_ids())
        queue_ids = set(self._get_queue())
        return current_member_ids == queue_ids
    
    def _get_current_member_ids(self) -> List[int]:
        """Получить список ID текущих членов inbox."""
        # TODO: Реализовать получение членов inbox
        # Пока используем всех пользователей филиала inbox
        if self.inbox.branch_id:
            return list(
                User.objects.filter(
                    branch_id=self.inbox.branch_id,
                    is_active=True,
                ).exclude(role=User.Role.ADMIN).values_list('id', flat=True)
            )
        return []
```

#### Шаг 2: Обновить auto_assign_conversation

```python
# backend/messenger/services.py

from .services.round_robin import InboxRoundRobinService

def auto_assign_conversation(conversation: Conversation) -> Optional[User]:
    """
    Автоназначение через Round-Robin список (по образцу Chatwoot).
    """
    from django.db.models import Q, Count
    from .models import AgentProfile
    
    branch_id = conversation.branch_id
    inbox_id = conversation.inbox_id
    open_statuses = [Conversation.Status.OPEN, Conversation.Status.PENDING]
    
    # Кандидаты: только онлайн операторы
    candidates_qs = (
        User.objects.filter(
            branch_id=branch_id,
            is_active=True,
        )
        .exclude(role=User.Role.ADMIN)
        .exclude(
            Q(agent_profile__status=AgentProfile.Status.AWAY)
            | Q(agent_profile__status=AgentProfile.Status.BUSY)
            | Q(agent_profile__status=AgentProfile.Status.OFFLINE)
        )
        .annotate(
            open_count=Count(
                "assigned_conversations",
                filter=Q(assigned_conversations__status__in=open_statuses),
                distinct=True,
            )
        )
        .order_by("open_count", "id")
    )
    
    allowed_agent_ids = list(candidates_qs.values_list("id", flat=True))
    
    if not allowed_agent_ids:
        return None
    
    # Round-Robin через Redis список
    round_robin_service = InboxRoundRobinService(conversation.inbox)
    assignee = round_robin_service.available_agent(allowed_agent_ids)
    
    if assignee:
        now = timezone.now()
        conversation.assignee_id = assignee.id
        conversation.assignee_assigned_at = now
        conversation.assignee_opened_at = None
        conversation.waiting_since = conversation.waiting_since or now
        conversation.save(update_fields=[
            "assignee_id", "assignee_assigned_at", "assignee_opened_at", "waiting_since"
        ])
        return assignee
    
    return None
```

---

### Задача 3.2: Добавить Rate Limiter для назначений

**По образцу Chatwoot:** `app/services/auto_assignment/rate_limiter.rb`

#### Шаг 1: Создать Rate Limiter

```python
# backend/messenger/services/rate_limiter.py

from typing import Optional
from django.core.cache import cache
from django.utils import timezone
from datetime import timedelta
from accounts.models import User
from .models import Inbox


class AssignmentRateLimiter:
    """
    Rate Limiter для автоназначения (по образцу Chatwoot).
    
    Ограничивает количество назначений оператору за период времени.
    """
    
    ASSIGNMENT_KEY_PATTERN = "messenger:assignment:{inbox_id}:{agent_id}:{conversation_id}"
    
    def __init__(self, inbox: Inbox, agent: User):
        self.inbox = inbox
        self.agent = agent
    
    def within_limit(self, limit: Optional[int] = None, window_hours: int = 24) -> bool:
        """
        Проверить, не превышен ли лимит назначений.
        
        Args:
            limit: Лимит назначений (если None — без лимита)
            window_hours: Окно времени в часах
        
        Returns:
            True если в пределах лимита, False если превышен
        """
        if limit is None or limit <= 0:
            return True
        
        current_count = self.current_count(window_hours)
        return current_count < limit
    
    def track_assignment(self, conversation_id: int, window_hours: int = 24):
        """Отследить назначение диалога оператору."""
        assignment_key = self._build_assignment_key(conversation_id)
        cache.set(
            assignment_key,
            conversation_id,
            timeout=window_hours * 3600  # TTL в секундах
        )
    
    def current_count(self, window_hours: int = 24) -> int:
        """Подсчитать текущее количество назначений за окно времени."""
        pattern = self._assignment_key_pattern()
        # Подсчёт ключей с таким паттерном
        # В Redis можно использовать SCAN или хранить счётчик отдельно
        # Упрощённая версия: подсчёт через ключи
        count = 0
        # TODO: Реализовать подсчёт через Redis SCAN или отдельный счётчик
        return count
    
    def _build_assignment_key(self, conversation_id: int) -> str:
        """Построить ключ для назначения."""
        return self.ASSIGNMENT_KEY_PATTERN.format(
            inbox_id=self.inbox.id,
            agent_id=self.agent.id,
            conversation_id=conversation_id
        )
    
    def _assignment_key_pattern(self) -> str:
        """Паттерн для поиска ключей назначений."""
        return f"messenger:assignment:{self.inbox.id}:{self.agent.id}:*"
```

#### Шаг 2: Интегрировать в auto_assign_conversation

```python
# backend/messenger/services.py

from .services.rate_limiter import AssignmentRateLimiter

def auto_assign_conversation(conversation: Conversation) -> Optional[User]:
    # ... получение кандидатов ...
    
    # Round-Robin через Redis список
    round_robin_service = InboxRoundRobinService(conversation.inbox)
    
    # Проверяем rate limit для каждого кандидата
    for agent_id in allowed_agent_ids:
        agent = User.objects.get(id=agent_id)
        rate_limiter = AssignmentRateLimiter(conversation.inbox, agent)
        
        # Проверяем лимит (настраивается через settings или AssignmentPolicy)
        limit = getattr(settings, 'MESSENGER_ASSIGNMENT_RATE_LIMIT', None)
        if not rate_limiter.within_limit(limit=limit):
            continue  # Пропускаем этого оператора
        
        assignee = round_robin_service.available_agent([agent_id])
        if assignee:
            # Отслеживаем назначение
            rate_limiter.track_assignment(conversation.id)
            
            # Назначаем диалог
            # ... остальная логика ...
            return assignee
    
    return None
```

---

## Фаза 4: Real-time коммуникация (Приоритет 🟡)

### Задача 4.1: Реализовать Event Dispatcher

**По образцу Chatwoot:** `app/dispatchers/`

#### Шаг 1: Создать Event Dispatcher

```python
# backend/messenger/dispatchers.py

from typing import Dict, Any, Callable
from django.utils import timezone
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


# События (по образцу Chatwoot)
class Events:
    # Conversation события
    CONVERSATION_CREATED = "conversation.created"
    CONVERSATION_UPDATED = "conversation.updated"
    CONVERSATION_OPENED = "conversation.opened"
    CONVERSATION_RESOLVED = "conversation.resolved"
    CONVERSATION_STATUS_CHANGED = "conversation.status_changed"
    ASSIGNEE_CHANGED = "assignee.changed"
    
    # Message события
    MESSAGE_CREATED = "message.created"
    MESSAGE_UPDATED = "message.updated"
    FIRST_REPLY_CREATED = "first_reply.created"
    REPLY_CREATED = "reply.created"
    
    # Contact события
    CONTACT_CREATED = "contact.created"
    CONTACT_UPDATED = "contact.updated"


class EventDispatcher:
    """
    Event Dispatcher (по образцу Chatwoot).
    
    Централизованная система событий для real-time обновлений, webhooks, уведомлений.
    """
    
    def __init__(self):
        self._sync_listeners: Dict[str, list[Callable]] = {}
        self._async_listeners: Dict[str, list[Callable]] = {}
    
    def dispatch(self, event_name: str, timestamp: datetime, data: Dict[str, Any], async: bool = False):
        """
        Отправить событие.
        
        Args:
            event_name: Имя события (из Events)
            timestamp: Время события
            data: Данные события
            async: Асинхронная обработка (через Celery)
        """
        if async:
            listeners = self._async_listeners.get(event_name, [])
        else:
            listeners = self._sync_listeners.get(event_name, [])
        
        for listener in listeners:
            try:
                listener(event_name, timestamp, data)
            except Exception as e:
                logger.error(
                    f"Error in event listener for {event_name}",
                    exc_info=True,
                    extra={"event": event_name, "data": data}
                )
    
    def subscribe(self, event_name: str, listener: Callable, async: bool = False):
        """Подписаться на событие."""
        if async:
            if event_name not in self._async_listeners:
                self._async_listeners[event_name] = []
            self._async_listeners[event_name].append(listener)
        else:
            if event_name not in self._sync_listeners:
                self._sync_listeners[event_name] = []
            self._sync_listeners[event_name].append(listener)


# Глобальный экземпляр
_dispatcher = EventDispatcher()


def get_dispatcher() -> EventDispatcher:
    """Получить глобальный Event Dispatcher."""
    return _dispatcher
```

#### Шаг 2: Интегрировать в модели

```python
# backend/messenger/models.py

from .dispatchers import get_dispatcher, Events

class Conversation(models.Model):
    # ... поля ...
    
    def save(self, *args, **kwargs):
        is_new = self.pk is None
        old_status = None
        old_assignee_id = None
        
        if not is_new:
            old = type(self).objects.get(pk=self.pk)
            old_status = old.status
            old_assignee_id = old.assignee_id
        
        super().save(*args, **kwargs)
        
        dispatcher = get_dispatcher()
        
        if is_new:
            # Событие создания диалога
            dispatcher.dispatch(
                Events.CONVERSATION_CREATED,
                timezone.now(),
                {"conversation": self}
            )
        else:
            # События обновления
            if old_status != self.status:
                dispatcher.dispatch(
                    Events.CONVERSATION_STATUS_CHANGED,
                    timezone.now(),
                    {"conversation": self, "old_status": old_status}
                )
            
            if old_assignee_id != self.assignee_id:
                dispatcher.dispatch(
                    Events.ASSIGNEE_CHANGED,
                    timezone.now(),
                    {"conversation": self, "old_assignee_id": old_assignee_id}
                )
            
            dispatcher.dispatch(
                Events.CONVERSATION_UPDATED,
                timezone.now(),
                {"conversation": self}
            )


class Message(models.Model):
    # ... поля ...
    
    def save(self, *args, **kwargs):
        is_new = self.pk is None
        super().save(*args, **kwargs)
        
        dispatcher = get_dispatcher()
        
        if is_new:
            dispatcher.dispatch(
                Events.MESSAGE_CREATED,
                timezone.now(),
                {"message": self}
            )
            
            # Проверка первого ответа
            if self._is_valid_first_reply():
                dispatcher.dispatch(
                    Events.FIRST_REPLY_CREATED,
                    timezone.now(),
                    {"message": self}
                )
```

---

## Фаза 5: Безопасность и валидации (Приоритет 🔴)

### Задача 5.1: Добавить защиту от флуда в модель

**По образцу Chatwoot:** `app/models/message.rb:274`

Уже описано в Задаче 1.2 (обновление модели Message).

---

### Задача 5.2: Добавить throttling для last_seen

**По образцу Chatwoot:** `app/controllers/api/v1/accounts/conversations_controller.rb:115`

Уже описано в разделе "API и контроллеры" документа сравнения.

---

## Фаза 6: Edge cases (Приоритет 🟡)

### Задача 6.1: Защита от race condition

**По образцу Chatwoot:** использование `select_for_update`

```python
# backend/messenger/services.py

from django.db import transaction

def assign_conversation(conversation: Conversation, user: User) -> None:
    """Назначить диалог оператору с защитой от race condition."""
    with transaction.atomic():
        # Блокируем запись для обновления
        conv = Conversation.objects.select_for_update().get(pk=conversation.pk)
        
        # Проверяем, что диалог не назначен другому оператору
        if conv.assignee_id and conv.assignee_id != user.id:
            raise ValueError("Conversation already assigned to another agent")
        
        now = timezone.now()
        conv.assignee = user
        conv.assignee_assigned_at = now
        conv.assignee_opened_at = None
        conv.waiting_since = conv.waiting_since or now
        conv.save(update_fields=[
            "assignee", "assignee_assigned_at", "assignee_opened_at", "waiting_since"
        ])
```

---

## Фаза 7: Код ревью и рефакторинг (Приоритет 🟢)

**Цель:** Оптимизировать код без ломки функциональности, сделать его современнее и красивее.

### Принципы рефакторинга

1. **Без ломки** — все существующие функции продолжают работать
2. **Без полных переписываний** — улучшаем существующий код, не переписываем с нуля
3. **Оптимизация** — улучшаем производительность, убираем N+1 запросы
4. **Современность** — используем современные паттерны Django/Python
5. **Красота** — улучшаем читаемость и структуру кода

### Задача 7.1: Оптимизация запросов (N+1)

**Проблема:** Множественные запросы к БД при загрузке связанных объектов.

**Решение:** Использовать `select_related()` и `prefetch_related()`.

```python
# backend/messenger/api.py

class ConversationViewSet(viewsets.ModelViewSet):
    def get_queryset(self):
        qs = Conversation.objects.select_related(
            'inbox', 'contact', 'branch', 'region', 'assignee'
        ).prefetch_related(
            'messages'
        )
        # ... остальная логика
```

### Задача 7.2: Рефакторинг сервисов

**Цель:** Вынести бизнес-логику из views в сервисы.

```python
# backend/messenger/services/conversation_service.py

class ConversationService:
    @staticmethod
    def create_conversation(inbox, contact, **kwargs):
        """Создание диалога с валидацией."""
        # Логика создания
        
    @staticmethod
    def assign_conversation(conversation, user):
        """Назначение диалога с защитой от race condition."""
        # Логика назначения
```

### Задача 7.3: Улучшение типизации

**Цель:** Добавить type hints для лучшей читаемости и поддержки IDE.

```python
from typing import Optional, List
from django.db.models import QuerySet

def get_conversations_for_user(user: User) -> QuerySet[Conversation]:
    """Получить диалоги для пользователя."""
    # ...
```

### Задача 7.4: Оптимизация сериализаторов

**Цель:** Использовать `SerializerMethodField` для сложных вычислений, кэшировать результаты.

```python
class ConversationSerializer(serializers.ModelSerializer):
    unread_count = serializers.SerializerMethodField()
    
    @staticmethod
    def get_unread_count(obj):
        # Кэшированное вычисление
        # ...
```

### Задача 7.5: Улучшение структуры кода

**Цель:** Разделить код на модули по функциональности.

```
backend/messenger/
├── models.py
├── services/
│   ├── __init__.py
│   ├── conversation_service.py
│   ├── message_service.py
│   └── assignment_service.py
├── serializers/
│   ├── __init__.py
│   ├── conversation.py
│   └── message.py
└── utils/
    ├── __init__.py
    └── helpers.py
```

### Задача 7.6: Добавление docstrings

**Цель:** Документировать все публичные методы и классы.

```python
def assign_conversation(conversation: Conversation, user: User) -> None:
    """
    Назначить диалог оператору.
    
    Args:
        conversation: Диалог для назначения
        user: Оператор для назначения
        
    Raises:
        ValueError: Если диалог уже назначен другому оператору
    """
    # ...
```

### Задача 7.7: Оптимизация кэширования

**Цель:** Использовать Redis для кэширования часто запрашиваемых данных.

```python
from django.core.cache import cache

def get_conversation_count_for_user(user: User) -> int:
    """Получить количество диалогов с кэшированием."""
    cache_key = f"conversation_count:{user.id}"
    count = cache.get(cache_key)
    if count is None:
        count = Conversation.objects.filter(assignee=user).count()
        cache.set(cache_key, count, timeout=300)  # 5 минут
    return count
```

---

## Порядок выполнения

### Неделя 1-2: Критические модели
1. ✅ Добавить поля в Conversation (waiting_since, first_reply_created_at, etc.)
2. ✅ Добавить поля в Message (content_attributes, защита от флуда)
3. ✅ Добавить поля в Contact (last_activity_at, blocked)
4. ✅ Создать ContactInbox модель
5. ✅ Миграции данных

### Неделя 3: Индексы
6. ✅ Добавить составные индексы
7. ✅ Оптимизировать запросы

### Неделя 4-5: Сервисы
8. ✅ Переделать Round-Robin на Redis список
9. ✅ Добавить Rate Limiter
10. ✅ Реализовать waiting_since логику
11. ✅ Реализовать first_reply логику

### Неделя 6-7: Real-time
12. ✅ Реализовать Event Dispatcher
13. ✅ Реализовать OnlineStatusTracker
14. ✅ Расширить SSE для операторской панели

### Неделя 8: Безопасность и edge cases
15. ✅ Добавить защиту от флуда
16. ✅ Добавить throttling last_seen
17. ✅ Защита от race condition

### Неделя 9: UI/UX по образцу Chatwoot
18. ⏳ Изучить UI/UX Chatwoot
19. ⏳ Адаптировать под наш проект (без конфликтов)
20. ⏳ Трёхколоночный layout для операторской панели
21. ⏳ Компактные карточки диалогов
22. ⏳ Real-time обновления в UI

### Неделя 10: Код ревью и рефакторинг (Фаза 7)
23. ✅ Код ревью всех изменений
24. ✅ Оптимизация запросов (N+1)
25. ✅ Рефакторинг сервисов
26. ✅ Улучшение типизации
27. ✅ Оптимизация сериализаторов
28. ✅ Улучшение структуры кода
29. ✅ Добавление docstrings
30. ✅ Оптимизация кэширования

---

## Критерии готовности к production

Мессенджер считается production-ready, когда:

1. ✅ Все критические поля добавлены и работают
2. ✅ Составные индексы созданы и протестированы
3. ✅ Round-Robin работает через Redis список с валидацией
4. ✅ Защита от флуда работает на уровне модели
5. ✅ Throttling last_seen реализован
6. ✅ Event Dispatcher работает для всех событий
7. ✅ Real-time обновления работают для виджета и операторской панели
8. ✅ Все edge cases обработаны
9. ✅ Тесты покрывают критическую функциональность
10. ✅ Документация обновлена

---

*План создан на основе досконального изучения Chatwoot и сравнения с текущей реализацией. Все решения опираются на проверенные методы Chatwoot.*
