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

from . import models, selectors, serializers, services
from .utils import ensure_messenger_enabled_api
from django.db.models import Q
from django.http import StreamingHttpResponse
import json
import time


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
    API для диалогов (conversations) - по образцу Chatwoot.
    
    Оптимизирован для производительности:
    - select_related для ForeignKey связей
    - prefetch_related для обратных связей
    - Аннотации для вычисляемых полей (unread_count, last_message_body)

    Поддерживаемые методы:
    - list: список диалогов (только видимые через visible_conversations_qs)
    - retrieve: детали диалога
    - partial_update: обновление статуса/назначения/приоритета (whitelist через serializer)
    - destroy: удаление диалога (только для администраторов)
    - messages (nested action): GET список сообщений, POST создание исходящего/внутреннего сообщения
    - stream (nested action): SSE стрим обновлений для операторской панели
    - read (action): отметить диалог прочитанным (с троттлингом)
    - typing (action): статус печати оператора/контакта
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
        
        Оптимизация запросов (по образцу Chatwoot):
        - select_related для ForeignKey связей (убирает N+1 запросы)
        - prefetch_related для обратных связей (messages)
        """
        user = self.request.user
        qs = selectors.visible_conversations_qs(user).select_related(
            "contact", 
            "branch", 
            "region", 
            "assignee", 
            "inbox"
        ).prefetch_related(
            # Предзагружаем сообщения для уменьшения запросов при сериализации
            "messages"
        )

        # Аннотации: превью последнего сообщения + unread_count (для текущего пользователя)
        from django.db.models import OuterRef, Subquery, Q, F, Count
        from .models import Message

        last_message = (
            Message.objects.filter(conversation=OuterRef("pk"))
            .order_by("-created_at", "-id")
            .values("body")[:1]
        )
        # Аннотируем последнее сообщение, но НЕ фильтруем здесь по last_message_body,
        # чтобы retrieve/detail продолжал работать даже для диалогов без сообщений.
        qs = qs.annotate(last_message_body=Subquery(last_message))
        
        # Аннотируем last_activity_at_fallback (fallback на created_at как в Chatwoot)
        from django.db.models import Case, When, F, DateTimeField
        qs = qs.annotate(
            last_activity_at_fallback=Case(
                When(last_activity_at__isnull=False, then=F('last_activity_at')),
                default=F('created_at'),
                output_field=DateTimeField()
            )
        )

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

        # Сортировка: новые сверху (используем last_activity_at вместо last_message_at)
        return qs.order_by("-last_activity_at", "-id")

    def list(self, request, *args, **kwargs):
        """
        Список диалогов: скрываем диалоги без единого сообщения,
        чтобы у операторов не появлялись пустые чаты, созданные только bootstrap'ом виджета.
        """
        # Оптимизация: фильтруем диалоги без сообщений, но используем оптимизированный queryset
        base_qs = self.filter_queryset(
            self.get_queryset().filter(last_message_body__isnull=False)
        )

        page = self.paginate_queryset(base_qs)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(base_qs, many=True)
        return Response(serializer.data)

    def get_serializer_class(self):
        if self.action == "messages":
            return serializers.MessageSerializer
        return serializers.ConversationSerializer

    @action(detail=True, methods=["post"], url_path="read")
    def read(self, request, pk=None):
        """
        Отметить диалог прочитанным текущим оператором (по образцу Chatwoot).
        
        Args:
            request: HTTP запрос
            pk: ID диалога
        
        Returns:
            Response с статусом и временем обновления
        
        Note:
            Использует троттлинг для предотвращения частых обновлений БД.
            Обновляет assignee_last_read_at и agent_last_seen_at.
        """
        conversation = self.get_object()
        if conversation.assignee_id != request.user.id:
            return Response({"status": "ignored"}, status=status.HTTP_200_OK)

        # Обновляем last_seen оператора с троттлингом (по образцу Chatwoot)
        now = services.touch_assignee_last_seen(conversation, request.user)
        return Response(
            {"status": "ok", "assignee_last_read_at": now.isoformat()},
            status=status.HTTP_200_OK,
        )

    def destroy(self, request, pk=None):
        """
        Удалить диалог (только для администраторов) - по образцу Chatwoot.
        
        Args:
            request: HTTP запрос
            pk: ID диалога
        
        Returns:
            Response с подтверждением удаления
        
        Raises:
            403 Forbidden: Если пользователь не является администратором
        """
        conversation = self.get_object()
        
        # Проверка прав доступа: только администраторы могут удалять чаты
        if not (request.user.is_superuser or request.user.role == User.Role.ADMIN):
            return Response(
                {"detail": "У вас нет прав для удаления диалогов."},
                status=status.HTTP_403_FORBIDDEN,
            )
        
        conversation_id = conversation.id
        conversation.delete()
        
        return Response(
            {"status": "ok", "message": "Диалог успешно удалён"},
            status=status.HTTP_200_OK,
        )

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
            # Оптимизация запросов (по образцу Chatwoot):
            # - prefetch_related для attachments (убирает N+1)
            # - select_related для sender_user и sender_contact (если используются в сериализаторе)
            messages = conversation.messages.all().select_related(
                "sender_user", "sender_contact"
            ).prefetch_related("attachments")

            from django.utils.dateparse import parse_datetime

            since_raw = (request.query_params.get("since") or "").strip()
            before_raw = (request.query_params.get("before") or "").strip()
            before_id_raw = (request.query_params.get("before_id") or "").strip()
            limit_raw = (request.query_params.get("limit") or "").strip()

            try:
                limit = int(limit_raw) if limit_raw else 50
            except (ValueError, TypeError):
                limit = 50
            limit = max(1, min(limit, 200))

            since_dt = parse_datetime(since_raw) if since_raw else None
            before_dt = parse_datetime(before_raw) if before_raw else None
            try:
                before_id = int(before_id_raw) if before_id_raw else None
            except (ValueError, TypeError):
                before_id = None

            if since_dt:
                messages = messages.filter(created_at__gt=since_dt).order_by("created_at", "id")
                serializer = serializers.MessageSerializer(messages, many=True)
                return Response(serializer.data)

            if before_dt:
                # Берём "кусок" истории с конца (до before) и разворачиваем в хронологический порядок
                chunk = list(
                    (
                        messages.filter(created_at__lt=before_dt)
                        if not before_id
                        else messages.filter(
                            Q(created_at__lt=before_dt) | (Q(created_at=before_dt) & Q(id__lt=before_id))
                        )
                    )
                    .order_by("-created_at", "-id")[:limit]
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

    @action(detail=True, methods=["get"], url_path="stream")
    def stream(self, request, pk=None):
        """
        SSE стрим обновлений для операторской панели (по образцу Chatwoot).
        
        Args:
            request: HTTP запрос
            pk: ID диалога
        
        Returns:
            StreamingHttpResponse с событиями SSE
        
        События:
        - ready: начальное событие (handshake)
        - message.created: новое сообщение
        - conversation.updated: обновление диалога (статус, назначение)
        - conversation.typing_started/stopped: статус печати контакта
        - keep-alive: каждые 5 секунд для поддержания соединения
        
        Note:
            Соединение закрывается через 30 секунд, клиент должен переподключаться.
            Использует prefetch_related для оптимизации запросов сообщений.
        """
        conversation = self.get_object()
        
        # Проверка прав доступа
        user = request.user
        if not (user.is_superuser or user.role == User.Role.ADMIN):
            # Проверяем, что оператор назначен на диалог или имеет доступ к филиалу
            if conversation.assignee_id != user.id:
                # Проверяем доступ к филиалу через selectors
                visible_qs = selectors.visible_conversations_qs(user)
                if not visible_qs.filter(pk=conversation.pk).exists():
                    return Response(
                        {"detail": "У вас нет доступа к этому диалогу."},
                        status=status.HTTP_403_FORBIDDEN,
                    )
        
        from .typing import get_typing_status
        
        def event_stream():
            """SSE стрим для операторской панели."""
            started = time.time()
            last_keepalive = 0.0
            last_message_id = 0
            last_typing = None
            last_conversation_data = None
            
            # Первое событие (handshake)
            yield "event: ready\ndata: {}\n\n"
            
            while True:
                now = time.time()
                # Закрываем соединение через 30 секунд (по образцу Chatwoot)
                if now - started > 30:
                    break
                
                # Получаем новые сообщения
                new_messages = conversation.messages.filter(
                    id__gt=last_message_id
                ).select_related(
                    "sender_user", "sender_contact"
                ).prefetch_related("attachments").order_by("created_at", "id")
                
                messages_list = list(new_messages)
                
                # Отправляем события для новых сообщений
                for msg in messages_list:
                    if msg.id > last_message_id:
                        last_message_id = msg.id
                    
                    serializer = serializers.MessageSerializer(msg)
                    yield f"event: message.created\ndata: {json.dumps(serializer.data, ensure_ascii=False)}\n\n"
                
                # Проверяем статус печати
                typing_status = get_typing_status(conversation.id)
                contact_typing = typing_status.get("contact_typing") is False
                
                if last_typing != contact_typing:
                    last_typing = contact_typing
                    if contact_typing:
                        yield "event: conversation.typing_started\ndata: {}\n\n"
                    else:
                        yield "event: conversation.typing_stopped\ndata: {}\n\n"
                
                # Проверяем обновления диалога (статус, назначение и т.д.)
                conversation.refresh_from_db()
                current_data = {
                    "id": conversation.id,
                    "status": conversation.status,
                    "assignee_id": conversation.assignee_id,
                    "last_activity_at": conversation.last_activity_at.isoformat() if conversation.last_activity_at else None,
                }
                
                if last_conversation_data != current_data:
                    last_conversation_data = current_data
                    yield f"event: conversation.updated\ndata: {json.dumps(current_data, ensure_ascii=False)}\n\n"
                
                # Keep-alive каждые 5 секунд
                if now - last_keepalive > 5:
                    last_keepalive = now
                    yield ": keep-alive\n\n"
                
                time.sleep(1)
        
        resp = StreamingHttpResponse(event_stream(), content_type="text/event-stream")
        resp["Cache-Control"] = "no-cache"
        resp["X-Accel-Buffering"] = "no"  # nginx: не буферизовать SSE
        
        return resp

    @action(detail=True, methods=["get", "post"], url_path="typing")
    def typing(self, request, pk=None):
        """
        Управление статусом печати (по образцу Chatwoot).
        
        Args:
            request: HTTP запрос
            pk: ID диалога
        
        GET:
            Возвращает статус печати оператора и контакта.
        
        POST:
            Отмечает, что оператор печатает (TTL 8 секунд в Redis).
        
        Returns:
            GET: { operator_typing: bool, contact_typing: bool }
            POST: { status: "ok" }
        """
        conversation = self.get_object()
        from .typing import get_typing_status, set_operator_typing

        if request.method == "POST":
            set_operator_typing(conversation.id)
            return Response({"status": "ok"}, status=status.HTTP_200_OK)
        return Response(get_typing_status(conversation.id), status=status.HTTP_200_OK)


class CannedResponseViewSet(MessengerEnabledApiMixin, viewsets.ModelViewSet):
    """
    API для шаблонов ответов (canned responses) - по образцу Chatwoot.
    
    Оптимизирован для работы с фильтрацией по филиалу пользователя.
    """

    queryset = models.CannedResponse.objects.all()
    serializer_class = serializers.CannedResponseSerializer
    permission_classes = [IsAuthenticated, PolicyPermission]
    policy_resource_prefix = "api:messenger:canned-responses"

    def get_queryset(self):
        user = self.request.user
        qs = models.CannedResponse.objects.all()
        # Фильтруем по филиалу пользователя
        if user.branch_id:
            qs = qs.filter(branch_id=user.branch_id)
        return qs.order_by("title")

    def perform_create(self, serializer):
        """
        Сохранить шаблон ответа с автоматическим указанием создателя.
        
        Args:
            serializer: Сериализатор с валидированными данными
        """
        serializer.save(created_by=self.request.user)
