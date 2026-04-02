"""
Тесты безопасности UI настроек Messenger.

Проверяем:
- Не-админ получает 403/redirect на /settings/messenger/*
- При MESSENGER_ENABLED=False все страницы дают 404
- Админ видит список Inbox'ов
"""

from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from django.urls import reverse

from accounts.models import Branch
from messenger.models import Inbox

User = get_user_model()


class MessengerSettingsUISecurityTests(TestCase):
    """Тесты безопасности UI настроек Messenger."""

    def setUp(self):
        """Подготовка тестовых данных."""
        self.branch = Branch.objects.create(code="test", name="Тестовый филиал")
        
        # Админ
        self.admin = User.objects.create_user(
            username="admin",
            email="admin@test.com",
            password="testpass",
            role=User.Role.ADMIN,
        )
        
        # Обычный пользователь (не админ)
        self.user = User.objects.create_user(
            username="user",
            email="user@test.com",
            password="testpass",
            role=User.Role.MANAGER,
            branch=self.branch,
        )
        
        # Inbox для тестов
        self.inbox = Inbox.objects.create(
            name="Test Inbox",
            branch=self.branch,
            is_active=True,
        )

    @override_settings(MESSENGER_ENABLED=True)
    def test_non_admin_redirected_from_overview(self):
        """Не-админ перенаправляется с /settings/messenger/."""
        self.client.login(username="user", password="testpass")
        response = self.client.get(reverse("settings_messenger_overview"))
        self.assertEqual(response.status_code, 302)  # Redirect

    @override_settings(MESSENGER_ENABLED=True)
    def test_non_admin_redirected_from_inbox_edit(self):
        """Не-админ перенаправляется с /settings/messenger/inboxes/<id>/."""
        self.client.login(username="user", password="testpass")
        response = self.client.get(reverse("settings_messenger_inbox_edit", args=[self.inbox.id]))
        self.assertEqual(response.status_code, 302)  # Redirect

    @override_settings(MESSENGER_ENABLED=True)
    def test_non_admin_redirected_from_routing_list(self):
        """Не-админ перенаправляется с /settings/messenger/routing/."""
        self.client.login(username="user", password="testpass")
        response = self.client.get(reverse("settings_messenger_routing_list"))
        self.assertEqual(response.status_code, 302)  # Redirect

    @override_settings(MESSENGER_ENABLED=False)
    def test_messenger_disabled_overview_404(self):
        """При MESSENGER_ENABLED=False страница overview возвращает 404."""
        self.client.login(username="admin", password="testpass")
        response = self.client.get(reverse("settings_messenger_overview"))
        self.assertEqual(response.status_code, 404)

    @override_settings(MESSENGER_ENABLED=False)
    def test_messenger_disabled_inbox_edit_404(self):
        """При MESSENGER_ENABLED=False страница inbox_edit возвращает 404."""
        self.client.login(username="admin", password="testpass")
        response = self.client.get(reverse("settings_messenger_inbox_edit", args=[self.inbox.id]))
        self.assertEqual(response.status_code, 404)

    @override_settings(MESSENGER_ENABLED=False)
    def test_messenger_disabled_routing_list_404(self):
        """При MESSENGER_ENABLED=False страница routing_list возвращает 404."""
        self.client.login(username="admin", password="testpass")
        response = self.client.get(reverse("settings_messenger_routing_list"))
        self.assertEqual(response.status_code, 404)

    @override_settings(MESSENGER_ENABLED=True)
    def test_admin_sees_inbox_list(self):
        """Админ видит список Inbox'ов."""
        self.client.login(username="admin", password="testpass")
        response = self.client.get(reverse("settings_messenger_overview"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.inbox.name)
        self.assertContains(response, self.branch.name)

    @override_settings(MESSENGER_ENABLED=True)
    def test_admin_can_create_inbox(self):
        """Админ может создать Inbox."""
        self.client.login(username="admin", password="testpass")
        response = self.client.get(reverse("settings_messenger_inbox_create"))
        self.assertEqual(response.status_code, 200)

    @override_settings(MESSENGER_ENABLED=True)
    def test_admin_can_edit_inbox(self):
        """Админ может редактировать Inbox."""
        self.client.login(username="admin", password="testpass")
        response = self.client.get(reverse("settings_messenger_inbox_edit", args=[self.inbox.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.inbox.name)
