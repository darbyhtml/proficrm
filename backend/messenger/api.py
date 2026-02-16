"""
DRF API для messenger.

Этап 2: операторский API для диалогов и шаблонов ответов.
"""

from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from accounts.models import User
from policy.drf import PolicyPermission

from . import models, selectors, serializers
from .utils import ensure_messenger_enabled_api


class MessengerEnabledApiMixin:
    """
    Миксин для проверки feature-флага MESSENGER_ENABLED во всех DRF ViewSet messenger.

    Вызывает ensure_messenger_enabled_api() в initial(), чтобы гарантировать,
    что при отключённом флаге все методы возвращают 404, не нарушая стабильность маршрутов.
    """

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        ensure_messenger_enabled_api()


class ConversationViewSet(MessengerEnabledApiMixin, viewsets.ReadOnlyModelViewSet):
    """
    API для диалогов (conversations).

    Поддерживаемые методы:
    - list: список диалогов (только видимые через visible_conversations_qs)
    - retrieve: детали диалога
    - partial_update: обновление статуса/назначения/приоритета (whitelist через serializer)
    - messages (nested action): GET список сообщений, POST создание исходящего/внутреннего сообщения
    """

    queryset = models.Conversation.objects.none()
    serializer_class = serializers.ConversationSerializer
    permission_classes = [IsAuthenticated, PolicyPermission]
    policy_resource_prefix = "api:messenger:conversations"

    def partial_update(self, request, *args, **kwargs):
        """
        Переопределяем partial_update для использования whitelist из serializer.
        """
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def get_queryset(self):
        """
        Возвращает только диалоги, видимые текущему пользователю через selectors.
        Никаких .objects.all() для не-админов.
        """
        user = self.request.user
        return selectors.visible_conversations_qs(user)

    def get_serializer_class(self):
        if self.action == "messages":
            return serializers.MessageSerializer
        return serializers.ConversationSerializer

    @action(detail=True, methods=["get", "post"], url_path="messages")
    def messages(self, request, pk=None):
        """
        Nested action для работы с сообщениями диалога.

        GET: список сообщений диалога (сортировка по created_at).
        POST: создание исходящего или внутреннего сообщения оператором.
        """
        conversation = self.get_object()

        if request.method == "GET":
            # GET: список сообщений
            messages = conversation.messages.all().order_by("created_at", "id")
            serializer = serializers.MessageSerializer(messages, many=True)
            return Response(serializer.data)

        elif request.method == "POST":
            # POST: создание сообщения оператором
            serializer = serializers.MessageSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)

            # Запрещаем создание входящих сообщений через операторский endpoint
            direction = serializer.validated_data.get("direction")
            if direction == models.Message.Direction.IN:
                return Response(
                    {"detail": "Входящие сообщения нельзя создавать через операторский API."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Разрешаем только OUT и INTERNAL
            if direction not in (models.Message.Direction.OUT, models.Message.Direction.INTERNAL):
                return Response(
                    {"detail": "Разрешены только исходящие (out) или внутренние (internal) сообщения."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # sender_user всегда = текущий пользователь (не принимаем из клиента)
            message = serializer.save(
                conversation=conversation,
                sender_user=request.user,
                sender_contact=None,  # Для OUT/INTERNAL sender_contact должен быть None
            )

            return Response(serializers.MessageSerializer(message).data, status=status.HTTP_201_CREATED)


class CannedResponseViewSet(MessengerEnabledApiMixin, viewsets.ModelViewSet):
    """
    API для шаблонов ответов (canned responses).

    - list: доступен всем через visible_canned_responses_qs
    - create/update/delete: только ADMIN/superuser
    """

    queryset = models.CannedResponse.objects.none()
    serializer_class = serializers.CannedResponseSerializer
    permission_classes = [IsAuthenticated, PolicyPermission]
    policy_resource_prefix = "api:messenger:canned-responses"

    def get_queryset(self):
        """
        Возвращает только шаблоны, видимые текущему пользователю через selectors.
        """
        user = self.request.user
        return selectors.visible_canned_responses_qs(user)

    def check_permissions(self, request):
        """
        Проверка прав на действие: для create/update/delete требуем ADMIN или superuser.
        """
        super().check_permissions(request)
        if self.action in ("create", "update", "partial_update", "destroy"):
            if not (request.user.is_superuser or request.user.role == User.Role.ADMIN):
                from rest_framework.exceptions import PermissionDenied

                raise PermissionDenied("Только администраторы могут изменять шаблоны ответов.")

    def perform_create(self, serializer):
        """
        При создании автоматически проставляем created_by = текущий пользователь.
        """
        serializer.save(created_by=self.request.user)

