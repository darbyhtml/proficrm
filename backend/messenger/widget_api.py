"""
Публичный Widget API для встраивания виджета на внешние сайты.

Endpoints:
- POST /api/widget/bootstrap/ - создание/получение сессии виджета
- POST /api/widget/send/ - отправка сообщения от посетителя
- GET /api/widget/poll/ - получение новых сообщений от операторов

Все endpoints публичные (без аутентификации), защищены через widget_token и widget_session_token.
"""

from django.utils import timezone as django_timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from . import models, serializers, services
from .utils import (
    create_widget_session,
    get_widget_session,
    ensure_messenger_enabled_api,
)


class WidgetApiMixin:
    """
    Миксин для проверки feature-флага в widget API endpoints.
    """

    def dispatch(self, *args, **kwargs):
        ensure_messenger_enabled_api()
        return super().dispatch(*args, **kwargs)


@api_view(["POST"])
@permission_classes([AllowAny])
def widget_bootstrap(request):
    """
    POST /api/widget/bootstrap/

    Создаёт или находит диалог для посетителя и возвращает widget_session_token.
    """
    ensure_messenger_enabled_api()

    input_serializer = serializers.WidgetBootstrapSerializer(data=request.data)
    input_serializer.is_valid(raise_exception=True)

    widget_token = input_serializer.validated_data["widget_token"]
    contact_external_id = input_serializer.validated_data["contact_external_id"]

    # Найти активный Inbox по widget_token
    try:
        inbox = models.Inbox.objects.get(widget_token=widget_token, is_active=True)
    except models.Inbox.DoesNotExist:
        return Response(
            {"detail": "Invalid widget_token or inbox is inactive."},
            status=status.HTTP_404_NOT_FOUND,
        )

    # Создать или получить Contact (обновляем поля, если переданы новые значения)
    contact = services.create_or_get_contact(
        external_id=contact_external_id,
        name=input_serializer.validated_data.get("name") or None,  # Передаём None, если пустая строка
        email=input_serializer.validated_data.get("email") or None,
        phone=input_serializer.validated_data.get("phone") or None,
        update_if_exists=True,  # Обновляем существующий контакт новыми данными
    )

    # Найти активный диалог (OPEN/PENDING) или создать новый
    # Если все диалоги закрыты (RESOLVED/CLOSED), создаём новый диалог
    conversation = models.Conversation.objects.filter(
        inbox=inbox,
        contact=contact,
        status__in=[models.Conversation.Status.OPEN, models.Conversation.Status.PENDING],
    ).first()

    if not conversation:
        # Проверяем, есть ли вообще диалоги с этим contact+inbox (даже закрытые)
        # Если есть закрытые - создаём новый диалог (не переоткрываем старые)
        conversation = models.Conversation.objects.create(
            inbox=inbox,
            contact=contact,
            status=models.Conversation.Status.OPEN,
            branch=inbox.branch,  # Автоматически из inbox.branch
        )

    # Создать widget_session_token
    session = create_widget_session(
        inbox_id=inbox.id,
        conversation_id=conversation.id,
        contact_id=str(contact.id),
    )

    # Опционально: вернуть последние сообщения (последние 10)
    initial_messages = []
    messages = conversation.messages.filter(direction__in=[models.Message.Direction.OUT, models.Message.Direction.INTERNAL]).order_by("-created_at")[:10]
    for msg in reversed(messages):  # В хронологическом порядке
        initial_messages.append({
            "id": msg.id,
            "body": msg.body,
            "direction": msg.direction,
            "created_at": msg.created_at.isoformat(),
        })

    response_serializer = serializers.WidgetBootstrapResponseSerializer({
        "widget_session_token": session.token,
        "conversation_id": conversation.id,
        "initial_messages": initial_messages,
    })

    return Response(response_serializer.data, status=status.HTTP_200_OK)


