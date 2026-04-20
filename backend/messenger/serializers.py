"""
Сериализаторы messenger.

Сериализаторы для операторского API и виджета.
Оптимизированы для работы с prefetch_related/select_related (по образцу Chatwoot).
"""

from typing import Optional
from rest_framework import serializers

from . import models


class ConversationSerializer(serializers.ModelSerializer):
    """
    Сериализатор для диалогов (по образцу Chatwoot).

    Использует prefetch_related для оптимизации запросов.
    Все связанные поля (contact_name, branch_name и т.д.) читаются из предзагруженных объектов.
    """

    contact_name = serializers.CharField(source="contact.name", read_only=True)
    contact_email = serializers.CharField(source="contact.email", read_only=True)
    contact_phone = serializers.CharField(source="contact.phone", read_only=True)
    branch_name = serializers.CharField(source="branch.name", read_only=True)
    region_name = serializers.CharField(source="region.name", read_only=True)
    assignee_name = serializers.CharField(source="assignee.get_full_name", read_only=True)
    last_message_body = serializers.CharField(read_only=True)
    unread_count = serializers.IntegerField(read_only=True)
    # UI-статус: NEW / WAITING / IN_PROGRESS / CLOSED — читается из property.
    ui_status = serializers.CharField(read_only=True)
    # Минуты ожидания клиента в состоянии WAITING (0 в прочих статусах).
    waiting_minutes = serializers.IntegerField(read_only=True)
    label_ids = serializers.PrimaryKeyRelatedField(
        source="labels",
        many=True,
        queryset=models.ConversationLabel.objects.all(),
        required=False,
    )
    label_names = serializers.SerializerMethodField()

    def get_label_names(self, obj):
        return [{"id": l.id, "title": l.title, "color": l.color} for l in obj.labels.all()]

    class Meta:
        model = models.Conversation
        fields = (
            "id",
            "inbox",
            "contact",
            "status",
            "priority",
            "assignee",
            "assignee_assigned_at",
            "assignee_last_read_at",
            "branch",
            "region",
            "created_at",
            "last_activity_at",
            "waiting_since",
            "first_reply_created_at",
            "snoozed_until",
            "contact_last_seen_at",
            "agent_last_seen_at",
            "needs_help",
            "needs_help_at",
            "resolution",
            "escalation_level",
            "last_escalated_at",
            # F5: off-hours request (заявка вне рабочих часов).
            "off_hours_channel",
            "off_hours_contact",
            "off_hours_note",
            "off_hours_requested_at",
            "contacted_back_at",
            "contacted_back_by",
            # annotated / computed
            "contact_name",
            "contact_email",
            "contact_phone",
            "branch_name",
            "region_name",
            "assignee_name",
            "last_message_body",
            "unread_count",
            "ui_status",
            "waiting_minutes",
            "label_ids",
            "label_names",
        )
        read_only_fields = (
            "created_at",
            "last_activity_at",
            "waiting_since",
            "first_reply_created_at",
            "contact_last_seen_at",
            "agent_last_seen_at",
            "needs_help",
            "needs_help_at",
            "escalation_level",
            "last_escalated_at",
            # off-hours поля управляются только через widget_offhours_request
            # и action contacted-back, через обычный PATCH не редактируются.
            "off_hours_channel",
            "off_hours_contact",
            "off_hours_note",
            "off_hours_requested_at",
            "contacted_back_at",
            "contacted_back_by",
        )

    def update(self, instance: models.Conversation, validated_data: dict) -> models.Conversation:
        """
        Обновление диалога с жёстким ограничением полей (по образцу Chatwoot).

        Args:
            instance: Экземпляр диалога для обновления
            validated_data: Валидированные данные из запроса

        Returns:
            Обновлённый экземпляр диалога

        Raises:
            ValidationError: Если попытка изменить запрещённые поля

        Разрешённые поля:
        - status: Статус диалога
        - assignee: Назначенный оператор
        - priority: Приоритет диалога

        Запрещённые поля:
        - inbox, branch, contact, region и любые другие системные поля
        """
        allowed_fields = {"status", "assignee", "priority", "labels", "resolution"}
        forbidden = {field for field in validated_data.keys() if field not in allowed_fields}
        if forbidden:
            raise serializers.ValidationError(
                {field: "Это поле нельзя изменять через API." for field in sorted(forbidden)}
            )

        # Назначить можно только менеджера
        if "assignee" in validated_data:
            assignee = validated_data["assignee"]
            if assignee is not None:
                from accounts.models import User

                if assignee.role != User.Role.MANAGER:
                    raise serializers.ValidationError(
                        {"assignee": "Ответственным можно назначить только менеджера."}
                    )

        # M2M поле labels обрабатываем отдельно
        labels = validated_data.pop("labels", None)

        old_status = instance.status
        for field in allowed_fields:
            if field != "labels" and field in validated_data:
                setattr(instance, field, validated_data[field])

        # Валидация инвариантов модели (branch/inbox и т.п.).
        instance.full_clean()
        instance.save()

        if labels is not None:
            instance.labels.set(labels)

        # Reporting: conversation resolved
        if instance.status == models.Conversation.Status.RESOLVED and old_status != instance.status:
            from .reporting import record_conversation_resolved

            record_conversation_resolved(instance)

        # Automation Rules: conversation_updated
        if old_status != instance.status:
            try:
                from .automation import dispatch_event

                dispatch_event("conversation_updated", conversation=instance)
            except Exception:
                pass

        return instance


