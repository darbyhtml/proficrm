"""W2.1.3c — Verify Group B codification preserves behavior (3 endpoints).

Endpoints codified:
- company_cold_call_toggle  → @policy_required(action, ui:companies:cold_call:toggle)
- company_cold_call_reset   → @policy_required(action, ui:companies:cold_call:reset)
- company_timeline_items    → @policy_required(page, ui:companies:detail)

Zero regression for main use cases. Behavior parity с already-codified
variants (contact_*/phone_*).
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings

from accounts.models import Branch
from companies.models import Company

User = get_user_model()


@override_settings(
    SECURE_SSL_REDIRECT=False,
    SECURE_HSTS_SECONDS=0,
    SESSION_COOKIE_SECURE=False,
)
class W2GroupBCodificationTests(TestCase):
    def setUp(self):
        # Test DB has no seeded rules — create explicit deny rule parity
        # со staging DB (manager denied for cold_call:reset).
        from policy.models import PolicyConfig, PolicyRule

        cfg = PolicyConfig.load()
        cfg.mode = PolicyConfig.Mode.ENFORCE
        cfg.save()
        PolicyRule.objects.create(
            enabled=True,
            priority=10,
            subject_type=PolicyRule.SubjectType.ROLE,
            role=User.Role.MANAGER,
            resource_type=PolicyRule.ResourceType.ACTION,
            resource="ui:companies:cold_call:reset",
            effect=PolicyRule.Effect.DENY,
        )
        # Allow manager access ui:companies:detail (smart default requires
        # visible_companies_qs check which works в real DB, но explicit allow
        # для test determinism).
        PolicyRule.objects.create(
            enabled=True,
            priority=10,
            subject_type=PolicyRule.SubjectType.ROLE,
            role=User.Role.MANAGER,
            resource_type=PolicyRule.ResourceType.PAGE,
            resource="ui:companies:detail",
            effect=PolicyRule.Effect.ALLOW,
        )
        PolicyRule.objects.create(
            enabled=True,
            priority=10,
            subject_type=PolicyRule.SubjectType.ROLE,
            role=User.Role.MANAGER,
            resource_type=PolicyRule.ResourceType.ACTION,
            resource="ui:companies:cold_call:toggle",
            effect=PolicyRule.Effect.ALLOW,
        )

        self.branch_ekb = Branch.objects.create(code="gb_ekb", name="GB EKB")
        self.admin = User.objects.create_superuser(
            username="gb_admin",
            email="a@gb.ru",
            password="pw",
        )
        self.admin.role = User.Role.ADMIN
        self.admin.save(update_fields=["role"])
        self.manager = User.objects.create_user(
            username="gb_mgr",
            email="m@gb.ru",
            password="pw",
            role=User.Role.MANAGER,
            branch=self.branch_ekb,
        )
        self.company = Company.objects.create(
            name="gb_co",
            branch=self.branch_ekb,
            responsible=self.manager,
            phone="+79005551234",
        )

    # --- Endpoint #1: company_cold_call_toggle ---

    def test_toggle_manager_allowed(self):
        """Manager can trigger cold_call toggle on own company."""
        c = Client()
        c.force_login(self.manager)
        r = c.post(f"/companies/{self.company.id}/cold-call/toggle/")
        # 302 redirect expected (non-AJAX POST)
        self.assertEqual(r.status_code, 302)

    def test_toggle_admin_allowed(self):
        c = Client()
        c.force_login(self.admin)
        r = c.post(f"/companies/{self.company.id}/cold-call/toggle/")
        self.assertEqual(r.status_code, 302)

    # --- Endpoint #2: company_cold_call_reset ---

    def test_reset_manager_denied_by_policy(self):
        """Manager cannot reset cold_call — policy rule denies (parity
        with contact_cold_call_reset which also denies manager role)."""
        c = Client()
        c.force_login(self.manager)
        r = c.post(f"/companies/{self.company.id}/cold-call/reset/")
        # @policy_required denies for MANAGER role → 403
        self.assertEqual(r.status_code, 403)

    def test_reset_admin_allowed(self):
        c = Client()
        c.force_login(self.admin)
        r = c.post(f"/companies/{self.company.id}/cold-call/reset/")
        # Admin passes policy, hits inline not_marked branch → 302 redirect
        self.assertEqual(r.status_code, 302)

    # --- Endpoint #3: company_timeline_items ---

    def test_timeline_manager_allowed(self):
        c = Client()
        c.force_login(self.manager)
        r = c.get(f"/companies/{self.company.id}/timeline/items/?offset=0&limit=10")
        self.assertEqual(r.status_code, 200)

    def test_timeline_admin_allowed(self):
        c = Client()
        c.force_login(self.admin)
        r = c.get(f"/companies/{self.company.id}/timeline/items/?offset=0&limit=10")
        self.assertEqual(r.status_code, 200)
