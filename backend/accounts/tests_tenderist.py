"""
Тесты для роли TENDERIST (см. docs/decisions.md 2026-04-15 §7.2).

Проверяют:
- permissions компаний: read-only, не может быть ответственным
- policy engine: доступ к страницам/экшенам по матрице
- messenger: полностью исключён (visible_* пустые)
"""

from django.test import TestCase

from accounts.models import Branch, User
from companies.models import Company
from companies.permissions import (
    can_edit_company,
    can_transfer_company,
    editable_company_qs,
    get_transfer_targets,
    get_users_for_lists,
)
from messenger.selectors import visible_conversations_qs, visible_inboxes_qs
from policy.engine import baseline_allowed_for_role
from policy.models import PolicyRule


class TenderistPermissionsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.branch = Branch.objects.create(code="msk", name="Москва")
        cls.tenderist = User.objects.create_user(
            username="t1",
            password="pwd12345",
            role=User.Role.TENDERIST,
            branch=cls.branch,
        )
        cls.manager = User.objects.create_user(
            username="m1",
            password="pwd12345",
            role=User.Role.MANAGER,
            branch=cls.branch,
        )
        cls.company = Company.objects.create(
            name="ООО Тест",
            responsible=cls.manager,
            branch=cls.branch,
        )

    def test_tenderist_cannot_edit_company(self):
        self.assertFalse(can_edit_company(self.tenderist, self.company))

    def test_tenderist_editable_qs_empty(self):
        self.assertEqual(editable_company_qs(self.tenderist).count(), 0)

    def test_tenderist_cannot_transfer(self):
        self.assertFalse(can_transfer_company(self.tenderist, self.company))

    def test_tenderist_not_in_transfer_targets(self):
        targets = get_transfer_targets(self.manager).values_list("id", flat=True)
        self.assertNotIn(self.tenderist.id, targets)

    def test_tenderist_excluded_from_user_lists(self):
        ids = get_users_for_lists().values_list("id", flat=True)
        self.assertNotIn(self.tenderist.id, ids)
        self.assertIn(self.manager.id, ids)

    def test_tenderist_is_tenderist_property(self):
        self.assertTrue(self.tenderist.is_tenderist)
        self.assertFalse(self.manager.is_tenderist)


class TenderistPolicyBaselineTests(TestCase):
    def _page(self, key):
        return baseline_allowed_for_role(
            role=User.Role.TENDERIST,
            resource_type=PolicyRule.ResourceType.PAGE,
            resource_key=key,
        )

    def _action(self, key):
        return baseline_allowed_for_role(
            role=User.Role.TENDERIST,
            resource_type=PolicyRule.ResourceType.ACTION,
            resource_key=key,
        )

    def test_pages_allowed(self):
        for key in (
            "ui:dashboard",
            "ui:companies:list",
            "ui:companies:detail",
            "ui:tasks:list",
            "ui:tasks:detail",
        ):
            self.assertTrue(self._page(key), f"должен видеть {key}")

    def test_pages_denied(self):
        for key in (
            "ui:mail",
            "ui:analytics",
            "ui:settings",
            "ui:mail:campaigns",
            "ui:mail:settings",
        ):
            self.assertFalse(self._page(key), f"не должен видеть {key}")

    def test_company_write_denied(self):
        for key in (
            "ui:companies:create",
            "ui:companies:update",
            "ui:companies:delete",
            "ui:companies:transfer",
        ):
            self.assertFalse(self._action(key), f"нельзя {key}")

    def test_tasks_allowed(self):
        for key in ("ui:tasks:create", "ui:tasks:update", "ui:tasks:status", "ui:tasks:delete"):
            self.assertTrue(self._action(key), f"должен мочь {key}")

    def test_mail_campaigns_denied(self):
        self.assertFalse(self._action("ui:mail:campaigns:create"))
        self.assertFalse(self._action("ui:mail:campaigns:start"))

    def test_autocomplete_allowed(self):
        self.assertTrue(self._action("ui:companies:autocomplete"))

    def test_notifications_allowed(self):
        self.assertTrue(self._action("ui:notifications:poll"))


class TenderistMessengerTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.branch = Branch.objects.create(code="spb", name="СПб")
        cls.tenderist = User.objects.create_user(
            username="t2",
            password="pwd12345",
            role=User.Role.TENDERIST,
            branch=cls.branch,
        )

    def test_tenderist_sees_no_inboxes(self):
        self.assertEqual(visible_inboxes_qs(self.tenderist).count(), 0)

    def test_tenderist_sees_no_conversations(self):
        self.assertEqual(visible_conversations_qs(self.tenderist).count(), 0)
