"""
DRF API для messenger.

Этап 2: операторский API для диалогов и шаблонов ответов.
"""

from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.renderers import BaseRenderer
from rest_framework.response import Response


class EventStreamRenderer(BaseRenderer):
    media_type = "text/event-stream"
    format = "text"

    def render(self, data, accepted_media_type=None, renderer_context=None):
        return data

from accounts.models import User
from policy.drf import PolicyPermission

from . import models, selectors, serializers, services
from .utils import ensure_messenger_enabled_api, validate_upload_safety
from django.db.models import Q
from django.http import StreamingHttpResponse
from django.utils import timezone
from rest_framework import serializers as drf_serializers
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


class ConversationViewSet(
    MessengerEnabledApiMixin,
    viewsets.ModelViewSet,
):
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
            # Предзагружаем сообщения и метки для уменьшения запросов при сериализации
            "messages", "labels"
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

        valid_statuses = {s[0] for s in models.Conversation.Status.choices}
        status_filter = (qp.get("status") or "").strip()
        if status_filter:
            if "," in status_filter:
                statuses = [s.strip() for s in status_filter.split(",") if s.strip() and s.strip() in valid_statuses]
                if statuses:
                    qs = qs.filter(status__in=statuses)
            elif status_filter in valid_statuses:
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

        Только менеджеры могут отмечать прочитанным — просмотр другими ролями
        не считается прочтением.

        Помимо assignee_last_read_at на диалоге, ставит read_at на все
        непрочитанные IN-сообщения — чтобы виджет мог показать чекмарки.
        """
        # Только менеджеры могут помечать прочитанным
        if request.user.role != User.Role.MANAGER:
            return Response({"status": "ignored"}, status=status.HTTP_200_OK)

        conversation = self.get_object()
        if conversation.assignee_id != request.user.id:
            return Response({"status": "ignored"}, status=status.HTTP_200_OK)

        # Обновляем last_seen оператора с троттлингом (по образцу Chatwoot)
        now = services.touch_assignee_last_seen(conversation, request.user)

        # Пометить все IN-сообщения как прочитанные оператором
        conversation.messages.filter(
            direction=models.Message.Direction.IN,
            read_at__isnull=True,
        ).update(read_at=now)

        return Response(
            {"status": "ok", "assignee_last_read_at": now.isoformat()},
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"], url_path="merge-contacts")
    def merge_contacts(self, request):
        """
        POST /api/conversations/merge-contacts/
        Body: {"primary_contact_id": "uuid", "merge_contact_id": "uuid"}

        Объединяет два контакта: переносит все диалоги merge_contact → primary_contact,
        обновляет пустые поля primary из merge, удаляет merge_contact.
        Аналог Chatwoot contact merge. Только для администраторов.
        """
        if not (request.user.is_superuser or request.user.role == User.Role.ADMIN):
            return Response({"detail": "Only administrators can merge contacts."}, status=status.HTTP_403_FORBIDDEN)
        primary_id = request.data.get("primary_contact_id")
        merge_id = request.data.get("merge_contact_id")
        if not primary_id or not merge_id:
            return Response({"detail": "primary_contact_id and merge_contact_id required"}, status=status.HTTP_400_BAD_REQUEST)
        if str(primary_id) == str(merge_id):
            return Response({"detail": "Cannot merge contact with itself"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            primary = models.Contact.objects.get(pk=primary_id)
            merge = models.Contact.objects.get(pk=merge_id)
        except (models.Contact.DoesNotExist, ValueError, TypeError):
            return Response({"detail": "Contact not found"}, status=status.HTTP_404_NOT_FOUND)

        from django.db import transaction
        with transaction.atomic():
            # Перенести диалоги
            moved = models.Conversation.objects.filter(contact=merge).update(contact=primary)
            # Перенести ContactInbox записи
            models.ContactInbox.objects.filter(contact=merge).update(contact=primary)
            # Заполнить пустые поля primary из merge
            for field in ("name", "email", "phone"):
                if not getattr(primary, field) and getattr(merge, field):
                    setattr(primary, field, getattr(merge, field))
            if not primary.region_detected and merge.region_detected:
                primary.region_detected = merge.region_detected
            primary.save()
            # Удалить merge-контакт
            merge.delete()

        return Response({
            "status": "ok",
            "primary_contact_id": str(primary.id),
            "conversations_moved": moved,
        })

    @action(detail=False, methods=["get"], url_path="unread-count")
    def unread_count(self, request):
        """GET /api/conversations/unread-count/ — общее число непрочитанных диалогов для sidebar badge."""
        from django.db.models import Q, F
        qs = selectors.visible_conversations_qs(request.user).filter(
            status__in=[models.Conversation.Status.OPEN, models.Conversation.Status.PENDING],
        )
        # Диалоги с непрочитанными IN-сообщениями (после assignee_last_read_at)
        count = qs.filter(
            Q(assignee=request.user),
            Q(assignee_last_read_at__isnull=True) | Q(messages__created_at__gt=F("assignee_last_read_at")),
            messages__direction=models.Message.Direction.IN,
        ).distinct().count()
        return Response({"unread_count": count})

    @action(detail=False, methods=["get"])
    def agents(self, request):
        """GET /api/conversations/agents/ — список менеджеров для @mention и назначения."""
        users = User.objects.filter(
            is_active=True, role=User.Role.MANAGER,
        ).values("id", "username", "first_name", "last_name").order_by("first_name", "last_name")
        result = [
            {
                "id": u["id"],
                "username": u["username"],
                "name": f'{u["first_name"]} {u["last_name"]}'.strip() or u["username"],
            }
            for u in users
        ]
        return Response(result)

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

    @action(detail=False, methods=["post"], url_path="bulk")
    def bulk(self, request):
        """
        Массовые действия над диалогами (по образцу Chatwoot).

        POST /api/conversations/bulk/
        Body: {"ids": [1,2,3], "action": "close"|"reopen"|"assign", "assignee_id": 5}
        """
        ids = request.data.get("ids", [])
        action_type = request.data.get("action", "")
        if not ids or not isinstance(ids, list):
            return Response({"detail": "ids is required."}, status=status.HTTP_400_BAD_REQUEST)

        qs = self.get_queryset().filter(id__in=ids)
        updated = 0

        if action_type == "close":
            updated = qs.update(status=models.Conversation.Status.CLOSED)
        elif action_type == "reopen":
            updated = qs.update(status=models.Conversation.Status.OPEN)
        elif action_type == "assign":
            assignee_id = request.data.get("assignee_id")
            if assignee_id:
                updated = qs.update(assignee_id=assignee_id)
            else:
                updated = qs.update(assignee=None)
        else:
            return Response({"detail": "Unknown action."}, status=status.HTTP_400_BAD_REQUEST)

        return Response({"status": "ok", "updated": updated})

    @action(detail=False, methods=["get"], url_path="notifications/stream",
            renderer_classes=[EventStreamRenderer])
    def notifications_stream(self, request):
        """
        Глобальный SSE стрим уведомлений оператора (аналог Chatwoot account-wide ActionCable).

        Транслирует ВСЕ новые входящие сообщения по всем видимым диалогам оператора.
        Оператор получает звук/push даже если смотрит другой диалог или вкладка в фоне.

        GET /api/conversations/notifications/stream/

        События:
        - ready: handshake
        - notification.message: новое входящее сообщение (conversation_id, contact_name, preview)
        - notification.assignment: новый диалог назначен оператору
        - keep-alive: каждые 5 секунд
        """
        user = request.user
        visible_qs = selectors.visible_conversations_qs(user)
        visible_ids = set(visible_qs.values_list("id", flat=True))

        def event_stream():
            started = time.time()
            last_keepalive = 0.0
            # Запоминаем последний ID сообщения на старте, чтобы отдавать только новые
            from django.db.models import Max
            last_seen_msg_id = models.Message.objects.filter(
                conversation_id__in=visible_ids
            ).aggregate(max_id=Max("id"))["max_id"] or 0

            # Запоминаем текущие assignee для отслеживания новых назначений
            my_assigned_ids = set(
                visible_qs.filter(assignee=user).values_list("id", flat=True)
            )

            yield "event: ready\ndata: {}\n\n"

            while True:
                now = time.time()
                if now - started > 55:  # 55сек (длиннее чем per-conversation)
                    break

                # Новые входящие сообщения по всем видимым диалогам
                new_messages = list(
                    models.Message.objects.filter(
                        conversation_id__in=visible_ids,
                        id__gt=last_seen_msg_id,
                        direction=models.Message.Direction.IN,
                    ).select_related(
                        "conversation__contact", "sender_contact"
                    ).order_by("id")[:20]
                )

                for msg in new_messages:
                    last_seen_msg_id = max(last_seen_msg_id, msg.id)
                    contact_name = ""
                    if msg.conversation and msg.conversation.contact:
                        c = msg.conversation.contact
                        contact_name = c.name or c.email or c.phone or ""
                    payload = {
                        "message_id": msg.id,
                        "conversation_id": msg.conversation_id,
                        "contact_name": contact_name,
                        "preview": (msg.body or "")[:140],
                        "created_at": msg.created_at.isoformat() if msg.created_at else None,
                    }
                    yield f"event: notification.message\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

                # Проверяем новые назначения на текущего оператора
                current_assigned = set(
                    visible_qs.filter(assignee=user).values_list("id", flat=True)
                )
                newly_assigned = current_assigned - my_assigned_ids
                for conv_id in newly_assigned:
                    try:
                        conv = visible_qs.get(id=conv_id)
                        contact_name = ""
                        if conv.contact:
                            contact_name = conv.contact.name or conv.contact.email or conv.contact.phone or ""
                        payload = {
                            "conversation_id": conv_id,
                            "contact_name": contact_name,
                            "status": conv.status,
                        }
                        yield f"event: notification.assignment\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                    except models.Conversation.DoesNotExist:
                        pass
                my_assigned_ids = current_assigned

                # Обновляем список видимых диалогов (могут появиться новые)
                if int(now - started) % 15 == 0 and int(now - started) > 0:
                    visible_ids.update(
                        selectors.visible_conversations_qs(user).values_list("id", flat=True)
                    )

                # Keep-alive
                if now - last_keepalive > 5:
                    last_keepalive = now
                    yield ": keep-alive\n\n"

                time.sleep(2)

        resp = StreamingHttpResponse(event_stream(), content_type="text/event-stream")
        resp["Cache-Control"] = "no-cache"
        resp["X-Accel-Buffering"] = "no"
        return resp

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
            # Только менеджеры могут отправлять сообщения клиентам
            if request.user.role != User.Role.MANAGER:
                return Response(
                    {"detail": "Только менеджеры могут отвечать в чатах."},
                    status=status.HTTP_403_FORBIDDEN,
                )

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

            if len(body) > models.Message.MAX_CONTENT_LENGTH:
                return Response(
                    {"detail": f"Текст сообщения слишком длинный (максимум {models.Message.MAX_CONTENT_LENGTH} символов)."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            for f in files:
                safety_error = validate_upload_safety(f)
                if safety_error:
                    return Response({"detail": safety_error}, status=status.HTTP_400_BAD_REQUEST)

            from django.db import transaction
            from .services import record_message

            # Атомарно: сообщение + вложения
            with transaction.atomic():
                message = record_message(
                    conversation=conversation,
                    direction=direction,
                    body=body or "",
                    sender_user=request.user,
                    sender_contact=None,
                )

                for f in files:
                    models.MessageAttachment.objects.create(message=message, file=f)

            # @mentions в внутренних заметках — уведомить упомянутых (вне транзакции)
            if direction == models.Message.Direction.INTERNAL and body:
                self._process_mentions(body, conversation, request.user)

            # Перезагрузить сообщение из БД, чтобы получить вложения в сериализаторе
            message.refresh_from_db()

            return Response(serializers.MessageSerializer(message).data, status=status.HTTP_201_CREATED)

    @staticmethod
    def _process_mentions(body, conversation, author):
        """Найти @username в тексте и отправить уведомления упомянутым пользователям."""
        import re
        mentions = set(re.findall(r'@(\w+)', body))
        if not mentions:
            return
        mentioned_users = User.objects.filter(username__in=mentions, is_active=True).exclude(pk=author.pk)
        for user in mentioned_users:
            try:
                from notifications.service import notify
                notify(
                    user=user,
                    title=f"{author.get_full_name() or author.username} упомянул вас",
                    body=body[:200],
                    url=f"/messenger/?conversation={conversation.id}",
                    kind="info",
                    dedupe_seconds=60,
                )
                # Также отправить push-уведомление
                from .push import send_push_to_user
                send_push_to_user(
                    user=user,
                    title=f"Упоминание от {author.get_full_name() or author.username}",
                    body=body[:100],
                    url=f"/messenger/?conversation={conversation.id}",
                    tag=f"mention-{conversation.id}",
                )
            except Exception:
                pass

    @action(detail=True, methods=["get"], url_path="stream", renderer_classes=[EventStreamRenderer])
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
        # Фильтруем по филиалу пользователя + глобальные (без филиала)
        if user.branch_id:
            qs = qs.filter(Q(branch_id=user.branch_id) | Q(branch__isnull=True))
        return qs.order_by("title")

    def perform_create(self, serializer):
        """
        Сохранить шаблон ответа с автоматическим указанием создателя.
        
        Args:
            serializer: Сериализатор с валидированными данными
        """
        serializer.save(created_by=self.request.user)


class ConversationLabelViewSet(MessengerEnabledApiMixin, viewsets.ModelViewSet):
    """API для меток диалогов."""
    queryset = models.ConversationLabel.objects.all()
    serializer_class = serializers.ConversationLabelSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return models.ConversationLabel.objects.order_by("title")


class PushSubscriptionViewSet(MessengerEnabledApiMixin, viewsets.ViewSet):
    """
    API для управления Browser Push подписками (Web Push API + VAPID).
    Аналог Chatwoot notification_subscriptions.
    """
    permission_classes = [IsAuthenticated]

    @action(detail=False, methods=["get"], url_path="vapid-key")
    def vapid_key(self, request):
        """GET /api/push/vapid-key/ — публичный VAPID-ключ для подписки."""
        from django.conf import settings
        return Response({
            "public_key": getattr(settings, "VAPID_PUBLIC_KEY", ""),
        })

    @action(detail=False, methods=["post"], url_path="subscribe")
    def subscribe(self, request):
        """POST /api/push/subscribe/ — сохранить push-подписку."""
        endpoint = request.data.get("endpoint", "")
        p256dh = request.data.get("p256dh", "")
        auth = request.data.get("auth", "")

        if not endpoint or not p256dh or not auth:
            return Response(
                {"detail": "endpoint, p256dh, auth are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        sub, created = models.PushSubscription.objects.update_or_create(
            endpoint=endpoint,
            defaults={
                "user": request.user,
                "p256dh": p256dh,
                "auth": auth,
                "is_active": True,
            },
        )
        return Response({"status": "subscribed", "created": created})

    @action(detail=False, methods=["post"], url_path="unsubscribe")
    def unsubscribe(self, request):
        """POST /api/push/unsubscribe/ — деактивировать push-подписку."""
        endpoint = request.data.get("endpoint", "")
        if endpoint:
            models.PushSubscription.objects.filter(
                user=request.user, endpoint=endpoint
            ).update(is_active=False)
        return Response({"status": "unsubscribed"})


class CampaignViewSet(MessengerEnabledApiMixin, viewsets.ModelViewSet):
    """API для проактивных кампаний (CRUD для операторов)."""
    queryset = models.Campaign.objects.all()
    permission_classes = [IsAuthenticated]

    class CampaignSerializer(drf_serializers.ModelSerializer):
        class Meta:
            model = models.Campaign
            fields = (
                "id", "inbox", "title", "message", "url_pattern",
                "time_on_page", "status", "only_during_business_hours",
                "created_at",
            )

    serializer_class = CampaignSerializer

    def get_queryset(self):
        return models.Campaign.objects.select_related("inbox").order_by("-created_at")


class AutomationRuleViewSet(MessengerEnabledApiMixin, viewsets.ModelViewSet):
    """API для правил автоматизации (CRUD для администраторов)."""
    queryset = models.AutomationRule.objects.all()
    permission_classes = [IsAuthenticated]

    class AutomationRuleSerializer(drf_serializers.ModelSerializer):
        class Meta:
            model = models.AutomationRule
            fields = (
                "id", "inbox", "name", "description", "event_name",
                "conditions", "actions", "is_active", "created_at",
            )

    serializer_class = AutomationRuleSerializer

    def get_queryset(self):
        return models.AutomationRule.objects.select_related("inbox").order_by("-created_at")


class ReportingViewSet(MessengerEnabledApiMixin, viewsets.ViewSet):
    """
    API аналитики мессенджера (аналог Chatwoot reports).
    GET /api/messenger-reports/overview/ — обзор метрик.
    """
    permission_classes = [IsAuthenticated]

    @action(detail=False, methods=["get"], url_path="overview")
    def overview(self, request):
        """Обзорные метрики за период."""
        from django.db.models import Avg, Count
        from datetime import timedelta

        days = int(request.query_params.get("days", "7"))
        since = timezone.now() - timedelta(days=days)

        events = models.ReportingEvent.objects.filter(created_at__gte=since)

        # Средний FRT
        avg_frt = events.filter(
            name=models.ReportingEvent.EventType.FIRST_RESPONSE
        ).aggregate(avg=Avg("value"))["avg"]

        # Средний reply time
        avg_reply = events.filter(
            name=models.ReportingEvent.EventType.REPLY_TIME
        ).aggregate(avg=Avg("value"))["avg"]

        # Решённые диалоги
        resolved_count = events.filter(
            name=models.ReportingEvent.EventType.CONVERSATION_RESOLVED
        ).count()

        # Общее кол-во диалогов за период
        total_conversations = models.Conversation.objects.filter(
            created_at__gte=since
        ).count()

        # CSAT
        rated = models.Conversation.objects.filter(
            rated_at__gte=since, rating_score__gt=0
        )
        avg_csat = rated.aggregate(avg=Avg("rating_score"))["avg"]
        csat_count = rated.count()

        return Response({
            "period_days": days,
            "total_conversations": total_conversations,
            "resolved_conversations": resolved_count,
            "avg_first_response_time_seconds": round(avg_frt, 1) if avg_frt else None,
            "avg_reply_time_seconds": round(avg_reply, 1) if avg_reply else None,
            "avg_csat_score": round(avg_csat, 2) if avg_csat else None,
            "csat_responses_count": csat_count,
        })


class MacroSerializer(drf_serializers.ModelSerializer):
    class Meta:
        model = models.Macro
        fields = ("id", "name", "actions", "visibility", "user", "created_at")
        read_only_fields = ("user", "created_at")


class MacroViewSet(MessengerEnabledApiMixin, viewsets.ModelViewSet):
    """
    CRUD для макросов (аналог Chatwoot /api/v1/accounts/:id/macros).
    Личные + общие макросы. Есть action execute для применения к диалогу.
    """
    serializer_class = MacroSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        from django.db.models import Q
        user = self.request.user
        return models.Macro.objects.filter(
            Q(user=user) | Q(visibility="global")
        ).order_by("name")

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    @action(detail=True, methods=["post"])
    def execute(self, request, pk=None):
        """
        POST /api/macros/{id}/execute/
        Body: {"conversation_id": 123}
        Выполняет все действия макроса на указанном диалоге.
        """
        macro = self.get_object()
        conversation_id = request.data.get("conversation_id")
        if not conversation_id:
            return Response({"detail": "conversation_id required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            conversation = models.Conversation.objects.get(pk=conversation_id)
        except models.Conversation.DoesNotExist:
            return Response({"detail": "Conversation not found"}, status=status.HTTP_404_NOT_FOUND)

        from .automation import _execute_actions
        _execute_actions(macro.actions, conversation, message=None)

        return Response({"status": "ok", "actions_count": len(macro.actions)})
