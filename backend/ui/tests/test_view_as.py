"""
Тесты для режима «Просмотр администратора» (view_as).
Проверка: сохранение/сброс сессии, контекст-процессор, доступ только для админа.
"""

from django.test import TestCase, Client, override_settings
from django.contrib.auth import get_user_model
from django.urls import reverse

User = get_user_model()


@override_settings(SECURE_SSL_REDIRECT=False)
class ViewAsUpdateTestCase(TestCase):
    """Тесты для view_as_update: сохранение филиала и пользователя в сессии."""

    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_user(
            username="admin",
            password="admin123",
            role=User.Role.ADMIN,
        )
        self.manager = User.objects.create_user(
            username="manager",
            password="mgr123",
            role=User.Role.MANAGER,
        )
        self.client.force_login(self.admin)

    def test_view_as_update_requires_admin(self):
        """Только администратор может вызывать view_as_update; менеджер получает редирект и сессия не меняется."""
        self.client.force_login(self.manager)
        response = self.client.post(
            reverse("view_as_update"),
            {"view_as_branch_id": "1", "next": "/"},
            follow=False,
        )
        self.assertEqual(response.status_code, 302)  # редирект на dashboard
        # Сессия не должна содержать view_as_branch_id — менеджер не мог изменить режим просмотра
        self.assertIsNone(self.client.session.get("view_as_branch_id"))

    def test_view_as_update_saves_branch_id_in_session(self):
        """POST с view_as_branch_id сохраняет филиал в сессии."""
        from accounts.models import Branch
        branch = Branch.objects.create(code="br1", name="Филиал 1")
        response = self.client.post(
            reverse("view_as_update"),
            {"view_as_branch_id": str(branch.id), "next": "/"},
            follow=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.session.get("view_as_branch_id"), branch.id)

    def test_view_as_update_saves_user_id_in_session(self):
        """POST с view_user_id сохраняет пользователя в сессии."""
        response = self.client.post(
            reverse("view_as_update"),
            {"view_user_id": str(self.manager.id), "next": "/"},
            follow=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.session.get("view_as_user_id"), self.manager.id)

    def test_view_as_update_accepts_view_as_branch_id_param(self):
        """Форма может отправлять view_as_branch_id (имя из base.html)."""
        from accounts.models import Branch
        branch = Branch.objects.create(code="br2", name="Филиал 2")
        response = self.client.post(
            reverse("view_as_update"),
            {"view_as_branch_id": str(branch.id), "next": "/companies/"},
            follow=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.session.get("view_as_branch_id"), branch.id)


@override_settings(SECURE_SSL_REDIRECT=False)
class ViewAsResetTestCase(TestCase):
    """Тесты для view_as_reset: сброс ключей сессии."""

    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_user(
            username="admin",
            password="admin123",
            role=User.Role.ADMIN,
        )
        self.client.force_login(self.admin)

    def test_view_as_reset_clears_session_keys(self):
        """view_as_reset очищает view_as_user_id, view_as_role, view_as_branch_id."""
        session = self.client.session
        session["view_as_user_id"] = 999
        session["view_as_role"] = User.Role.MANAGER
        session["view_as_branch_id"] = 1
        session.save()
        response = self.client.get(reverse("view_as_reset"), follow=False)
        self.assertEqual(response.status_code, 302)
        self.assertIsNone(self.client.session.get("view_as_user_id"))
        self.assertIsNone(self.client.session.get("view_as_role"))
        self.assertIsNone(self.client.session.get("view_as_branch_id"))


@override_settings(SECURE_SSL_REDIRECT=False)
class SettingsUsersToggleViewAsTestCase(TestCase):
    """Тесты для переключения режима просмотра на странице Настройки → Пользователи."""

    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_user(
            username="admin",
            password="admin123",
            role=User.Role.ADMIN,
        )
        self.client.force_login(self.admin)

    def test_toggle_off_clears_view_as_session_keys(self):
        """При выключении режима просмотра очищаются view_as_user_id, view_as_role, view_as_branch_id."""
        session = self.client.session
        session["view_as_enabled"] = True
        session["view_as_user_id"] = 2
        session["view_as_role"] = User.Role.MANAGER
        session["view_as_branch_id"] = 1
        session.save()
        # POST без галочки view_as_enabled (чекбокс не отправляется при снятии)
        response = self.client.post(
            reverse("settings_users"),
            {"toggle_view_as": "1"},
            follow=False,
        )
        self.assertEqual(response.status_code, 302)
        session = self.client.session
        self.assertFalse(session.get("view_as_enabled", True))
        self.assertIsNone(session.get("view_as_user_id"))
        self.assertIsNone(session.get("view_as_role"))
        self.assertIsNone(session.get("view_as_branch_id"))


@override_settings(SECURE_SSL_REDIRECT=False)
class ViewAsContextProcessorTestCase(TestCase):
    """Тесты контекст-процессора: view_as_branch при выбранной роли без филиала."""

    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_user(
            username="admin",
            password="admin123",
            role=User.Role.ADMIN,
        )
        self.client.force_login(self.admin)

    def test_view_as_branch_none_when_only_role_selected(self):
        """При включённом view_as с ролью, но без филиала в сессии — view_as_branch в контексте None."""
        session = self.client.session
        session["view_as_enabled"] = True
        session["view_as_role"] = User.Role.MANAGER
        # view_as_branch_id не задаём
        if "view_as_branch_id" in session:
            del session["view_as_branch_id"]
        if "view_as_user_id" in session:
            del session["view_as_user_id"]
        session.save()
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("view_as_branch", response.context)
        self.assertIsNone(response.context["view_as_branch"])
        self.assertTrue(response.context.get("view_as_enabled"))
        self.assertEqual(response.context.get("view_as_role"), User.Role.MANAGER)
