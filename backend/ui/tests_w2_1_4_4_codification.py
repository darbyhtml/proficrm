"""W2.1.4.4 — Codification verification для final 14 settings endpoints.

Integrations (7 admin-only):
- settings_import, settings_import_tasks
- settings_company_columns, settings_security
- settings_mobile_devices, settings_mobile_overview, settings_mobile_device_detail

Mobile apps (3 admin-only):
- settings_mobile_apps, settings_mobile_apps_upload, settings_mobile_apps_toggle

Bootstrap-safety (2):
- settings_access, settings_access_role
  Superuser bypass verified even when DENY rule explicitly set на role=admin.

Role-mixed (2):
- settings_calls_stats, settings_calls_manager_detail
  Allow: manager/sales_head/branch_director/group_manager/admin.
  Deny: tenderist.

🎯 W2.1.4 milestone: 64/64 settings endpoints codified.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings

from accounts.models import Branch
from core.test_utils import make_disposable_user

User = get_user_model()


@override_settings(
    SECURE_SSL_REDIRECT=False,
    SECURE_HSTS_SECONDS=0,
    SESSION_COOKIE_SECURE=False,
)
class W2144CodificationTests(TestCase):
    """14 endpoints verified через 3 patterns: admin-only, bootstrap, role-mixed."""

    @classmethod
    def setUpTestData(cls):
        cls.branch = Branch.objects.create(code="w2144_ekb", name="W2.1.4.4 EKB")

        # Superuser admin (sdm-equivalent)
        cls.superuser = User.objects.create_superuser(
            username="w2144_su", email="su@w2144.ru", password="pw"
        )
        cls.superuser.role = User.Role.ADMIN
        cls.superuser.save(update_fields=["role"])

        # Non-superuser admin (at bootstrap lockout risk)
        cls.admin = User.objects.create_user(
            username="w2144_admin",
            email="a@w2144.ru",
            password="pw",
            role=User.Role.ADMIN,
            is_staff=True,
        )

        cls.manager = User.objects.create_user(
            username="w2144_mgr",
            email="m@w2144.ru",
            password="pw",
            role=User.Role.MANAGER,
            branch=cls.branch,
        )

        cls.tenderist = User.objects.create_user(
            username="w2144_tn",
            email="t@w2144.ru",
            password="pw",
            role=User.Role.TENDERIST,
            branch=cls.branch,
        )

        # Seed PolicyRule для call stats (test DB — migration applied автоматически,
        # но явно подстрахуемся т.к. test setUp может не всегда подхватывать data migrations)
        from policy.models import PolicyConfig, PolicyRule

        call_resources = ["ui:settings:calls:stats", "ui:settings:calls:manager_detail"]
        allowed_roles = [
            User.Role.MANAGER,
            User.Role.SALES_HEAD,
            User.Role.BRANCH_DIRECTOR,
            User.Role.GROUP_MANAGER,
            User.Role.ADMIN,
        ]
        for resource in call_resources:
            for role in allowed_roles:
                PolicyRule.objects.update_or_create(
                    subject_type="role",
                    role=role,
                    resource_type="page",
                    resource=resource,
                    defaults={"effect": "allow", "enabled": True, "priority": 100},
                )

        # Force ENFORCE mode
        cfg = PolicyConfig.load()
        cfg.mode = PolicyConfig.Mode.ENFORCE
        cfg.save(update_fields=["mode"])

    def _assert_admin_ok_manager_tenderist_denied(self, method: str, url: str, **kwargs):
        """Standard admin-only pattern: admin 200/302/400/404/405, non-admin 403."""
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

    # --- Integrations (7 admin-only) ---

    def test_settings_import(self):
        self._assert_admin_ok_manager_tenderist_denied("GET", "/admin/import/")

    def test_settings_import_tasks(self):
        self._assert_admin_ok_manager_tenderist_denied("GET", "/admin/import/tasks/")

    def test_settings_company_columns(self):
        self._assert_admin_ok_manager_tenderist_denied("GET", "/admin/company-columns/")

    def test_settings_security(self):
        self._assert_admin_ok_manager_tenderist_denied("GET", "/admin/security/")

    def test_settings_mobile_devices(self):
        self._assert_admin_ok_manager_tenderist_denied("GET", "/admin/mobile/devices/")

    def test_settings_mobile_overview(self):
        self._assert_admin_ok_manager_tenderist_denied("GET", "/admin/mobile/overview/")

    def test_settings_mobile_device_detail_fake_id(self):
        # Fake UUID → admin hits get_object_or_404 → 404 (decorator passed),
        # non-admin blocked by decorator → 403.
        self._assert_admin_ok_manager_tenderist_denied(
            "GET", "/admin/mobile/devices/550e8400-e29b-41d4-a716-446655440000/"
        )

    # --- Mobile apps (3 admin-only) ---

    def test_settings_mobile_apps(self):
        self._assert_admin_ok_manager_tenderist_denied("GET", "/admin/mobile-apps/")

    def test_settings_mobile_apps_upload(self):
        self._assert_admin_ok_manager_tenderist_denied("POST", "/admin/mobile-apps/upload/", data={})

    def test_settings_mobile_apps_toggle(self):
        self._assert_admin_ok_manager_tenderist_denied(
            "POST", "/admin/mobile-apps/00000000-0000-0000-0000-000000000000/toggle/"
        )

    # --- Bootstrap-safety (2) ---

    def test_settings_access_admin_allowed(self):
        c = Client()
        c.force_login(self.admin)
        r = c.get("/admin/access/")
        self.assertEqual(r.status_code, 200)

    def test_settings_access_manager_denied(self):
        c = Client()
        c.force_login(self.manager)
        r = c.get("/admin/access/")
        self.assertEqual(r.status_code, 403)

    def test_settings_access_superuser_bypass_with_deny_rule(self):
        """CRITICAL bootstrap-safety test: superuser passes даже при DENY rule на role=admin.

        Guarantees: accidental DENY rule на ui:settings:access не lockout'ит
        superuser — они могут зайти и remove bad rule.
        """
        from policy.models import PolicyRule

        PolicyRule.objects.create(
            subject_type="role",
            role=User.Role.ADMIN,
            resource_type="page",
            resource="ui:settings:access",
            effect="deny",
            enabled=True,
            priority=50,
        )
        try:
            # Superuser bypasses engine (decide() line 337)
            c = Client()
            c.force_login(self.superuser)
            r = c.get("/admin/access/")
            self.assertEqual(
                r.status_code,
                200,
                "Superuser должен bypass policy engine даже при DENY rule — это bootstrap safety",
            )
            # Non-superuser admin — locked out (documented risk)
            c = Client()
            c.force_login(self.admin)
            r = c.get("/admin/access/")
            self.assertEqual(
                r.status_code,
                403,
                "Non-superuser admin должен быть locked out (documented risk)",
            )
        finally:
            PolicyRule.objects.filter(
                resource="ui:settings:access", role=User.Role.ADMIN, effect="deny"
            ).delete()

    def test_settings_access_role_admin_allowed(self):
        c = Client()
        c.force_login(self.admin)
        r = c.get("/admin/access/roles/manager/")
        self.assertEqual(r.status_code, 200)

    def test_settings_access_role_manager_denied(self):
        c = Client()
        c.force_login(self.manager)
        r = c.get("/admin/access/roles/manager/")
        self.assertEqual(r.status_code, 403)

    # --- Role-mixed: settings_calls_stats + manager_detail ---

    def test_calls_stats_5_roles_allowed(self):
        """Allow: manager, sales_head, branch_director, group_manager, admin."""
        for role in [
            User.Role.MANAGER,
            User.Role.SALES_HEAD,
            User.Role.BRANCH_DIRECTOR,
            User.Role.GROUP_MANAGER,
            User.Role.ADMIN,
        ]:
            user = make_disposable_user(role=role, branch=self.branch)
            c = Client()
            c.force_login(user)
            r = c.get("/admin/calls/stats/")
            self.assertEqual(r.status_code, 200, f"role={role} must access calls_stats")

    def test_calls_stats_tenderist_denied(self):
        """TENDERIST denied."""
        tn = make_disposable_user(role=User.Role.TENDERIST, branch=self.branch)
        c = Client()
        c.force_login(tn)
        r = c.get("/admin/calls/stats/")
        self.assertEqual(r.status_code, 403)

    def test_calls_manager_detail_5_roles_allowed(self):
        """Allow: manager, sales_head, branch_director, group_manager, admin."""
        # target user для URL — manager
        target = self.manager
        for role in [
            User.Role.MANAGER,
            User.Role.SALES_HEAD,
            User.Role.BRANCH_DIRECTOR,
            User.Role.GROUP_MANAGER,
            User.Role.ADMIN,
        ]:
            user = make_disposable_user(role=role, branch=self.branch)
            c = Client()
            c.force_login(user)
            r = c.get(f"/admin/calls/stats/{target.id}/")
            self.assertIn(
                r.status_code,
                [200, 302, 404],
                f"role={role} должен достичь view (decorator passed)",
            )

    def test_calls_manager_detail_tenderist_denied(self):
        tn = make_disposable_user(role=User.Role.TENDERIST, branch=self.branch)
        c = Client()
        c.force_login(tn)
        r = c.get(f"/admin/calls/stats/{self.manager.id}/")
        self.assertEqual(r.status_code, 403)

    # --- Defense-in-depth ---

    def test_defense_in_depth_all_w2144(self):
        """Inline role check present в каждом W2.1.4.4 endpoint."""
        import importlib
        import inspect

        # Import modules directly (NOT re-exports через ui.views — они shadow функцией)
        settings_integrations = importlib.import_module("ui.views.settings_integrations")
        settings_mobile_apps = importlib.import_module("ui.views.settings_mobile_apps")
        settings_core = importlib.import_module("ui.views.settings_core")

        admin_only = [
            (settings_integrations, "settings_import"),
            (settings_integrations, "settings_import_tasks"),
            (settings_integrations, "settings_company_columns"),
            (settings_integrations, "settings_security"),
            (settings_integrations, "settings_mobile_devices"),
            (settings_integrations, "settings_mobile_overview"),
            (settings_integrations, "settings_mobile_device_detail"),
            (settings_mobile_apps, "settings_mobile_apps"),
            (settings_mobile_apps, "settings_mobile_apps_upload"),
            (settings_mobile_apps, "settings_mobile_apps_toggle"),
        ]
        for module, name in admin_only:
            func = getattr(module, name)
            source = inspect.getsource(func)
            self.assertIn("require_admin", source, f"{name}: require_admin missing")
            self.assertIn("@policy_required", source, f"{name}: @policy_required missing")

        # Bootstrap-safety — inline uses `is_superuser or role==ADMIN` pattern
        for name in ["settings_access", "settings_access_role"]:
            func = getattr(settings_core, name)
            source = inspect.getsource(func)
            self.assertIn("is_superuser", source, f"{name}: inline is_superuser check missing")
            self.assertIn("@policy_required", source, f"{name}: @policy_required missing")

        # Role-mixed — inline uses role allowlist
        for name in ["settings_calls_stats", "settings_calls_manager_detail"]:
            func = getattr(settings_integrations, name)
            source = inspect.getsource(func)
            self.assertIn("GROUP_MANAGER", source, f"{name}: GROUP_MANAGER missing в allowlist")
            self.assertIn("@policy_required", source, f"{name}: @policy_required missing")