class MessageSerializer(serializers.ModelSerializer):
    """
    Сериализатор для сообщений (по образцу Chatwoot).

    Использует prefetch_related для attachments и select_related для sender_user/sender_contact.
    """

    class MessageAttachmentSerializer(serializers.ModelSerializer):
        """Сериализатор для вложений сообщений."""

        class Meta:
            model = models.MessageAttachment
            fields = ("id", "file", "original_name", "content_type", "size", "created_at")

    attachments = MessageAttachmentSerializer(many=True, read_only=True)
    sender_user_name = serializers.CharField(source="sender_user.get_full_name", read_only=True)
    sender_user_username = serializers.CharField(source="sender_user.username", read_only=True)
    sender_contact_name = serializers.CharField(source="sender_contact.name", read_only=True)

    class Meta:
        model = models.Message
        fields = (
            "id",
            "conversation",
            "direction",
            "body",
            "content_attributes",
            "source_id",
            "sender_user",
            "sender_contact",
            "created_at",
            "delivered_at",
            "read_at",
            # computed
            "attachments",
            "sender_user_name",
            "sender_user_username",
            "sender_contact_name",
        )
        read_only_fields = ("created_at", "delivered_at")

    def create(self, validated_data: dict) -> models.Message:
        """
        Создание сообщения с валидацией инвариантов (по образцу Chatwoot).

        Args:
            validated_data: Валидированные данные сообщения

        Returns:
            Созданный экземпляр сообщения

        Raises:
            ValidationError: Если нарушены инварианты (direction/sender)
        """
        instance = models.Message(**validated_data)
        # Гарантируем вызов model.clean() (инварианты direction/sender).
        instance.full_clean()
        instance.save()
        return instance

    def update(self, instance: models.Message, validated_data: dict) -> models.Message:
        """
        Обновление сообщения (ограниченное, по образцу Chatwoot).

        Args:
            instance: Экземпляр сообщения для обновления
            validated_data: Валидированные данные

        Returns:
            Обновлённый экземпляр сообщения

        Note:
            Сообщения считаются практически неизменяемыми. Разрешено только
            обновление delivered_at/body, но при сохранении валидируются инварианты.
        """
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.full_clean()
        instance.save()
        return instance


class ConversationLabelSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.ConversationLabel
        fields = ("id", "title", "color", "created_at")
        read_only_fields = ("created_at",)


class CannedResponseSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.CannedResponse
        fields = (
            "id",
            "title",
            "body",
            "branch",
            "created_by",
            "created_at",
            "is_quick_button",
            "sort_order",
        )
        read_only_fields = ("created_by", "created_at")


# ============================================================================
# Widget API serializers (публичный API для виджета)
# ============================================================================


class WidgetBootstrapSerializer(serializers.Serializer):
    """
    Input для POST /api/widget/bootstrap/
    """

    widget_token = serializers.CharField(required=True, help_text="Токен виджета из Inbox")
    contact_external_id = serializers.CharField(
        required=True,
        max_length=255,
        help_text="Внешний идентификатор посетителя (visitor_id)",
    )
    name = serializers.CharField(required=False, allow_blank=True, max_length=255)
    email = serializers.EmailField(required=False, allow_blank=True)
    phone = serializers.CharField(required=False, allow_blank=True, max_length=50)
    meta = serializers.JSONField(
        required=False, default=dict, help_text="Дополнительные метаданные посетителя"
    )
    region_id = serializers.IntegerField(
        required=False, allow_null=True, help_text="ID региона для маршрутизации"
    )


