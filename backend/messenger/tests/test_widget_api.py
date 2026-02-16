"""
Тесты публичного Widget API (Этап 3).

Проверяем:
- bootstrap: создание/получение сессии, создание/поиск диалога
- send: создание входящих сообщений, валидация токенов
- poll: получение сообщений от операторов, фильтрация по since_id
- feature flag: отключение messenger возвращает 404
"""

from django.test import TestCase
from django.contrib.auth import get_user_model
from django.conf import settings
from rest_framework.test import APIClient
from rest_framework import status

from accounts.models import Branch
from messenger.models import Inbox, Contact, Conversation, Message
from messenger.utils import get_widget_session

User = get_user_model()


class WidgetAPITests(TestCase):
    """
    Тесты публичного Widget API.
    """

    def setUp(self):
        """Подготовка тестовых данных."""
        self.branch = Branch.objects.create(code="test_branch", name="Test Branch")

        self.inbox = Inbox.objects.create(
            name="Test Inbox",
            branch=self.branch,
            widget_token="test_widget_token_123",
            is_active=True,
        )

        self.contact = Contact.objects.create(
            external_id="visitor_123",
            name="Test Visitor",
            email="visitor@test.com",
        )

        self.conversation = Conversation.objects.create(
            inbox=self.inbox,
            contact=self.contact,
            branch=self.branch,
            status=Conversation.Status.OPEN,
        )

        # Включаем messenger для тестов
        self.original_messenger_enabled = getattr(settings, "MESSENGER_ENABLED", False)
        settings.MESSENGER_ENABLED = True

    def tearDown(self):
        """Восстановление настроек после тестов."""
        settings.MESSENGER_ENABLED = self.original_messenger_enabled

    # ========================================================================
    # Bootstrap тесты
    # ========================================================================

    def test_bootstrap_creates_session_and_conversation(self):
        """Bootstrap успешно создаёт сессию и диалог."""
        client = APIClient()

        response = client.post(
            "/api/widget/bootstrap/",
            {
                "widget_token": "test_widget_token_123",
                "contact_external_id": "new_visitor_456",
                "name": "New Visitor",
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("widget_session_token", response.data)
        self.assertIn("conversation_id", response.data)

        # Проверяем, что сессия создана
        session = get_widget_session(response.data["widget_session_token"])
        self.assertIsNotNone(session)
        self.assertEqual(session.inbox_id, self.inbox.id)
        self.assertEqual(session.conversation_id, response.data["conversation_id"])

        # Проверяем, что диалог создан
        conversation = Conversation.objects.get(id=response.data["conversation_id"])
        self.assertEqual(conversation.inbox, self.inbox)
        self.assertEqual(conversation.contact.external_id, "new_visitor_456")

    def test_bootstrap_finds_existing_conversation(self):
        """Bootstrap находит существующий активный диалог."""
        client = APIClient()

        response = client.post(
            "/api/widget/bootstrap/",
            {
                "widget_token": "test_widget_token_123",
                "contact_external_id": "visitor_123",
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["conversation_id"], self.conversation.id)

    def test_bootstrap_invalid_widget_token_returns_404(self):
        """Bootstrap с неверным widget_token возвращает 404."""
        client = APIClient()

        response = client.post(
            "/api/widget/bootstrap/",
            {
                "widget_token": "invalid_token",
                "contact_external_id": "visitor_123",
            },
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertIn("detail", response.data)

    def test_bootstrap_inactive_inbox_returns_404(self):
        """Bootstrap с неактивным inbox возвращает 404."""
        self.inbox.is_active = False
        self.inbox.save()

        client = APIClient()

        response = client.post(
            "/api/widget/bootstrap/",
            {
                "widget_token": "test_widget_token_123",
                "contact_external_id": "visitor_123",
            },
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_bootstrap_returns_initial_messages(self):
        """Bootstrap возвращает последние сообщения диалога."""
        # Создаём несколько сообщений от оператора
        operator = User.objects.create_user(
            username="operator",
            email="operator@test.com",
            password="testpass",
        )

        Message.objects.create(
            conversation=self.conversation,
            direction=Message.Direction.OUT,
            body="Message 1",
            sender_user=operator,
        )
        Message.objects.create(
            conversation=self.conversation,
            direction=Message.Direction.OUT,
            body="Message 2",
            sender_user=operator,
        )

        client = APIClient()

        response = client.post(
            "/api/widget/bootstrap/",
            {
                "widget_token": "test_widget_token_123",
                "contact_external_id": "visitor_123",
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("initial_messages", response.data)
        self.assertEqual(len(response.data["initial_messages"]), 2)

    # ========================================================================
    # Send тесты
    # ========================================================================

    def test_send_creates_inbound_message(self):
        """Send успешно создаёт входящее сообщение."""
        client = APIClient()

        # Сначала получаем сессию через bootstrap
        bootstrap_response = client.post(
            "/api/widget/bootstrap/",
            {
                "widget_token": "test_widget_token_123",
                "contact_external_id": "visitor_123",
            },
        )
        widget_session_token = bootstrap_response.data["widget_session_token"]

        # Отправляем сообщение
        response = client.post(
            "/api/widget/send/",
            {
                "widget_token": "test_widget_token_123",
                "widget_session_token": widget_session_token,
                "body": "Hello from visitor",
            },
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn("id", response.data)
        self.assertIn("created_at", response.data)

        # Проверяем, что сообщение создано с правильными полями
        message = Message.objects.get(id=response.data["id"])
        self.assertEqual(message.direction, Message.Direction.IN)
        self.assertEqual(message.body, "Hello from visitor")
        self.assertEqual(message.sender_contact, self.contact)
        self.assertIsNone(message.sender_user)

    def test_send_invalid_session_token_returns_401(self):
        """Send с неверным session_token возвращает 401."""
        client = APIClient()

        response = client.post(
            "/api/widget/send/",
            {
                "widget_token": "test_widget_token_123",
                "widget_session_token": "invalid_session_token",
                "body": "Test message",
            },
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertIn("detail", response.data)

    def test_send_mismatch_inbox_returns_403(self):
        """Send с несовпадающим inbox_id возвращает 403."""
        # Создаём другой inbox
        other_branch = Branch.objects.create(code="other_branch", name="Other Branch")
        other_inbox = Inbox.objects.create(
            name="Other Inbox",
            branch=other_branch,
            widget_token="other_token_456",
            is_active=True,
        )

        client = APIClient()

        # Получаем сессию для первого inbox
        bootstrap_response = client.post(
            "/api/widget/bootstrap/",
            {
                "widget_token": "test_widget_token_123",
                "contact_external_id": "visitor_123",
            },
        )
        widget_session_token = bootstrap_response.data["widget_session_token"]

        # Пытаемся отправить с другим widget_token
        response = client.post(
            "/api/widget/send/",
            {
                "widget_token": "other_token_456",  # Другой inbox
                "widget_session_token": widget_session_token,  # Сессия от первого inbox
                "body": "Test message",
            },
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_send_empty_body_returns_400(self):
        """Send с пустым телом сообщения возвращает 400."""
        client = APIClient()

        bootstrap_response = client.post(
            "/api/widget/bootstrap/",
            {
                "widget_token": "test_widget_token_123",
                "contact_external_id": "visitor_123",
            },
        )
        widget_session_token = bootstrap_response.data["widget_session_token"]

        response = client.post(
            "/api/widget/send/",
            {
                "widget_token": "test_widget_token_123",
                "widget_session_token": widget_session_token,
                "body": "   ",  # Только пробелы
            },
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    # ========================================================================
    # Poll тесты
    # ========================================================================

    def test_poll_returns_outbound_messages(self):
        """Poll возвращает только исходящие сообщения от операторов."""
        operator = User.objects.create_user(
            username="operator",
            email="operator@test.com",
            password="testpass",
        )

        # Создаём сообщения разных типов
        Message.objects.create(
            conversation=self.conversation,
            direction=Message.Direction.IN,
            body="Inbound message",
            sender_contact=self.contact,
        )
        out_msg1 = Message.objects.create(
            conversation=self.conversation,
            direction=Message.Direction.OUT,
            body="Outbound message 1",
            sender_user=operator,
        )
        out_msg2 = Message.objects.create(
            conversation=self.conversation,
            direction=Message.Direction.OUT,
            body="Outbound message 2",
            sender_user=operator,
        )

        client = APIClient()

        # Получаем сессию
        bootstrap_response = client.post(
            "/api/widget/bootstrap/",
            {
                "widget_token": "test_widget_token_123",
                "contact_external_id": "visitor_123",
            },
        )
        widget_session_token = bootstrap_response.data["widget_session_token"]

        # Poll
        response = client.get(
            "/api/widget/poll/",
            {
                "widget_token": "test_widget_token_123",
                "widget_session_token": widget_session_token,
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("messages", response.data)
        self.assertEqual(len(response.data["messages"]), 2)  # Только OUT сообщения

        message_ids = [msg["id"] for msg in response.data["messages"]]
        self.assertIn(out_msg1.id, message_ids)
        self.assertIn(out_msg2.id, message_ids)

    def test_poll_filters_by_since_id(self):
        """Poll фильтрует сообщения по since_id."""
        operator = User.objects.create_user(
            username="operator",
            email="operator@test.com",
            password="testpass",
        )

        msg1 = Message.objects.create(
            conversation=self.conversation,
            direction=Message.Direction.OUT,
            body="Message 1",
            sender_user=operator,
        )
        msg2 = Message.objects.create(
            conversation=self.conversation,
            direction=Message.Direction.OUT,
            body="Message 2",
            sender_user=operator,
        )

        client = APIClient()

        bootstrap_response = client.post(
            "/api/widget/bootstrap/",
            {
                "widget_token": "test_widget_token_123",
                "contact_external_id": "visitor_123",
            },
        )
        widget_session_token = bootstrap_response.data["widget_session_token"]

        # Poll с since_id = msg1.id (должен вернуть только msg2)
        response = client.get(
            "/api/widget/poll/",
            {
                "widget_token": "test_widget_token_123",
                "widget_session_token": widget_session_token,
                "since_id": msg1.id,
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["messages"]), 1)
        self.assertEqual(response.data["messages"][0]["id"], msg2.id)

    def test_poll_invalid_session_token_returns_401(self):
        """Poll с неверным session_token возвращает 401."""
        client = APIClient()

        response = client.get(
            "/api/widget/poll/",
            {
                "widget_token": "test_widget_token_123",
                "widget_session_token": "invalid_token",
            },
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_poll_mismatch_inbox_returns_403(self):
        """Poll с несовпадающим inbox_id возвращает 403."""
        other_branch = Branch.objects.create(code="other_branch", name="Other Branch")
        other_inbox = Inbox.objects.create(
            name="Other Inbox",
            branch=other_branch,
            widget_token="other_token_456",
            is_active=True,
        )

        client = APIClient()

        bootstrap_response = client.post(
            "/api/widget/bootstrap/",
            {
                "widget_token": "test_widget_token_123",
                "contact_external_id": "visitor_123",
            },
        )
        widget_session_token = bootstrap_response.data["widget_session_token"]

        response = client.get(
            "/api/widget/poll/",
            {
                "widget_token": "other_token_456",
                "widget_session_token": widget_session_token,
            },
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    # ========================================================================
    # Feature flag тесты
    # ========================================================================

    def test_widget_endpoints_disabled_when_messenger_off(self):
        """Все widget endpoints возвращают 404 при отключённом messenger."""
        settings.MESSENGER_ENABLED = False

        client = APIClient()

        endpoints = [
            ("POST", "/api/widget/bootstrap/", {"widget_token": "test", "contact_external_id": "visitor"}),
            ("POST", "/api/widget/send/", {"widget_token": "test", "widget_session_token": "session", "body": "test"}),
            ("GET", "/api/widget/poll/", {"widget_token": "test", "widget_session_token": "session"}),
        ]

        for method, endpoint, data in endpoints:
            if method == "POST":
                response = client.post(endpoint, data)
            else:
                response = client.get(endpoint, data)

            self.assertEqual(
                response.status_code,
                status.HTTP_404_NOT_FOUND,
                f"Endpoint {endpoint} должен возвращать 404 при отключённом messenger",
            )
