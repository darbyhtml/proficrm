"""
Тесты view-слоя для notifications/ — HTTP request/response, permissions.

Покрытие:
  1. mark_all_read   — POST, сбрасывает уведомления, инвалидирует кэш
  2. mark_read       — POST, помечает одно уведомление, чужое → 404
  3. poll            — GET, возвращает JSON с bell_count и notif_items
  4. all_notifications — GET, рендерит страницу, показывает уведомления
  5. all_reminders   — GET, рендерит страницу
"""

from __future__ import annotations

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from notifications.models import Notification


def _make_user(username, role=User.Role.MANAGER):
    return User.objects.create_user(username=username, password="pass", role=role)


def _make_notification(user, title="Test", is_read=False):
    return Notification.objects.create(
        user=user,
        title=title,
        body="Body text",
        kind=Notification.Kind.SYSTEM,
        is_read=is_read,
    )


# ---------------------------------------------------------------------------
# 1. mark_all_read
# ---------------------------------------------------------------------------


@override_settings(SECURE_SSL_REDIRECT=False)
class MarkAllReadViewTest(TestCase):
    def setUp(self):
        self.user = _make_user("mgr")

    def test_requires_login(self):
        r = self.client.post(reverse("notifications_mark_all_read"))
        self.assertNotEqual(r.status_code, 200)

    def test_get_redirects(self):
        self.client.force_login(self.user)
        r = self.client.get(reverse("notifications_mark_all_read"))
        self.assertEqual(r.status_code, 302)

    def test_post_marks_all_read(self):
        _make_notification(self.user, "N1")
        _make_notification(self.user, "N2")
        self.client.force_login(self.user)
        self.client.post(reverse("notifications_mark_all_read"))
        unread = Notification.objects.filter(user=self.user, is_read=False).count()
        self.assertEqual(unread, 0)

    def test_post_does_not_affect_other_users(self):
        other = _make_user("other")
        n = _make_notification(other, "Other notification")
        self.client.force_login(self.user)
        self.client.post(reverse("notifications_mark_all_read"))
        n.refresh_from_db()
        self.assertFalse(n.is_read)


# ---------------------------------------------------------------------------
# 2. mark_read
# ---------------------------------------------------------------------------


@override_settings(SECURE_SSL_REDIRECT=False)
class MarkReadViewTest(TestCase):
    def setUp(self):
        self.user = _make_user("mgr")

    def test_requires_login(self):
        n = _make_notification(self.user)
        r = self.client.post(reverse("notifications_mark_read", kwargs={"notification_id": n.id}))
        self.assertNotEqual(r.status_code, 200)

    def test_post_marks_single_notification_read(self):
        n = _make_notification(self.user)
        self.assertFalse(n.is_read)
        self.client.force_login(self.user)
        self.client.post(reverse("notifications_mark_read", kwargs={"notification_id": n.id}))
        n.refresh_from_db()
        self.assertTrue(n.is_read)

    def test_get_redirects(self):
        n = _make_notification(self.user)
        self.client.force_login(self.user)
        r = self.client.get(reverse("notifications_mark_read", kwargs={"notification_id": n.id}))
        self.assertEqual(r.status_code, 302)

    def test_cannot_mark_other_users_notification(self):
        other = _make_user("other")
        n = _make_notification(other)
        self.client.force_login(self.user)
        r = self.client.post(reverse("notifications_mark_read", kwargs={"notification_id": n.id}))
        self.assertEqual(r.status_code, 404)
        n.refresh_from_db()
        self.assertFalse(n.is_read)


# ---------------------------------------------------------------------------
# 3. poll (AJAX JSON)
# ---------------------------------------------------------------------------


@override_settings(SECURE_SSL_REDIRECT=False)
class PollViewTest(TestCase):
    def setUp(self):
        self.user = _make_user("mgr")

    def test_requires_login(self):
        r = self.client.get(reverse("notifications_poll"))
        self.assertNotEqual(r.status_code, 200)

    def test_returns_json(self):
        self.client.force_login(self.user)
        r = self.client.get(reverse("notifications_poll"))
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue(data.get("ok"))
        self.assertIn("bell_count", data)
        self.assertIn("notif_items", data)
        self.assertIn("reminder_count", data)

    def test_unread_count_matches(self):
        _make_notification(self.user, "Unread 1")
        _make_notification(self.user, "Unread 2")
        _make_notification(self.user, "Read", is_read=True)
        self.client.force_login(self.user)
        r = self.client.get(reverse("notifications_poll"))
        data = r.json()
        self.assertEqual(data["notif_unread_count"], 2)

    def test_only_own_notifications_in_response(self):
        other = _make_user("other")
        _make_notification(self.user, "Mine")
        _make_notification(other, "Theirs")
        self.client.force_login(self.user)
        r = self.client.get(reverse("notifications_poll"))
        data = r.json()
        titles = [item["title"] for item in data["notif_items"]]
        self.assertIn("Mine", titles)
        self.assertNotIn("Theirs", titles)


# ---------------------------------------------------------------------------
# 4. all_notifications
# ---------------------------------------------------------------------------


@override_settings(SECURE_SSL_REDIRECT=False)
class AllNotificationsViewTest(TestCase):
    def setUp(self):
        self.user = _make_user("mgr")

    def test_requires_login(self):
        r = self.client.get(reverse("notifications_all"))
        self.assertNotEqual(r.status_code, 200)

    def test_renders_200(self):
        self.client.force_login(self.user)
        r = self.client.get(reverse("notifications_all"))
        self.assertEqual(r.status_code, 200)

    def test_context_has_counts(self):
        _make_notification(self.user, "Unread")
        _make_notification(self.user, "Read", is_read=True)
        self.client.force_login(self.user)
        r = self.client.get(reverse("notifications_all"))
        self.assertEqual(r.context["unread_count"], 1)
        self.assertEqual(r.context["read_count"], 1)
        self.assertEqual(r.context["total_count"], 2)

    def test_empty_state_renders(self):
        self.client.force_login(self.user)
        r = self.client.get(reverse("notifications_all"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["total_count"], 0)


# ---------------------------------------------------------------------------
# 5. all_reminders
# ---------------------------------------------------------------------------


@override_settings(SECURE_SSL_REDIRECT=False)
class AllRemindersViewTest(TestCase):
    def setUp(self):
        self.user = _make_user("mgr")

    def test_requires_login(self):
        r = self.client.get(reverse("notifications_reminders_all"))
        self.assertNotEqual(r.status_code, 200)

    def test_renders_200(self):
        self.client.force_login(self.user)
        r = self.client.get(reverse("notifications_reminders_all"))
        self.assertEqual(r.status_code, 200)
