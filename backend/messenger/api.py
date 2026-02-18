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
        qs = selectors.visible_conversations_qs(user).select_related("contact", "branch", "region", "assignee", "inbox")

        # Аннотации: превью последнего сообщения + unread_count (для текущего пользователя)
        from django.db.models import OuterRef, Subquery, Q, F, Count
        from .models import Message

        last_message = (
            Message.objects.filter(conversation=OuterRef("pk"))
            .order_by("-created_at", "-id")
            .values("body")[:1]
        )
        qs = qs.annotate(last_message_body=Subquery(last_message))

        if user and user.is_authenticated:
            qs = qs.annotate(
                unread_count=Count(
                    "messages__id",
                    filter=Q(
                        messages__direction=Message.Direction.IN,
                        assignee_id=user.id,
                    )
                    & (
                        Q(assignee_last_read_at__isnull=True)
                        | Q(messages__created_at__gt=F("assignee_last_read_at"))
                    ),
                    distinct=True,
                )
            )

        # Фильтры для UI (q/status/mine/assignee)
        qp = self.request.query_params
        q = (qp.get("q") or "").strip()
        if q:
            q_digits = "".join([c for c in q if c.isdigit()])
            q_obj = Q(contact__name__icontains=q) | Q(contact__email__icontains=q) | Q(contact__phone__icontains=q)
            if q_digits:
                try:
                    q_obj = q_obj | Q(id=int(q_digits))
                except (ValueError, TypeError):
                    pass
            qs = qs.filter(q_obj)

        status_filter = (qp.get("status") or "").strip()
        if status_filter:
            if "," in status_filter:
                statuses = [s.strip() for s in status_filter.split(",") if s.strip()]
                qs = qs.filter(status__in=statuses)
            else:
                qs = qs.filter(status=status_filter)

        mine = (qp.get("mine") or "").strip().lower() in ("1", "true", "yes")
        if mine and user and user.is_authenticated:
            qs = qs.filter(assignee_id=user.id)

        assignee_id = (qp.get("assignee") or "").strip()
        if assignee_id:
            try:
                qs = qs.filter(assignee_id=int(assignee_id))
            except (ValueError, TypeError):
                pass

        # Сортировка: новые сверху
        return qs.order_by("-last_message_at", "-id")

    def get_serializer_class(self):
        if self.action == "messages":
            return serializers.MessageSerializer
        return serializers.ConversationSerializer

    @action(detail=True, methods=["post"], url_path="read")
    def read(self, request, pk=None):
        """
        POST: отметить диалог прочитанным текущим оператором (если он назначен).
        """
        conversation = self.get_object()
        if conversation.assignee_id != request.user.id:
            return Response({"status": "ignored"}, status=status.HTTP_200_OK)
        from django.utils import timezone

        now = timezone.now()
        models.Conversation.objects.filter(pk=conversation.pk).update(assignee_last_read_at=now)
        return Response({"status": "ok", "assignee_last_read_at": now.isoformat()}, status=status.HTTP_200_OK)

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
            #
            # Поддерживаем параметры:
            # - since: вернуть только новые (created_at > since)
            # - before: для ленивой подгрузки истории (created_at < before)
            # - limit: ограничение (по умолчанию 50)
            messages = conversation.messages.all()

            from django.utils.dateparse import parse_datetime

            since_raw = (request.query_params.get("since") or "").strip()
            before_raw = (request.query_params.get("before") or "").strip()
            limit_raw = (request.query_params.get("limit") or "").strip()

            try:
                limit = int(limit_raw) if limit_raw else 50
            except (ValueError, TypeError):
                limit = 50
            limit = max(1, min(limit, 200))

            since_dt = parse_datetime(since_raw) if since_raw else None
            before_dt = parse_datetime(before_raw) if before_raw else None

            if since_dt:
                messages = messages.filter(created_at__gt=since_dt).order_by("created_at", "id")
                serializer = serializers.MessageSerializer(messages, many=True)
                return Response(serializer.data)

            if before_dt:
                # Берём "кусок" истории с конца (до before) и разворачиваем в хронологический порядок
                chunk = list(
                    messages.filter(created_at__lt=before_dt).order_by("-created_at", "-id")[:limit]
                )
                chunk.reverse()
                serializer = serializers.MessageSerializer(chunk, many=True)
                return Response(serializer.data)

            # По умолчанию: последние N сообщений (чтобы не тащить всю историю)
            chunk = list(messages.order_by("-created_at", "-id")[:limit])
            chunk.reverse()
            serializer = serializers.MessageSerializer(chunk, many=True)
            return Response(serializer.data)

        elif request.method == "POST":
            # POST: создание сообщения оператором (+ вложения)
            direction = (request.data.get("direction") or models.Message.Direction.OUT).strip().lower()
            body = (request.data.get("body") or "").strip()
            files = []
            try:
                files = list(request.FILES.getlist("attachments"))
            except Exception:
                files = []

            # Запрещаем создание входящих сообщений через операторский endpoint
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

            if not body and not files:
                return Response(
                    {"detail": "Текст сообщения не может быть пустым без вложений."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            from .services import record_message

            message = record_message(
                conversation=conversation,
                direction=direction,
                body=body or "",
                sender_user=request.user,
                sender_contact=None,
            )

            for f in files:
                models.MessageAttachment.objects.create(message=message, file=f)

            # Перезагрузить сообщение из БД, чтобы получить вложения в сериализаторе
            message.refresh_from_db()
            
            return Response(serializers.MessageSerializer(message).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get", "post"], url_path="typing")
    def typing(self, request, pk=None):
        """
        GET: { operator_typing, contact_typing } для отображения «печатает» в панели оператора.
        POST: отметить, что оператор печатает (TTL 8 с в Redis).
        """
        conversation = self.get_object()
        from .typing import get_typing_status, set_operator_typing

        if request.method == "POST":
            set_operator_typing(conversation.id)
            return Response({"status": "ok"}, status=status.HTTP_200_OK)
        return Response(get_typing_status(conversation.id), status=status.HTTP_200_OK)


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