class WidgetBootstrapResponseSerializer(serializers.Serializer):
    """
    Output для POST /api/widget/bootstrap/
    """

    widget_session_token = serializers.CharField(
        help_text="Токен сессии виджета для последующих запросов"
    )
    conversation_id = serializers.IntegerField(help_text="ID диалога")
    initial_messages = serializers.ListField(
        child=serializers.DictField(),
        required=False,
        help_text="Последние сообщения диалога (опционально)",
    )
    outside_working_hours = serializers.BooleanField(
        required=False,
        default=False,
        help_text="True, если сейчас вне рабочих часов и автоназначение не выполнено",
    )
    working_hours_message = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text="Сообщение для виджета при вне рабочих часов (например: «Мы ответим в рабочее время»)",
    )
    offline_mode = serializers.BooleanField(
        required=False,
        default=False,
        help_text="True, если показывать офлайн-сообщение (нет операторов или вне рабочих часов)",
    )
    offline_message = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text="Текст офлайн-сообщения для виджета (настраивается в Inbox)",
    )
    attachments_enabled = serializers.BooleanField(required=False, default=True)
    max_file_size_bytes = serializers.IntegerField(required=False, default=5242880)
    allowed_content_types = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True,
    )
    captcha_required = serializers.BooleanField(required=False, default=False)
    captcha_token = serializers.CharField(required=False, allow_blank=True, default="")
    captcha_question = serializers.CharField(required=False, allow_blank=True, default="")
    sse_enabled = serializers.BooleanField(required=False, default=True)
    title = serializers.CharField(required=False, allow_blank=True)
    greeting = serializers.CharField(required=False, allow_blank=True)
    color = serializers.CharField(required=False, allow_blank=True)
    privacy_url = serializers.CharField(required=False, allow_blank=True)
    privacy_text = serializers.CharField(required=False, allow_blank=True)
    prechat_required = serializers.BooleanField(required=False, default=False)
    working_hours_display = serializers.CharField(required=False, allow_blank=True)


class WidgetSendSerializer(serializers.Serializer):
    """
    Input для POST /api/widget/send/

    Валидация:
    - body обязателен, не пустой после strip(), max_length=2000
    - hp (honeypot) должен быть пустым (если заполнен - это бот)
    """

    widget_token = serializers.CharField(required=True)
    widget_session_token = serializers.CharField(required=True)
    body = serializers.CharField(
        required=True,
        max_length=2000,
        help_text="Текст сообщения (макс. 2000 символов)",
    )
    hp = serializers.CharField(
        required=False, allow_blank=True, help_text="Honeypot поле (должно быть пустым)"
    )

    def validate_hp(self, value):
        """
        Honeypot валидация: если поле заполнено - это бот.
        """
        if value and value.strip():
            raise serializers.ValidationError("Invalid request.")
        return value

    def validate(self, attrs):
        """
        Дополнительная валидация: проверка на спам через cache.
        """
        attrs = super().validate(attrs)

        # Проверка honeypot
        hp = attrs.get("hp", "")
        if hp and hp.strip():
            raise serializers.ValidationError({"hp": "Invalid request."})

        # Проверка на слишком много ссылок в сообщении
        body = attrs.get("body", "")
        if body:
            # Подсчёт ссылок (http://, https://, www.)
            import re

            url_pattern = r"(https?://|www\.)[^\s]+"
            urls = re.findall(url_pattern, body, re.IGNORECASE)
            if len(urls) > 3:  # Максимум 3 ссылки
                raise serializers.ValidationError({"body": "Message contains too many links."})

        return attrs


class WidgetSendResponseSerializer(serializers.Serializer):
    """
    Output для POST /api/widget/send/
    """

    id = serializers.IntegerField(help_text="ID созданного сообщения")
    created_at = serializers.DateTimeField(help_text="Время создания сообщения")
    attachments = serializers.ListField(
        child=serializers.DictField(),
        required=False,
        allow_empty=True,
        help_text="Сериализованные вложения сообщения (как в widget poll/bootstrap).",
    )
