"""
Сериализаторы messenger.

Полноценные API будут реализованы на Этапе 2.
Сейчас файл создан как заготовка, чтобы структура приложения была завершённой.
"""

from rest_framework import serializers

from . import models


class ConversationSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.Conversation
        fields = "__all__"
        # branch выставляется автоматически из inbox.branch и не редактируется вручную.
        # inbox/contact/region для v1 считаем неизменяемыми через API (только status/assignee/priority).
        read_only_fields = ("branch", "created_at", "last_message_at")

    def update(self, instance, validated_data):
        """
        Жёстко ограничиваем набор обновляемых полей:
        - разрешаем: status, assignee, priority;
        - запрещаем: inbox, branch, contact, region и любые другие системные поля.
        """
        allowed_fields = {"status", "assignee", "priority"}
        forbidden = {field for field in validated_data.keys() if field not in allowed_fields}
        if forbidden:
            raise serializers.ValidationError(
                {field: "Это поле нельзя изменять через API." for field in sorted(forbidden)}
            )

        for field in allowed_fields:
            if field in validated_data:
                setattr(instance, field, validated_data[field])

        # Валидация инвариантов модели (branch/inbox и т.п.).
        instance.full_clean()
        instance.save()
        return instance


class MessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.Message
        fields = "__all__"
        read_only_fields = ("created_at", "delivered_at")

    def create(self, validated_data):
        instance = models.Message(**validated_data)
        # Гарантируем вызов model.clean() (инварианты direction/sender).
        instance.full_clean()
        instance.save()
        return instance

    def update(self, instance, validated_data):
        # Для v1 сообщения считаем практически неизменяемыми. Разрешим, при необходимости,
        # только обновление delivered_at/body, но при сохранении всё равно валидируем инварианты.
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.full_clean()
        instance.save()
        return instance


class CannedResponseSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.CannedResponse
        fields = "__all__"
        read_only_fields = ("created_by", "created_at")

