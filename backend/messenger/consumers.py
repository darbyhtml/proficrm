"""
WebSocket consumer для messenger — real-time обновления для операторов.

Два типа подключений:
1. OperatorConsumer — для операторов CRM (аутентификация по session/JWT)
2. WidgetConsumer — для виджета посетителя (аутентификация по session_token)

Каналы (groups):
- conversation_{id} — обновления конкретного диалога (сообщения, typing, статус)
- inbox_{id} — обновления по inbox (новые диалоги, назначения)
- operator_{user_id} — личные уведомления оператору
"""

import json
import logging
from datetime import datetime

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebSocketConsumer
from django.utils import timezone

logger = logging.getLogger("messenger.ws")


class OperatorConsumer(AsyncJsonWebSocketConsumer):
    """
    WebSocket для операторов CRM.

    Подключение: ws://.../ws/messenger/operator/
    Аутентификация через Django session (cookie).

    После connect оператор автоматически подписан на:
    - operator_{user_id} (личные уведомления)
    - inbox_{id} для каждого inbox, к которому имеет доступ

    Клиент может подписаться/отписаться от конкретного диалога:
    - {"action": "subscribe", "conversation_id": 123}
    - {"action": "unsubscribe", "conversation_id": 123}
    - {"action": "typing", "conversation_id": 123}
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = None
        self.user_id = None
        self.subscribed_conversations = set()
        self.subscribed_inboxes = set()

    async def connect(self):
        self.user = self.scope.get("user")
        if not self.user or not self.user.is_authenticated:
            await self.close(code=4401)
            return

        self.user_id = self.user.pk
        await self.accept()

        # Подписка на личный канал оператора
        await self.channel_layer.group_add(
            f"operator_{self.user_id}",
            self.channel_name,
        )

        # Подписка на все inbox, к которым есть доступ
        inbox_ids = await self._get_accessible_inbox_ids()
        for inbox_id in inbox_ids:
            await self.channel_layer.group_add(
                f"inbox_{inbox_id}",
                self.channel_name,
            )
            self.subscribed_inboxes.add(inbox_id)

        await self._set_operator_online()

        logger.info("Operator %s connected via WebSocket", self.user_id)

    async def disconnect(self, close_code):
        if not self.user_id:
            return

        # Отписка от всех каналов
        await self.channel_layer.group_discard(
            f"operator_{self.user_id}",
            self.channel_name,
        )
        for inbox_id in self.subscribed_inboxes:
            await self.channel_layer.group_discard(
                f"inbox_{inbox_id}",
                self.channel_name,
            )
        for conv_id in self.subscribed_conversations:
            await self.channel_layer.group_discard(
                f"conversation_{conv_id}",
                self.channel_name,
            )

        await self._set_operator_offline()
        logger.info("Operator %s disconnected (code=%s)", self.user_id, close_code)

    async def receive_json(self, content, **kwargs):
        action = content.get("action")

        if action == "subscribe" and "conversation_id" in content:
            conv_id = int(content["conversation_id"])
            if await self._can_access_conversation(conv_id):
                await self.channel_layer.group_add(
                    f"conversation_{conv_id}",
                    self.channel_name,
                )
                self.subscribed_conversations.add(conv_id)

        elif action == "unsubscribe" and "conversation_id" in content:
            conv_id = int(content["conversation_id"])
            await self.channel_layer.group_discard(
                f"conversation_{conv_id}",
                self.channel_name,
            )
            self.subscribed_conversations.discard(conv_id)

        elif action == "typing" and "conversation_id" in content:
            conv_id = int(content["conversation_id"])
            if conv_id in self.subscribed_conversations:
                await self.channel_layer.group_send(
                    f"conversation_{conv_id}",
                    {
                        "type": "typing.indicator",
                        "user_id": self.user_id,
                        "is_typing": content.get("is_typing", True),
                    },
                )

        elif action == "ping":
            await self.send_json({"type": "pong", "ts": timezone.now().isoformat()})

    # ─── Group message handlers ──────────────────────────────────────

    async def new_message(self, event):
        """Новое сообщение в диалоге."""
        await self.send_json({
            "type": "new_message",
            "conversation_id": event.get("conversation_id"),
            "message": event.get("message"),
        })

    async def conversation_updated(self, event):
        """Обновление диалога (статус, assignee и т.д.)."""
        await self.send_json({
            "type": "conversation_updated",
            "conversation_id": event.get("conversation_id"),
            "changes": event.get("changes"),
        })

    async def new_conversation(self, event):
        """Новый диалог в inbox."""
        await self.send_json({
            "type": "new_conversation",
            "conversation": event.get("conversation"),
        })

    async def typing_indicator(self, event):
        """Индикатор набора текста."""
        if event.get("user_id") != self.user_id:
            await self.send_json({
                "type": "typing",
                "conversation_id": event.get("conversation_id"),
                "user_id": event.get("user_id"),
                "is_typing": event.get("is_typing", True),
            })

    async def operator_notification(self, event):
        """Личное уведомление оператору."""
        await self.send_json({
            "type": "notification",
            "title": event.get("title"),
            "body": event.get("body"),
            "conversation_id": event.get("conversation_id"),
        })

    # ─── DB helpers ──────────────────────────────────────────────────

    @database_sync_to_async
    def _get_accessible_inbox_ids(self):
        from .models import Inbox
        qs = Inbox.objects.filter(is_active=True)
        if hasattr(self.user, 'branch_id') and self.user.branch_id:
            qs = qs.filter(branch_id=self.user.branch_id)
        return list(qs.values_list("id", flat=True))

    @database_sync_to_async
    def _can_access_conversation(self, conversation_id):
        from .models import Conversation
        try:
            conv = Conversation.objects.select_related("inbox").get(pk=conversation_id)
            if hasattr(self.user, 'branch_id') and self.user.branch_id:
                return conv.inbox.branch_id == self.user.branch_id
            return True
        except Conversation.DoesNotExist:
            return False

    @database_sync_to_async
    def _set_operator_online(self):
        from .models import AgentProfile
        AgentProfile.objects.update_or_create(
            user=self.user,
            defaults={"status": AgentProfile.Status.ONLINE, "last_seen_at": timezone.now()},
        )

    @database_sync_to_async
    def _set_operator_offline(self):
        from .models import AgentProfile
        AgentProfile.objects.filter(user=self.user).update(
            status=AgentProfile.Status.OFFLINE,
            last_seen_at=timezone.now(),
        )


class WidgetConsumer(AsyncJsonWebSocketConsumer):
    """
    WebSocket для виджета посетителя.

    Подключение: ws://.../ws/messenger/widget/{widget_token}/
    Аутентификация по session_token (передаётся в первом сообщении).

    После аутентификации подписан на:
    - conversation_{id} — обновления своего диалога
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.widget_token = None
        self.session_token = None
        self.conversation_id = None
        self.authenticated = False

    async def connect(self):
        self.widget_token = self.scope["url_route"]["kwargs"].get("widget_token", "")
        inbox_exists = await self._validate_inbox()
        if not inbox_exists:
            await self.close(code=4404)
            return
        await self.accept()

    async def disconnect(self, close_code):
        if self.conversation_id:
            await self.channel_layer.group_discard(
                f"conversation_{self.conversation_id}",
                self.channel_name,
            )

    async def receive_json(self, content, **kwargs):
        action = content.get("action")

        if action == "authenticate":
            session_token = content.get("session_token", "")
            result = await self._authenticate_session(session_token)
            if result:
                self.authenticated = True
                self.session_token = session_token
                self.conversation_id = result
                await self.channel_layer.group_add(
                    f"conversation_{self.conversation_id}",
                    self.channel_name,
                )
                await self.send_json({"type": "authenticated", "conversation_id": self.conversation_id})
            else:
                await self.send_json({"type": "error", "code": "auth_failed"})
                await self.close(code=4401)

        elif not self.authenticated:
            await self.send_json({"type": "error", "code": "not_authenticated"})
            return

        elif action == "typing":
            if self.conversation_id:
                await self.channel_layer.group_send(
                    f"conversation_{self.conversation_id}",
                    {
                        "type": "typing.indicator",
                        "user_id": None,  # visitor
                        "is_typing": content.get("is_typing", True),
                    },
                )

        elif action == "ping":
            await self.send_json({"type": "pong"})

    # ─── Group message handlers ──────────────────────────────────────

    async def new_message(self, event):
        """Новое сообщение — показать посетителю только исходящие (от оператора)."""
        msg = event.get("message", {})
        if msg.get("direction") == "out":
            await self.send_json({
                "type": "new_message",
                "message": msg,
            })

    async def typing_indicator(self, event):
        """Оператор печатает."""
        if event.get("user_id") is not None:
            await self.send_json({
                "type": "typing",
                "is_typing": event.get("is_typing", True),
            })

    async def conversation_updated(self, event):
        """Статус диалога изменился."""
        await self.send_json({
            "type": "conversation_updated",
            "changes": event.get("changes"),
        })

    # ─── DB helpers ──────────────────────────────────────────────────

    @database_sync_to_async
    def _validate_inbox(self):
        from .models import Inbox
        return Inbox.objects.filter(widget_token=self.widget_token, is_active=True).exists()

    @database_sync_to_async
    def _authenticate_session(self, session_token):
        from .models import Contact
        try:
            contact = Contact.objects.get(session_token=session_token)
            from .models import Conversation
            conv = Conversation.objects.filter(
                contact=contact,
                inbox__widget_token=self.widget_token,
            ).exclude(
                status=Conversation.Status.CLOSED,
            ).order_by("-created_at").first()
            return conv.id if conv else None
        except Contact.DoesNotExist:
            return None
