"""
Тесты безопасности API messenger (Этап 2).

Обязательные проверки:
- Visibility: оператор не видит чужой филиал, admin видит всё, SELF scope видит только назначенные
- Update whitelist: запрет изменения inbox/branch/contact через API
- Message invariants: валидация direction/sender
- Feature flag: отключение messenger возвращает 404
"""

from django.test import TestCase
from django.contrib.auth import get_user_model
from django.conf import settings
from rest_framework.test import APIClient
from rest_framework import status

from accounts.models import Branch
from companies.models import Region
from messenger.models import Inbox, Contact, Conversation, Message
from messenger import selectors

User = get_user_model()


def _get_response_data(response):
    """
    Вспомогательная функция для получения данных из DRF response.
    Обрабатывает как пагинированные ответы (dict с "results"), так и обычные списки.
    """
    if isinstance(response.data, dict) and "results" in response.data:
        return response.data["results"]
    return response.data if isinstance(response.data, list) else [response.data]


class MessengerAPISecurityTests(TestCase):
    """
    Тесты безопасности API messenger.

    Проверяем:
    - видимость данных по филиалам и ролям
    - запрет изменения системных полей через API
    - валидацию инвариантов сообщений
    - работу feature-флага
    """

    def setUp(self):
        """Подготовка тестовых данных."""
        # Создаём филиалы
        self.branch_a = Branch.objects.create(code="branch_a", name="Филиал A")
        self.branch_b = Branch.objects.create(code="branch_b", name="Филиал B")

        # Создаём пользователей
        self.admin = User.objects.create_user(
            username="admin",
            email="admin@test.com",
            password="testpass",
            role=User.Role.ADMIN,
        )

        self.operator_a = User.objects.create_user(
            username="operator_a",
            email="operator_a@test.com",
            password="testpass",
            role=User.Role.MANAGER,  # Используем MANAGER как оператора
            branch=self.branch_a,
            data_scope=User.DataScope.BRANCH,
        )

        self.operator_b = User.objects.create_user(
            username="operator_b",
            email="operator_b@test.com",
            password="testpass",
            role=User.Role.MANAGER,
            branch=self.branch_b,
            data_scope=User.DataScope.BRANCH,
        )

        self.operator_self = User.objects.create_user(
            username="operator_self",
            email="operator_self@test.com",
            password="testpass",
            role=User.Role.MANAGER,
            branch=self.branch_a,
            data_scope=User.DataScope.SELF,
        )

        # Создаём inbox'ы
        self.inbox_a = Inbox.objects.create(
            name="Inbox A",
            branch=self.branch_a,
            widget_token="token_a_123",
            is_active=True,
        )

        self.inbox_b = Inbox.objects.create(
            name="Inbox B",
            branch=self.branch_b,
            widget_token="token_b_456",
            is_active=True,
        )

        # Создаём контакты
        self.contact_a = Contact.objects.create(
            name="Contact A",
            email="contact_a@test.com",
        )

        self.contact_b = Contact.objects.create(
            name="Contact B",
            email="contact_b@test.com",
        )

        # Создаём диалоги
        self.conversation_a = Conversation.objects.create(
            inbox=self.inbox_a,
            contact=self.contact_a,
            branch=self.branch_a,  # Автоматически из inbox.branch
            status=Conversation.Status.OPEN,
            assignee=self.operator_a,
        )

        self.conversation_b = Conversation.objects.create(
            inbox=self.inbox_b,
            contact=self.contact_b,
            branch=self.branch_b,
            status=Conversation.Status.OPEN,
            assignee=self.operator_b,
        )

        # Включаем messenger для тестов
        self.original_messenger_enabled = getattr(settings, "MESSENGER_ENABLED", False)
        settings.MESSENGER_ENABLED = True

    def tearDown(self):
        """Восстановление настроек после тестов."""
        settings.MESSENGER_ENABLED = self.original_messenger_enabled

    # ========================================================================
    # A) Visibility тесты
    # ========================================================================

    def test_operator_branch_a_cannot_see_branch_b_conversations(self):
        """Оператор филиала A не видит диалоги филиала B."""
        client = APIClient()
        client.force_authenticate(user=self.operator_a)

        response = client.get("/api/messenger/conversations/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        conversation_ids = [conv["id"] for conv in response.data["results"]]
        self.assertIn(self.conversation_a.id, conversation_ids)
        self.assertNotIn(self.conversation_b.id, conversation_ids)

    def test_admin_sees_all_conversations(self):
        """Администратор видит все диалоги."""
        client = APIClient()
        client.force_authenticate(user=self.admin)

        response = client.get("/api/messenger/conversations/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        data = _get_response_data(response)
        conversation_ids = [conv["id"] for conv in data]
        self.assertIn(self.conversation_a.id, conversation_ids)
        self.assertIn(self.conversation_b.id, conversation_ids)

    def test_self_scope_sees_only_assigned_conversations(self):
        """Пользователь с data_scope=SELF видит только назначенные ему диалоги."""
        client = APIClient()
        client.force_authenticate(user=self.operator_self)

        response = client.get("/api/messenger/conversations/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        data = _get_response_data(response)
        conversation_ids = [conv["id"] for conv in data]
        # operator_self не назначен ни на один диалог
        self.assertEqual(len(conversation_ids), 0)

        # Назначаем operator_self на conversation_a
        self.conversation_a.assignee = self.operator_self
        self.conversation_a.save()

        response = client.get("/api/messenger/conversations/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        data = _get_response_data(response)
        conversation_ids = [conv["id"] for conv in data]
        self.assertIn(self.conversation_a.id, conversation_ids)
        self.assertNotIn(self.conversation_b.id, conversation_ids)

    def test_operator_without_branch_sees_no_inboxes(self):
        """Оператор без филиала не видит inbox'ы."""
        operator_no_branch = User.objects.create_user(
            username="operator_no_branch",
            email="operator_no_branch@test.com",
            password="testpass",
            role=User.Role.MANAGER,
            branch=None,
            data_scope=User.DataScope.BRANCH,
        )

        visible = selectors.visible_inboxes_qs(operator_no_branch)
        self.assertEqual(visible.count(), 0)

    # ========================================================================
    # B) Update whitelist тесты
    # ========================================================================

    def test_patch_inbox_forbidden(self):
        """Попытка изменить inbox через API должна вернуть 400."""
        client = APIClient()
        client.force_authenticate(user=self.operator_a)

        response = client.patch(
            f"/api/messenger/conversations/{self.conversation_a.id}/",
            {"inbox": self.inbox_b.id},
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("inbox", response.data)

    def test_patch_branch_forbidden(self):
        """Попытка изменить branch через API должна вернуть 400."""
        client = APIClient()
        client.force_authenticate(user=self.operator_a)

        response = client.patch(
            f"/api/messenger/conversations/{self.conversation_a.id}/",
            {"branch": self.branch_b.id},
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("branch", response.data)

    def test_patch_contact_forbidden(self):
        """Попытка изменить contact через API должна вернуть 400."""
        client = APIClient()
        client.force_authenticate(user=self.operator_a)

        response = client.patch(
            f"/api/messenger/conversations/{self.conversation_a.id}/",
            {"contact": self.contact_b.id},
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("contact", response.data)

    def test_patch_allowed_fields_succeeds(self):
        """Обновление разрешённых полей (status, assignee, priority) должно работать."""
        client = APIClient()
        client.force_authenticate(user=self.operator_a)

        response = client.patch(
            f"/api/messenger/conversations/{self.conversation_a.id}/",
            {
                "status": Conversation.Status.PENDING,
                "priority": Conversation.Priority.HIGH,
            },
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.conversation_a.refresh_from_db()
        self.assertEqual(self.conversation_a.status, Conversation.Status.PENDING)
        self.assertEqual(self.conversation_a.priority, Conversation.Priority.HIGH)

    # ========================================================================
    # C) Message invariants тесты
    # ========================================================================

    def test_post_inbound_message_forbidden(self):
        """Создание входящего сообщения через операторский endpoint запрещено."""
        client = APIClient()
        client.force_authenticate(user=self.operator_a)

        response = client.post(
            f"/api/messenger/conversations/{self.conversation_a.id}/messages/",
            {
                "direction": Message.Direction.IN,
                "body": "Test inbound message",
                "sender_contact": self.contact_a.id,
            },
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("detail", response.data)

    def test_post_outbound_message_auto_sets_sender_user(self):
        """При создании исходящего сообщения sender_user автоматически = request.user."""
        client = APIClient()
        client.force_authenticate(user=self.operator_a)

        response = client.post(
            f"/api/messenger/conversations/{self.conversation_a.id}/messages/",
            {
                "direction": Message.Direction.OUT,
                "body": "Test outbound message",
            },
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        message = Message.objects.get(id=response.data["id"])
        self.assertEqual(message.sender_user, self.operator_a)
        self.assertIsNone(message.sender_contact)

    def test_post_outbound_with_sender_user_ignored(self):
        """Попытка передать sender_user вручную игнорируется (всегда request.user)."""
        client = APIClient()
        client.force_authenticate(user=self.operator_a)

        # Пытаемся передать другого пользователя
        response = client.post(
            f"/api/messenger/conversations/{self.conversation_a.id}/messages/",
            {
                "direction": Message.Direction.OUT,
                "body": "Test message",
                "sender_user": self.operator_b.id,  # Должно быть проигнорировано
            },
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        message = Message.objects.get(id=response.data["id"])
        # sender_user должен быть operator_a (request.user), а не operator_b
        self.assertEqual(message.sender_user, self.operator_a)

    def test_post_internal_message_requires_sender_user(self):
        """Внутреннее сообщение требует sender_user (автоматически проставляется)."""
        client = APIClient()
        client.force_authenticate(user=self.operator_a)

        response = client.post(
            f"/api/messenger/conversations/{self.conversation_a.id}/messages/",
            {
                "direction": Message.Direction.INTERNAL,
                "body": "Internal note",
            },
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        message = Message.objects.get(id=response.data["id"])
        self.assertEqual(message.sender_user, self.operator_a)
        self.assertIsNone(message.sender_contact)

    def test_get_messages_list(self):
        """GET /conversations/{id}/messages/ возвращает список сообщений."""
        # Создаём несколько сообщений
        Message.objects.create(
            conversation=self.conversation_a,
            direction=Message.Direction.OUT,
            body="Message 1",
            sender_user=self.operator_a,
        )
        Message.objects.create(
            conversation=self.conversation_a,
            direction=Message.Direction.OUT,
            body="Message 2",
            sender_user=self.operator_a,
        )

        client = APIClient()
        client.force_authenticate(user=self.operator_a)

        response = client.get(f"/api/messenger/conversations/{self.conversation_a.id}/messages/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 2)

    # ========================================================================
    # D) Feature flag тесты
    # ========================================================================

    def test_messenger_disabled_returns_404(self):
        """При MESSENGER_ENABLED=False все messenger endpoints возвращают 404."""
        settings.MESSENGER_ENABLED = False

        client = APIClient()
        client.force_authenticate(user=self.admin)

        # Проверяем разные endpoints
        endpoints = [
            "/api/messenger/conversations/",
            f"/api/messenger/conversations/{self.conversation_a.id}/",
            "/api/messenger/canned-responses/",
        ]

        for endpoint in endpoints:
            response = client.get(endpoint)
            self.assertEqual(
                response.status_code,
                status.HTTP_404_NOT_FOUND,
                f"Endpoint {endpoint} должен возвращать 404 при отключённом messenger",
            )