@api_view(["POST"])
@permission_classes([AllowAny])
def widget_send(request):
    """
    POST /api/widget/send/

    Отправка сообщения от посетителя (входящее сообщение).
    """
    ensure_messenger_enabled_api()

    input_serializer = serializers.WidgetSendSerializer(data=request.data)
    input_serializer.is_valid(raise_exception=True)

    widget_token = input_serializer.validated_data["widget_token"]
    widget_session_token = input_serializer.validated_data["widget_session_token"]
    body = input_serializer.validated_data["body"].strip()

    if not body:
        return Response(
            {"detail": "Message body cannot be empty."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Валидировать widget_token → inbox
    try:
        inbox = models.Inbox.objects.get(widget_token=widget_token, is_active=True)
    except models.Inbox.DoesNotExist:
        return Response(
            {"detail": "Invalid widget_token or inbox is inactive."},
            status=status.HTTP_404_NOT_FOUND,
        )

    # Получить сессию виджета
    session = get_widget_session(widget_session_token)
    if not session:
        return Response(
            {"detail": "Invalid or expired widget_session_token."},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    # Проверить совпадение inbox_id
    if session.inbox_id != inbox.id:
        return Response(
            {"detail": "Widget session token does not match widget_token."},
            status=status.HTTP_403_FORBIDDEN,
        )

    # Получить conversation и contact из сессии
    try:
        conversation = models.Conversation.objects.get(id=session.conversation_id, inbox=inbox)
        contact = models.Contact.objects.get(id=session.contact_id)
    except (models.Conversation.DoesNotExist, models.Contact.DoesNotExist):
        return Response(
            {"detail": "Conversation or contact not found."},
            status=status.HTTP_404_NOT_FOUND,
        )

    # Создать входящее сообщение
    message = models.Message(
        conversation=conversation,
        direction=models.Message.Direction.IN,
        body=body,
        sender_contact=contact,
        sender_user=None,  # Для IN сообщений sender_user должен быть None
    )
    message.full_clean()  # Валидация инвариантов
    message.save()

    # Обновить last_message_at
    models.Conversation.objects.filter(pk=conversation.id).update(last_message_at=django_timezone.now())

    response_serializer = serializers.WidgetSendResponseSerializer({
        "id": message.id,
        "created_at": message.created_at,
    })

    return Response(response_serializer.data, status=status.HTTP_201_CREATED)


@api_view(["GET"])
@permission_classes([AllowAny])
def widget_poll(request):
    """
    GET /api/widget/poll/

    Получение новых сообщений от операторов (OUT и INTERNAL).
    """
    ensure_messenger_enabled_api()

    widget_token = request.query_params.get("widget_token")
    widget_session_token = request.query_params.get("widget_session_token")
    since_id = request.query_params.get("since_id")

    if not widget_token or not widget_session_token:
        return Response(
            {"detail": "widget_token and widget_session_token are required."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Валидировать widget_token → inbox
    try:
        inbox = models.Inbox.objects.get(widget_token=widget_token, is_active=True)
    except models.Inbox.DoesNotExist:
        return Response(
            {"detail": "Invalid widget_token or inbox is inactive."},
            status=status.HTTP_404_NOT_FOUND,
        )

    # Получить сессию виджета
    session = get_widget_session(widget_session_token)
    if not session:
        return Response(
            {"detail": "Invalid or expired widget_session_token."},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    # Проверить совпадение inbox_id
    if session.inbox_id != inbox.id:
        return Response(
            {"detail": "Widget session token does not match widget_token."},
            status=status.HTTP_403_FORBIDDEN,
        )

    # Получить conversation
    try:
        conversation = models.Conversation.objects.get(id=session.conversation_id, inbox=inbox)
    except models.Conversation.DoesNotExist:
        return Response(
            {"detail": "Conversation not found."},
            status=status.HTTP_404_NOT_FOUND,
        )

    # Получить новые сообщения (только OUT и INTERNAL)
    messages_qs = conversation.messages.filter(
        direction__in=[models.Message.Direction.OUT, models.Message.Direction.INTERNAL]
    ).order_by("created_at", "id")

    if since_id:
        try:
            since_id_int = int(since_id)
            messages_qs = messages_qs.filter(id__gt=since_id_int)
        except ValueError:
            return Response(
                {"detail": "Invalid since_id format."},
                status=status.HTTP_400_BAD_REQUEST,
            )

    messages = messages_qs[:50]  # Лимит 50 сообщений за запрос

    result = []
    for msg in messages:
        result.append({
            "id": msg.id,
            "body": msg.body,
            "direction": msg.direction,
            "created_at": msg.created_at.isoformat(),
        })

    return Response({"messages": result}, status=status.HTTP_200_OK)
