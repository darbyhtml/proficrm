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
from messenger.models import Inbox, Contact, Conversation, Message, RoutingRule
from messenger.utils import get_widget_session
from messenger import services

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
    
    # ========================================================================
    # Throttling тесты (PRD-1)
    # ========================================================================
    
    def test_bootstrap_throttling(self):
        """Проверка throttling для bootstrap: превышение лимита возвращает 429."""
        from django.core.cache import cache
        from messenger.throttles import WidgetBootstrapThrottle
        
        # Очистить кэш перед тестом
        cache.clear()
        
        client = APIClient()
        
        # Отправить больше лимита запросов с одного IP
        for i in range(WidgetBootstrapThrottle.RATE_PER_IP + 1):
            response = client.post(
                "/api/widget/bootstrap/",
                {
                    "widget_token": "test_widget_token_123",
                    "contact_external_id": f"test-contact-{i}",
                },
            )
            
            if i < WidgetBootstrapThrottle.RATE_PER_IP:
                # Первые запросы должны проходить
                self.assertIn(
                    response.status_code,
                    [status.HTTP_200_OK, status.HTTP_404_NOT_FOUND],  # 404 если inbox не найден, но не 429
                    f"Запрос {i} не должен быть throttled",
                )
            else:
                # Последний запрос должен быть заблокирован
                self.assertEqual(
                    response.status_code,
                    status.HTTP_429_TOO_MANY_REQUESTS,
                    f"Запрос {i} должен быть throttled (429)",
                )
    
    def test_send_throttling(self):
        """Проверка throttling для send: превышение лимита возвращает 429."""
        from django.core.cache import cache
        from messenger.throttles import WidgetSendThrottle
        from messenger.utils import create_widget_session
        
        cache.clear()
        
        # Создать сессию
        session = create_widget_session(
            inbox_id=self.inbox.id,
            conversation_id=self.conversation.id,
            contact_id=str(self.contact.id),
        )
        
        client = APIClient()
        
        # Отправить больше лимита запросов
        for i in range(WidgetSendThrottle.RATE_PER_SESSION + 1):
            response = client.post(
                "/api/widget/send/",
                {
                    "widget_token": "test_widget_token_123",
                    "widget_session_token": session.token,
                    "body": f"Test message {i}",
                },
            )
            
            if i < WidgetSendThrottle.RATE_PER_SESSION:
                # Первые запросы должны проходить
                self.assertIn(
                    response.status_code,
                    [status.HTTP_201_CREATED, status.HTTP_400_BAD_REQUEST],  # 400 если валидация не прошла
                    f"Запрос {i} не должен быть throttled",
                )
            else:
                # Последний запрос должен быть заблокирован
                self.assertEqual(
                    response.status_code,
                    status.HTTP_429_TOO_MANY_REQUESTS,
                    f"Запрос {i} должен быть throttled (429)",
                )
    
    def test_poll_throttling(self):
        """Проверка throttling для poll: минимальный интервал и лимит запросов."""
        from django.core.cache import cache
        from messenger.throttles import WidgetPollThrottle
        from messenger.utils import create_widget_session
        
        cache.clear()
        
        # Создать сессию
        session = create_widget_session(
            inbox_id=self.inbox.id,
            conversation_id=self.conversation.id,
            contact_id=str(self.contact.id),
        )
        
        client = APIClient()
        
        # Первый запрос должен пройти
        response1 = client.get(
            "/api/widget/poll/",
            {
                "widget_token": "test_widget_token_123",
                "widget_session_token": session.token,
            },
        )
        self.assertIn(
            response1.status_code,
            [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST],
            "Первый запрос должен пройти",
        )
        
        # Второй запрос сразу после первого должен быть заблокирован (min interval)
        response2 = client.get(
            "/api/widget/poll/",
            {
                "widget_token": "test_widget_token_123",
                "widget_session_token": session.token,
            },
        )
        self.assertEqual(
                response2.status_code,
                status.HTTP_429_TOO_MANY_REQUESTS,
                "Второй запрос должен быть заблокирован из-за минимального интервала",
            )
    
    # ========================================================================
    # Honeypot и антибот-валидация тесты (PRD-2)
    # ========================================================================
    
    def test_send_honeypot_blocks_bot(self):
        """Проверка honeypot: заполненное поле hp блокирует запрос."""
        from messenger.utils import create_widget_session
        
        session = create_widget_session(
            inbox_id=self.inbox.id,
            conversation_id=self.conversation.id,
            contact_id=str(self.contact.id),
        )
        
        client = APIClient()
        
        # Запрос с заполненным honeypot полем
        response = client.post(
            "/api/widget/send/",
            {
                "widget_token": "test_widget_token_123",
                "widget_session_token": session.token,
                "body": "Test message",
                "hp": "filled",  # Бот заполнил honeypot
            },
        )
        
        self.assertEqual(
            response.status_code,
            status.HTTP_400_BAD_REQUEST,
            "Запрос с заполненным honeypot должен быть заблокирован",
        )
        self.assertIn("hp", response.data or {})
    
    def test_send_too_many_links_blocked(self):
        """Проверка: сообщение с слишком большим количеством ссылок блокируется."""
        from messenger.utils import create_widget_session
        
        session = create_widget_session(
            inbox_id=self.inbox.id,
            conversation_id=self.conversation.id,
            contact_id=str(self.contact.id),
        )
        
        client = APIClient()
        
        # Сообщение с 4+ ссылками
        response = client.post(
            "/api/widget/send/",
            {
                "widget_token": "test_widget_token_123",
                "widget_session_token": session.token,
                "body": "Check http://link1.com and https://link2.com and www.link3.com and http://link4.com",
            },
        )
        
        self.assertEqual(
            response.status_code,
            status.HTTP_400_BAD_REQUEST,
            "Сообщение с слишком большим количеством ссылок должно быть заблокировано",
        )
    
    def test_send_duplicate_messages_blocked(self):
        """Проверка: одинаковые сообщения подряд блокируются."""
        from django.core.cache import cache
        from messenger.utils import create_widget_session
        
        cache.clear()
        
        session = create_widget_session(
            inbox_id=self.inbox.id,
            conversation_id=self.conversation.id,
            contact_id=str(self.contact.id),
        )
        
        client = APIClient()
        
        message_body = "Spam message"
        
        # Отправляем одинаковое сообщение несколько раз
        for i in range(4):
            response = client.post(
                "/api/widget/send/",
                {
                    "widget_token": "test_widget_token_123",
                    "widget_session_token": session.token,
                    "body": message_body,
                },
            )
            
            if i < 3:
                # Первые 3 сообщения должны проходить
                self.assertIn(
                    response.status_code,
                    [status.HTTP_201_CREATED, status.HTTP_400_BAD_REQUEST],
                    f"Сообщение {i} должно проходить",
                )
            else:
                # 4-е сообщение должно быть заблокировано
                self.assertEqual(
                    response.status_code,
                    status.HTTP_400_BAD_REQUEST,
                    "4-е одинаковое сообщение должно быть заблокировано",
                )
                self.assertIn("duplicate", response.data.get("detail", "").lower())
    
    # ========================================================================
    # Routing Engine тесты (PRD-4)
    # ========================================================================
    
    def test_bootstrap_applies_routing_rule_by_region(self):
        """Проверка: при создании нового Conversation применяется RoutingRule по region."""
        from companies.models import Region
        
        # Создаём регион
        region = Region.objects.create(name="Москва", code="MSK")
        
        # Создаём правило маршрутизации
        routing_rule = RoutingRule.objects.create(
            name="Москва → Филиал 1",
            inbox=self.inbox,
            branch=self.branch,
            priority=10,
            is_active=True,
        )
        routing_rule.regions.add(region)
        
        client = APIClient()
        
        # Bootstrap с указанием region_id
        response = client.post(
            "/api/widget/bootstrap/",
            {
                "widget_token": "test_widget_token_123",
                "contact_external_id": "new_visitor_routing",
                "region_id": region.id,
            },
        )
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # Проверяем, что Conversation создан с правильным region
        conversation = Conversation.objects.get(id=response.data["conversation_id"])
        self.assertEqual(conversation.region, region)
    
    def test_bootstrap_fallback_routing_rule(self):
        """Проверка: fallback правило применяется, если нет правила по region."""
        from companies.models import Region
        
        # Создаём регион без правила
        region = Region.objects.create(name="Санкт-Петербург", code="SPB")
        
        # Создаём fallback правило
        fallback_rule = RoutingRule.objects.create(
            name="Fallback",
            inbox=self.inbox,
            branch=self.branch,
            priority=100,
            is_fallback=True,
            is_active=True,
        )
        
        client = APIClient()
        
        # Bootstrap с region, для которого нет правила
        response = client.post(
            "/api/widget/bootstrap/",
            {
                "widget_token": "test_widget_token_123",
                "contact_external_id": "new_visitor_fallback",
                "region_id": region.id,
            },
        )
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # Проверяем, что Conversation создан с region (fallback не меняет region, но правило найдено)
        conversation = Conversation.objects.get(id=response.data["conversation_id"])
        self.assertEqual(conversation.region, region)  # region проставляется из параметра
    
    def test_select_routing_rule_by_region(self):
        """Проверка функции select_routing_rule: выбор правила по region."""
        from companies.models import Region
        
        region1 = Region.objects.create(name="Москва", code="MSK")
        region2 = Region.objects.create(name="СПб", code="SPB")
        
        # Правило для region1
        rule1 = RoutingRule.objects.create(
            name="Москва",
            inbox=self.inbox,
            branch=self.branch,
            priority=10,
            is_active=True,
        )
        rule1.regions.add(region1)
        
        # Fallback правило
        fallback = RoutingRule.objects.create(
            name="Fallback",
            inbox=self.inbox,
            branch=self.branch,
            priority=100,
            is_fallback=True,
            is_active=True,
        )
        
        # Выбор правила для region1
        selected = services.select_routing_rule(self.inbox, region1)
        self.assertIsNotNone(selected)
        self.assertEqual(selected.id, rule1.id)
        
        # Выбор правила для region2 (должен вернуть fallback)
        selected = services.select_routing_rule(self.inbox, region2)
        self.assertIsNotNone(selected)
        self.assertEqual(selected.id, fallback.id)
        
        # Выбор правила без region (должен вернуть fallback)
        selected = services.select_routing_rule(self.inbox, None)
        self.assertIsNotNone(selected)
        self.assertEqual(selected.id, fallback.id)

    # ========================================================================
    # Auto-assign тесты (PRD-5)
    # ========================================================================

    def test_auto_assign_only_within_branch(self):
        """Автоназначение только внутри branch: оператор другого филиала не назначается."""
        from django.core.cache import cache

        cache.clear()

        # Оператор нашего филиала (не ADMIN)
        operator = User.objects.create_user(
            username="operator1",
            email="op1@test.com",
            password="testpass",
            branch=self.branch,
            role=User.Role.MANAGER,
        )

        # Другой филиал и оператор
        other_branch = Branch.objects.create(code="other_br", name="Other Branch")
        other_operator = User.objects.create_user(
            username="operator_other",
            email="op_other@test.com",
            password="testpass",
            branch=other_branch,
            role=User.Role.MANAGER,
        )

        client = APIClient()
        response = client.post(
            "/api/widget/bootstrap/",
            {
                "widget_token": "test_widget_token_123",
                "contact_external_id": "visitor_auto_assign",
            },
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        conversation = Conversation.objects.get(id=response.data["conversation_id"])
        self.assertIsNotNone(conversation.assignee_id)
        self.assertEqual(conversation.assignee_id, operator.id)
        self.assertNotEqual(conversation.assignee_id, other_operator.id)

    def test_auto_assign_round_robin_two_operators(self):
        """Round-robin: при двух операторах новые диалоги назначаются по очереди."""
        from django.core.cache import cache

        cache.clear()

        op1 = User.objects.create_user(
            username="rr_op1",
            email="rr1@test.com",
            password="testpass",
            branch=self.branch,
            role=User.Role.MANAGER,
        )
        op2 = User.objects.create_user(
            username="rr_op2",
            email="rr2@test.com",
            password="testpass",
            branch=self.branch,
            role=User.Role.MANAGER,
        )

        client = APIClient()
        ids = []
        for i in range(2):
            r = client.post(
                "/api/widget/bootstrap/",
                {
                    "widget_token": "test_widget_token_123",
                    "contact_external_id": f"visitor_rr_{i}",
                },
            )
            self.assertEqual(r.status_code, status.HTTP_200_OK)
            conv = Conversation.objects.get(id=r.data["conversation_id"])
            ids.append(conv.assignee_id)

        self.assertIsNotNone(ids[0])
        self.assertIsNotNone(ids[1])
        self.assertNotEqual(ids[0], ids[1])
        self.assertEqual(set(ids), {op1.id, op2.id})
