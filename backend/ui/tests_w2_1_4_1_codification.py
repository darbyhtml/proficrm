"""W2.1.4.1 — Codification verification для 13 settings endpoints.

Все endpoints: @policy_required(admin-only) + inline require_admin() preserved.
Behaviour: admin 200/302, non-admin 403 (via enforce PermissionDenied).
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings

from accounts.models import Branch

User = get_user_model()


@override_settings(
    SECURE_SSL_REDIRECT=False,
    SECURE_HSTS_SECONDS=0,
    SESSION_COOKIE_SECURE=False,
)
class W2141CodificationTests(TestCase):
    """13 endpoints protected admin-only через @policy_required + inline fallback."""

    @classmethod
    def setUpTestData(cls):
        cls.branch = Branch.objects.create(code="w2141_ekb", name="W2.1.4.1 EKB")

        cls.admin = User.objects.create_superuser(
            username="w2141_admin",
            email="a@w2141.ru",
            password="pw",
        )
        cls.admin.role = User.Role.ADMIN
        cls.admin.save(update_fields=["role"])

        cls.manager = User.objects.create_user(
            username="w2141_mgr",
            email="m@w2141.ru",
            password="pw",
            role=User.Role.MANAGER,
            branch=cls.branch,
        )

        cls.tenderist = User.objects.create_user(
            username="w2141_tn",
            email="t@w2141.ru",
            password="pw",
            role=User.Role.TENDERIST,
            branch=cls.branch,
        )

        # Target user для user-specific endpoints (edit, form, update, logout, magic_link_generate)
        # delete test использует disposable user (создаётся per-test, не class-level)
        cls.target = User.objects.create_user(
            username="w2141_target",
            email="t2@w2141.ru",
            password="pw",
            role=User.Role.MANAGER,
            branch=cls.branch,
        )

        # Force PolicyConfig в ENFORCE mode чтобы enforce() raise'ил
        # PermissionDenied → 403 (match staging behavior). Test DB default =
        # OBSERVE_ONLY, в котором @policy_required логирует но не блокирует,
        # и тогда inline require_admin() fallback → 302 redirect.
        from policy.models import PolicyConfig

        cfg = PolicyConfig.load()
        cfg.mode = PolicyConfig.Mode.ENFORCE
        cfg.save(update_fields=["mode"])

    def _assert_admin_ok_others_denied(self, method: str, url: str, **kwargs):
        """Admin допускается (200/302), non-admin (manager, tenderist) → 403."""
        c_admin = Client()
        c_admin.force_login(self.admin)
        r = getattr(c_admin, method.lower())(url, **kwargs)
        self.assertIn(
            r.status_code,
            [200, 302, 400, 405],
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

    # --- Endpoint 1: settings_dashboard ---
    def test_settings_dashboard(self):
        self._assert_admin_ok_others_denied("GET", "/admin/")

    # --- Endpoint 2: settings_announcements ---
    def test_settings_announcements(self):
        self._assert_admin_ok_others_denied("GET", "/admin/announcements/")

    # --- Endpoint 3: settings_branches ---
    def test_settings_branches(self):
        self._assert_admin_ok_others_denied("GET", "/admin/branches/")

    # --- Endpoint 4: settings_branch_create ---
    def test_settings_branch_create(self):
        self._assert_admin_ok_others_denied("GET", "/admin/branches/new/")

    # --- Endpoint 5: settings_branch_edit ---
    def test_settings_branch_edit(self):
        self._assert_admin_ok_others_denied("GET", f"/admin/branches/{self.branch.id}/edit/")

    # --- Endpoint 6: settings_users (nuanced — view_as toggle внутри) ---
    def test_settings_users_get(self):
        self._assert_admin_ok_others_denied("GET", "/admin/users/")

    def test_settings_users_view_as_toggle_preserved(self):
        """Admin POST toggle_view_as — flow preserved."""
        c = Client()
        c.force_login(self.admin)
        r = c.post("/admin/users/", {"toggle_view_as": "1", "view_as_enabled": "on"})
        self.assertEqual(r.status_code, 302)

    # --- Endpoint 7: settings_user_create ---
    def test_settings_user_create(self):
        self._assert_admin_ok_others_denied("GET", "/admin/users/new/")

    # --- Endpoint 8: settings_user_edit ---
    def test_settings_user_edit(self):
        self._assert_admin_ok_others_denied("GET", f"/admin/users/{self.target.id}/edit/")

    # --- Endpoint 9: settings_user_magic_link_generate (sensitive) ---
    def test_settings_user_magic_link_generate_admin(self):
        """Admin generates magic link (rate limit + audit preserved)."""
        from django.core.cache import cache

        from accounts.models import MagicLinkToken

        cache.clear()
        before = MagicLinkToken.objects.filter(user=self.target).count()
        c = Client()
        c.force_login(self.admin)
        r = c.post(
            f"/admin/users/{self.target.id}/magic-link/generate/",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(r.status_code, 200)
        after = MagicLinkToken.objects.filter(user=self.target).count()
        self.assertEqual(after, before + 1, "New MagicLinkToken должен быть создан")

    def test_settings_user_magic_link_generate_non_admin_403(self):
        for user, label in [(self.manager, "manager"), (self.tenderist, "tenderist")]:
            c = Client()
            c.force_login(user)
            r = c.post(f"/admin/users/{self.target.id}/magic-link/generate/")
            self.assertEqual(r.status_code, 403, f"{label} should be denied")

    # --- Endpoint 10: settings_user_logout ---
    def test_settings_user_logout(self):
        self._assert_admin_ok_others_denied("POST", f"/admin/users/{self.target.id}/logout/")

    # --- Endpoint 11: settings_user_form_ajax ---
    def test_settings_user_form_ajax(self):
        self._assert_admin_ok_others_denied("GET", f"/admin/users/{self.target.id}/form/")

    # --- Endpoint 12: settings_user_update_ajax ---
    def test_settings_user_update_ajax(self):
        # Admin POST с пустой формой → 400 (form invalid), non-admin → 403
        self._assert_admin_ok_others_denied(
            "POST", f"/admin/users/{self.target.id}/update/", data={}
        )

    # --- Endpoint 13: settings_user_delete ---
    def test_settings_user_delete(self):
        """Admin deletes disposable user (success), non-admin → 403."""
        disposable = User.objects.create_user(
            username="w2141_disposable",
            email="disp@w2141.ru",
            password="pw",
            role=User.Role.MANAGER,
            branch=self.branch,
        )
        # Non-admin denied
        for user, label in [(self.manager, "manager"), (self.tenderist, "tenderist")]:
            c = Client()
            c.force_login(user)
            r = c.post(f"/admin/users/{disposable.id}/delete/")
            self.assertEqual(r.status_code, 403, f"{label} should be denied")
        # Admin succeeds
        c = Client()
        c.force_login(self.admin)
        r = c.post(f"/admin/users/{disposable.id}/delete/")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(
            User.objects.filter(username="w2141_disposable").exists(),
            "Disposable user должен быть удалён",
        )

    # --- Defense-in-depth preservation ---

    def test_defense_in_depth_inline_check_preserved(self):
        """Inline require_admin() preserved в каждом codified view (source-level check)."""
        import inspect

        from ui.views import settings_core

        endpoints = [
            "settings_dashboard",
            "settings_announcements",
            "settings_branches",
            "settings_branch_create",
            "settings_branch_edit",
            "settings_users",
            "settings_user_create",
            "settings_user_edit",
            "settings_user_magic_link_generate",
            "settings_user_logout",
            "settings_user_form_ajax",
            "settings_user_update_ajax",
            "settings_user_delete",
        ]
        for name in endpoints:
            func = getattr(settings_core, name)
            source = inspect.getsource(func)
            self.assertIn(
                "require_admin",
                source,
                f"{name}: inline require_admin() должен быть preserved",
            )
            self.assertIn(
                "@policy_required",
                source,
                f"{name}: @policy_required decorator должен быть present",
            )
