"""W2.1.4.2 — Codification verification для 18 settings endpoints.

Dictionaries (13):
- settings_dicts (page)
- company_status/sphere, contract_type, task_type × {create, edit, delete}

Audit logs (5):
- settings_activity (page, sensitive)
- settings_error_log (page, sensitive)
- settings_error_log × {resolve, unresolve, details} (actions)

Behavior: @policy_required blocks non-admin (403) + inline require_admin()
defense-in-depth preserved. Disposable fixtures для destructive endpoints.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings

from accounts.models import Branch
from audit.models import ErrorLog
from companies.models import CompanySphere, CompanyStatus, ContractType
from core.test_utils import make_disposable_dict_entry, make_disposable_user
from tasksapp.models import TaskType

User = get_user_model()


@override_settings(
    SECURE_SSL_REDIRECT=False,
    SECURE_HSTS_SECONDS=0,
    SESSION_COOKIE_SECURE=False,
)
class W2142CodificationTests(TestCase):
    """18 endpoints protected admin-only через @policy_required + inline fallback."""

    @classmethod
    def setUpTestData(cls):
        cls.branch = Branch.objects.create(code="w2142_ekb", name="W2.1.4.2 EKB")

        cls.admin = User.objects.create_superuser(
            username="w2142_admin",
            email="a@w2142.ru",
            password="pw",
        )
        cls.admin.role = User.Role.ADMIN
        cls.admin.save(update_fields=["role"])

        cls.manager = User.objects.create_user(
            username="w2142_mgr",
            email="m@w2142.ru",
            password="pw",
            role=User.Role.MANAGER,
            branch=cls.branch,
        )

        cls.tenderist = User.objects.create_user(
            username="w2142_tn",
            email="t@w2142.ru",
            password="pw",
            role=User.Role.TENDERIST,
            branch=cls.branch,
        )

        # Force PolicyConfig в ENFORCE mode (test DB default = OBSERVE_ONLY)
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

    # --- Dictionaries page ---

    def test_settings_dicts(self):
        self._assert_admin_ok_others_denied("GET", "/admin/dicts/")

    # --- company_status CRUD ---

    def test_company_status_create(self):
        self._assert_admin_ok_others_denied("GET", "/admin/dicts/company-status/new/")

    def test_company_status_edit(self):
        entry = make_disposable_dict_entry(CompanyStatus)
        self._assert_admin_ok_others_denied("GET", f"/admin/dicts/company-status/{entry.id}/edit/")

    def test_company_status_delete(self):
        """Admin deletes disposable entry, non-admin denied."""
        entry = make_disposable_dict_entry(CompanyStatus)
        # Non-admin denied первыми
        for user, label in [(self.manager, "manager"), (self.tenderist, "tenderist")]:
            c = Client()
            c.force_login(user)
            r = c.post(f"/admin/dicts/company-status/{entry.id}/delete/")
            self.assertEqual(r.status_code, 403, f"{label} should be denied")
        # Admin deletes
        c = Client()
        c.force_login(self.admin)
        r = c.post(f"/admin/dicts/company-status/{entry.id}/delete/")
        self.assertIn(r.status_code, [200, 302])
        self.assertFalse(CompanyStatus.objects.filter(id=entry.id).exists())

    # --- company_sphere CRUD ---

    def test_company_sphere_create(self):
        self._assert_admin_ok_others_denied("GET", "/admin/dicts/company-sphere/new/")

    def test_company_sphere_edit(self):
        entry = make_disposable_dict_entry(CompanySphere)
        self._assert_admin_ok_others_denied("GET", f"/admin/dicts/company-sphere/{entry.id}/edit/")

    def test_company_sphere_delete(self):
        entry = make_disposable_dict_entry(CompanySphere)
        for user, label in [(self.manager, "manager"), (self.tenderist, "tenderist")]:
            c = Client()
            c.force_login(user)
            r = c.post(f"/admin/dicts/company-sphere/{entry.id}/delete/")
            self.assertEqual(r.status_code, 403)
        c = Client()
        c.force_login(self.admin)
        r = c.post(f"/admin/dicts/company-sphere/{entry.id}/delete/", {"action": "delete"})
        self.assertIn(r.status_code, [200, 302])
        self.assertFalse(CompanySphere.objects.filter(id=entry.id).exists())

    # --- contract_type CRUD ---

    def test_contract_type_create(self):
        self._assert_admin_ok_others_denied("GET", "/admin/dicts/contract-type/new/")

    def test_contract_type_edit(self):
        entry = make_disposable_dict_entry(ContractType)
        self._assert_admin_ok_others_denied("GET", f"/admin/dicts/contract-type/{entry.id}/edit/")

    def test_contract_type_delete(self):
        entry = make_disposable_dict_entry(ContractType)
        for user, label in [(self.manager, "manager"), (self.tenderist, "tenderist")]:
            c = Client()
            c.force_login(user)
            r = c.post(f"/admin/dicts/contract-type/{entry.id}/delete/")
            self.assertEqual(r.status_code, 403)
        c = Client()
        c.force_login(self.admin)
        r = c.post(f"/admin/dicts/contract-type/{entry.id}/delete/")
        self.assertIn(r.status_code, [200, 302])
        self.assertFalse(ContractType.objects.filter(id=entry.id).exists())

    # --- task_type CRUD ---

    def test_task_type_create(self):
        self._assert_admin_ok_others_denied("GET", "/admin/dicts/task-type/new/")

    def test_task_type_edit(self):
        entry = make_disposable_dict_entry(TaskType)
        self._assert_admin_ok_others_denied("GET", f"/admin/dicts/task-type/{entry.id}/edit/")

    def test_task_type_delete(self):
        entry = make_disposable_dict_entry(TaskType)
        for user, label in [(self.manager, "manager"), (self.tenderist, "tenderist")]:
            c = Client()
            c.force_login(user)
            r = c.post(f"/admin/dicts/task-type/{entry.id}/delete/")
            self.assertEqual(r.status_code, 403)
        c = Client()
        c.force_login(self.admin)
        r = c.post(f"/admin/dicts/task-type/{entry.id}/delete/")
        self.assertIn(r.status_code, [200, 302])
        self.assertFalse(TaskType.objects.filter(id=entry.id).exists())

    # --- Audit logs ---

    def test_settings_activity(self):
        self._assert_admin_ok_others_denied("GET", "/admin/activity/")

    def test_settings_error_log(self):
        self._assert_admin_ok_others_denied("GET", "/admin/error-log/")

    def _make_disposable_error_log(self):
        return ErrorLog.objects.create(
            level="warning",
            exception_type="DisposableW2142Error",
            message="disposable for W2.1.4.2 tests",
            path="/test/",
            method="GET",
        )

    def test_settings_error_log_resolve(self):
        err = self._make_disposable_error_log()
        for user, label in [(self.manager, "manager"), (self.tenderist, "tenderist")]:
            c = Client()
            c.force_login(user)
            r = c.post(f"/admin/error-log/{err.id}/resolve/")
            self.assertEqual(r.status_code, 403)
        c = Client()
        c.force_login(self.admin)
        r = c.post(f"/admin/error-log/{err.id}/resolve/")
        self.assertIn(r.status_code, [200, 302])
        err.refresh_from_db()
        self.assertTrue(err.resolved, "Admin resolve должен выставить resolved=True")

    def test_settings_error_log_unresolve(self):
        err = self._make_disposable_error_log()
        err.resolved = True
        err.save()
        for user, label in [(self.manager, "manager"), (self.tenderist, "tenderist")]:
            c = Client()
            c.force_login(user)
            r = c.post(f"/admin/error-log/{err.id}/unresolve/")
            self.assertEqual(r.status_code, 403)
        c = Client()
        c.force_login(self.admin)
        r = c.post(f"/admin/error-log/{err.id}/unresolve/")
        self.assertIn(r.status_code, [200, 302])
        err.refresh_from_db()
        self.assertFalse(err.resolved)

    def test_settings_error_log_details(self):
        err = self._make_disposable_error_log()
        self._assert_admin_ok_others_denied("GET", f"/admin/error-log/{err.id}/details/")

    # --- Defense-in-depth source verification ---

    def test_defense_in_depth_all_endpoints(self):
        """Inline require_admin() + @policy_required present в каждом endpoint."""
        import inspect

        from ui.views import settings_core

        endpoints = [
            "settings_dicts",
            "settings_company_status_create",
            "settings_company_status_edit",
            "settings_company_status_delete",
            "settings_company_sphere_create",
            "settings_company_sphere_edit",
            "settings_company_sphere_delete",
            "settings_contract_type_create",
            "settings_contract_type_edit",
            "settings_contract_type_delete",
            "settings_task_type_create",
            "settings_task_type_edit",
            "settings_task_type_delete",
            "settings_activity",
            "settings_error_log",
            "settings_error_log_resolve",
            "settings_error_log_unresolve",
            "settings_error_log_details",
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

    # --- Disposable helper smoke ---

    def test_disposable_user_helper(self):
        """make_disposable_user produces unique user с unusable password."""
        u1 = make_disposable_user(role="manager", branch=self.branch)
        u2 = make_disposable_user(role="manager", branch=self.branch)
        self.assertNotEqual(u1.username, u2.username)
        self.assertTrue(u1.username.startswith("disp_"))
        self.assertFalse(u1.has_usable_password())
        self.assertEqual(u1.role, "manager")
        self.assertEqual(u1.branch.id, self.branch.id)
