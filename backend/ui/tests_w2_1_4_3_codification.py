"""W2.1.4.3 — Codification verification для 19 settings endpoints.

Messenger (14):
- overview (page)
- inbox: source_choose / ready / edit (actions + create/edit)
- health (page)
- analytics (page)
- routing: list (page) + edit + delete (actions)
- canned: list (page) + edit + delete (actions)
- campaigns (page)
- automation (page)

Mail (5):
- setup (page)
- save_password / test_send / save_config / toggle_enabled (actions)

Behavior: @policy_required blocks non-admin 403 + inline require_admin()
defense-in-depth preserved.
"""

from __future__ import annotations

import time

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings

from accounts.models import Branch
from messenger.models import CannedResponse, Inbox, RoutingRule

User = get_user_model()


@override_settings(
    SECURE_SSL_REDIRECT=False,
    SECURE_HSTS_SECONDS=0,
    SESSION_COOKIE_SECURE=False,
    MESSENGER_ENABLED=True,
)
class W2143CodificationTests(TestCase):
    """19 endpoints protected admin-only через @policy_required + inline fallback."""

    @classmethod
    def setUpTestData(cls):
        cls.branch = Branch.objects.create(code="w2143_ekb", name="W2.1.4.3 EKB")

        cls.admin = User.objects.create_superuser(
            username="w2143_admin",
            email="a@w2143.ru",
            password="pw",
        )
        cls.admin.role = User.Role.ADMIN
        cls.admin.save(update_fields=["role"])

        cls.manager = User.objects.create_user(
            username="w2143_mgr",
            email="m@w2143.ru",
            password="pw",
            role=User.Role.MANAGER,
            branch=cls.branch,
        )

        cls.tenderist = User.objects.create_user(
            username="w2143_tn",
            email="t@w2143.ru",
            password="pw",
            role=User.Role.TENDERIST,
            branch=cls.branch,
        )

        # Force PolicyConfig в ENFORCE mode
        from policy.models import PolicyConfig

        cfg = PolicyConfig.load()
        cfg.mode = PolicyConfig.Mode.ENFORCE
        cfg.save(update_fields=["mode"])

    def _assert_admin_ok_others_denied(self, method: str, url: str, **kwargs):
        """Admin 200/302/400/405, non-admin 403."""
        c_admin = Client()
        c_admin.force_login(self.admin)
        r = getattr(c_admin, method.lower())(url, **kwargs)
        self.assertIn(
            r.status_code,
            [200, 302, 400, 404, 405],
            f"admin {method} {url} → unexpected {r.status_code}",
        )
        for user, label in [(self.manager, "manager"), (self.tenderist, "tenderist")]:
            c = Client()
            c.force_login(user)
            r = getattr(c, method.lower())(url, **kwargs)
            self.assertEqual(
                r.status_code,
                403,
                f"{label} {method} {url} expected 403, got {r.status_code}",
            )

    # --- Messenger navigation + pages ---

    def test_messenger_overview(self):
        self._assert_admin_ok_others_denied("GET", "/admin/messenger/")

    def test_messenger_source_choose(self):
        self._assert_admin_ok_others_denied("GET", "/admin/messenger/sources/choose/")

    def test_messenger_inbox_ready(self):
        # Fake inbox_id → admin hits get_object_or_404, but decorator passes.
        # Manager blocked by decorator.
        self._assert_admin_ok_others_denied("GET", "/admin/messenger/inboxes/999999/ready/")

    def test_messenger_health(self):
        self._assert_admin_ok_others_denied("GET", "/admin/messenger/health/")

    def test_messenger_analytics(self):
        self._assert_admin_ok_others_denied("GET", "/admin/messenger/analytics/")

    # --- Messenger inbox edit ---

    def test_messenger_inbox_edit_create(self):
        self._assert_admin_ok_others_denied("GET", "/admin/messenger/inboxes/new/")

    # --- Messenger routing CRUD ---

    def test_messenger_routing_list(self):
        self._assert_admin_ok_others_denied("GET", "/admin/messenger/routing/")

    def test_messenger_routing_edit_create(self):
        self._assert_admin_ok_others_denied("GET", "/admin/messenger/routing/new/")

    def test_messenger_routing_delete(self):
        """Disposable RoutingRule: admin deletes, non-admin 403."""
        inbox = Inbox.objects.create(name=f"disp_{time.time_ns()}", branch=self.branch)
        rule = RoutingRule.objects.create(
            name=f"disp_{time.time_ns()}",
            inbox=inbox,
            branch=self.branch,
            priority=999,
            is_active=True,
        )
        for user in [self.manager, self.tenderist]:
            c = Client()
            c.force_login(user)
            r = c.post(f"/admin/messenger/routing/{rule.id}/delete/")
            self.assertEqual(r.status_code, 403)
        c = Client()
        c.force_login(self.admin)
        r = c.post(f"/admin/messenger/routing/{rule.id}/delete/")
        self.assertIn(r.status_code, [200, 302])
        self.assertFalse(RoutingRule.objects.filter(id=rule.id).exists())

    # --- Messenger canned CRUD ---

    def test_messenger_canned_list(self):
        self._assert_admin_ok_others_denied("GET", "/admin/messenger/canned-responses/")

    def test_messenger_canned_edit_create(self):
        self._assert_admin_ok_others_denied("GET", "/admin/messenger/canned-responses/new/")

    def test_messenger_canned_delete(self):
        canned = CannedResponse.objects.create(
            title=f"disp_{time.time_ns()}",
            body="test",
            created_by=self.admin,
        )
        for user in [self.manager, self.tenderist]:
            c = Client()
            c.force_login(user)
            r = c.post(f"/admin/messenger/canned-responses/{canned.id}/delete/")
            self.assertEqual(r.status_code, 403)
        c = Client()
        c.force_login(self.admin)
        r = c.post(f"/admin/messenger/canned-responses/{canned.id}/delete/")
        self.assertIn(r.status_code, [200, 302])
        self.assertFalse(CannedResponse.objects.filter(id=canned.id).exists())

    # --- Messenger campaigns + automation pages ---

    def test_messenger_campaigns(self):
        self._assert_admin_ok_others_denied("GET", "/admin/messenger/campaigns/")

    def test_messenger_automation(self):
        self._assert_admin_ok_others_denied("GET", "/admin/messenger/automation/")

    # --- Mail (SMTP) admin ---

    def test_mail_setup(self):
        self._assert_admin_ok_others_denied("GET", "/admin/mail/setup/")

    def test_mail_save_password(self):
        """POST empty password — admin gets 302 (validation redirect), manager 403."""
        self._assert_admin_ok_others_denied(
            "POST", "/admin/mail/setup/save-password/", data={"new_password": ""}
        )

    def test_mail_save_config(self):
        """POST empty form — admin 302 (saves trivial), manager 403."""
        self._assert_admin_ok_others_denied("POST", "/admin/mail/setup/save-config/", data={})

    def test_mail_toggle_enabled(self):
        """POST toggle — admin 302, manager 403. Test не включает (false→true would trigger Fernet check)."""
        self._assert_admin_ok_others_denied("POST", "/admin/mail/setup/toggle-enabled/")

    def test_mail_test_send(self):
        """POST test_send — admin would attempt SMTP, but no email → redirect 302. Manager 403."""
        # Admin: self.admin.email is set (a@w2143.ru) так что SMTP будет attempted.
        # Real SMTP не доступен в test env → send fails → messages.error + redirect 302.
        self._assert_admin_ok_others_denied("POST", "/admin/mail/setup/test-send/")

    # --- Defense-in-depth source verification ---

    def test_defense_in_depth_messenger(self):
        """Inline require_admin() + @policy_required present в каждом messenger endpoint."""
        import inspect

        from ui.views import settings_messenger

        endpoints = [
            "settings_messenger_overview",
            "settings_messenger_source_choose",
            "settings_messenger_inbox_ready",
            "settings_messenger_health",
            "settings_messenger_analytics",
            "settings_messenger_inbox_edit",
            "settings_messenger_routing_list",
            "settings_messenger_routing_edit",
            "settings_messenger_routing_delete",
            "settings_messenger_canned_list",
            "settings_messenger_canned_edit",
            "settings_messenger_canned_delete",
            "settings_messenger_campaigns",
            "settings_messenger_automation",
        ]
        for name in endpoints:
            func = getattr(settings_messenger, name)
            source = inspect.getsource(func)
            self.assertIn("require_admin", source, f"{name}: inline require_admin missing")
            self.assertIn("@policy_required", source, f"{name}: @policy_required missing")

    def test_defense_in_depth_mail(self):
        """Inline require_admin() + @policy_required present в каждом mail endpoint."""
        import inspect

        from ui.views import settings_mail

        endpoints = [
            "settings_mail_setup",
            "settings_mail_save_password",
            "settings_mail_test_send",
            "settings_mail_save_config",
            "settings_mail_toggle_enabled",
        ]
        for name in endpoints:
            func = getattr(settings_mail, name)
            source = inspect.getsource(func)
            self.assertIn("require_admin", source, f"{name}: inline require_admin missing")
            self.assertIn("@policy_required", source, f"{name}: @policy_required missing")
