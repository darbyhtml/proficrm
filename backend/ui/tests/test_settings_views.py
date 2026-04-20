"""
Тесты view-слоя для settings_core.py — admin-only views.

Покрытие:
  1. settings_dashboard    — GET, только admin
  2. settings_users        — GET, список пользователей
  3. settings_user_create  — GET/POST, создание пользователя
  4. settings_user_edit    — GET/POST, редактирование
  5. settings_user_delete  — POST, удаление (JSON response)
  6. settings_branches     — GET, список филиалов
  7. settings_dicts        — GET, словари
  8. settings_announcements— GET/POST, объявления
"""

from __future__ import annotations

from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import User, Branch


def _make_admin(username="admin"):
    return User.objects.create_user(username=username, password="pass", role=User.Role.ADMIN)


def _make_manager(username="mgr"):
    return User.objects.create_user(username=username, password="pass", role=User.Role.MANAGER)


# ---------------------------------------------------------------------------
# 1. settings_dashboard — требует admin
# ---------------------------------------------------------------------------


@override_settings(SECURE_SSL_REDIRECT=False)
class SettingsDashboardViewTest(TestCase):
    def test_requires_login(self):
        r = self.client.get(reverse("settings_dashboard"))
        self.assertNotEqual(r.status_code, 200)

    def test_admin_can_access(self):
        admin = _make_admin()
        self.client.force_login(admin)
        r = self.client.get(reverse("settings_dashboard"))
        self.assertEqual(r.status_code, 200)

    def test_manager_redirected(self):
        mgr = _make_manager()
        self.client.force_login(mgr)
        r = self.client.get(reverse("settings_dashboard"))
        self.assertRedirects(r, reverse("dashboard"), fetch_redirect_response=False)


# ---------------------------------------------------------------------------
# 2. settings_users
# ---------------------------------------------------------------------------


@override_settings(SECURE_SSL_REDIRECT=False)
class SettingsUsersViewTest(TestCase):
    def test_admin_sees_users_list(self):
        admin = _make_admin()
        _make_manager("m1")
        _make_manager("m2")
        self.client.force_login(admin)
        r = self.client.get(reverse("settings_users"))
        self.assertEqual(r.status_code, 200)

    def test_manager_redirected(self):
        mgr = _make_manager()
        self.client.force_login(mgr)
        r = self.client.get(reverse("settings_users"))
        self.assertRedirects(r, reverse("dashboard"), fetch_redirect_response=False)

    def test_requires_login(self):
        r = self.client.get(reverse("settings_users"))
        self.assertNotEqual(r.status_code, 200)


# ---------------------------------------------------------------------------
# 3. settings_user_create
# ---------------------------------------------------------------------------


@override_settings(SECURE_SSL_REDIRECT=False)
class SettingsUserCreateViewTest(TestCase):
    def setUp(self):
        self.admin = _make_admin()

    def test_get_renders_form(self):
        self.client.force_login(self.admin)
        r = self.client.get(reverse("settings_user_create"))
        self.assertEqual(r.status_code, 200)

    def test_post_creates_user(self):
        self.client.force_login(self.admin)
        self.client.post(
            reverse("settings_user_create"),
            {
                "username": "newuser",
                "first_name": "New",
                "last_name": "User",
                "role": User.Role.MANAGER,
                "email": "new@example.com",
            },
        )
        self.assertTrue(User.objects.filter(username="newuser").exists())

    def test_manager_cannot_create_users(self):
        mgr = _make_manager()
        self.client.force_login(mgr)
        r = self.client.get(reverse("settings_user_create"))
        self.assertRedirects(r, reverse("dashboard"), fetch_redirect_response=False)


# ---------------------------------------------------------------------------
# 4. settings_user_edit
# ---------------------------------------------------------------------------


@override_settings(SECURE_SSL_REDIRECT=False)
class SettingsUserEditViewTest(TestCase):
    def setUp(self):
        self.admin = _make_admin()
        self.target = _make_manager("target")

    def test_admin_can_edit(self):
        self.client.force_login(self.admin)
        r = self.client.get(reverse("settings_user_edit", kwargs={"user_id": self.target.pk}))
        self.assertEqual(r.status_code, 200)

    def test_post_updates_name(self):
        self.client.force_login(self.admin)
        self.client.post(
            reverse("settings_user_edit", kwargs={"user_id": self.target.pk}),
            {
                "username": self.target.username,
                "first_name": "Updated",
                "last_name": "Name",
                "role": User.Role.MANAGER,
                "email": "target@example.com",
            },
        )
        self.target.refresh_from_db()
        self.assertEqual(self.target.first_name, "Updated")

    def test_404_for_nonexistent_user(self):
        self.client.force_login(self.admin)
        r = self.client.get(reverse("settings_user_edit", kwargs={"user_id": 99999}))
        self.assertEqual(r.status_code, 404)


# ---------------------------------------------------------------------------
# 5. settings_user_delete
# ---------------------------------------------------------------------------


@override_settings(SECURE_SSL_REDIRECT=False)
class SettingsUserDeleteViewTest(TestCase):
    def setUp(self):
        self.admin = _make_admin()

    def test_admin_can_delete_manager(self):
        target = _make_manager("to_delete")
        target_id = target.pk
        self.client.force_login(self.admin)
        r = self.client.post(reverse("settings_user_delete", kwargs={"user_id": target_id}))
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue(data.get("ok"))
        self.assertFalse(User.objects.filter(pk=target_id).exists())  # hard delete

    def test_admin_cannot_delete_self(self):
        self.client.force_login(self.admin)
        r = self.client.post(reverse("settings_user_delete", kwargs={"user_id": self.admin.pk}))
        data = r.json()
        self.assertFalse(data.get("ok"))


# ---------------------------------------------------------------------------
# 6. settings_branches
# ---------------------------------------------------------------------------


@override_settings(SECURE_SSL_REDIRECT=False)
class SettingsBranchesViewTest(TestCase):
    def test_admin_sees_branches(self):
        admin = _make_admin()
        self.client.force_login(admin)
        r = self.client.get(reverse("settings_branches"))
        self.assertEqual(r.status_code, 200)

    def test_manager_redirected(self):
        mgr = _make_manager()
        self.client.force_login(mgr)
        r = self.client.get(reverse("settings_branches"))
        self.assertRedirects(r, reverse("dashboard"), fetch_redirect_response=False)


# ---------------------------------------------------------------------------
# 7. settings_dicts
# ---------------------------------------------------------------------------


@override_settings(SECURE_SSL_REDIRECT=False)
class SettingsDictsViewTest(TestCase):
    def test_admin_can_access(self):
        admin = _make_admin()
        self.client.force_login(admin)
        r = self.client.get(reverse("settings_dicts"))
        self.assertEqual(r.status_code, 200)

    def test_manager_redirected(self):
        mgr = _make_manager()
        self.client.force_login(mgr)
        r = self.client.get(reverse("settings_dicts"))
        self.assertRedirects(r, reverse("dashboard"), fetch_redirect_response=False)
