"""Тесты Plan 2 Task 5:
- Расширение GET /api/conversations/agents/ (branch_id, online)
- GET /api/messenger/branches/
- POST /api/conversations/{id}/needs-help/
"""

from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Branch
from messenger.models import Contact, Conversation, Inbox
from messenger.signals import auto_assign_new_conversation

User = get_user_model()


@override_settings(MESSENGER_ENABLED=True)
class AgentsActionFilterTests(TestCase):
    """Фильтры GET /api/conversations/agents/."""

    def setUp(self):
        self.ekb = Branch.objects.create(name="ЕКБ", code="ekb")
        self.krd = Branch.objects.create(name="КРД", code="krd")

        self.op_ekb_online = User.objects.create_user(
            "op_ekb_on",
            password="pw",
            role=User.Role.MANAGER,
            branch=self.ekb,
            first_name="Иван",
            last_name="Екатеринбуржец",
            messenger_online=True,
            messenger_last_seen=timezone.now(),
        )
        self.op_ekb_offline = User.objects.create_user(
            "op_ekb_off",
            password="pw",
            role=User.Role.MANAGER,
            branch=self.ekb,
            messenger_online=False,
        )
        self.op_krd_online = User.objects.create_user(
            "op_krd_on",
            password="pw",
            role=User.Role.MANAGER,
            branch=self.krd,
            messenger_online=True,
            messenger_last_seen=timezone.now(),
        )

        self.admin = User.objects.create_user(
            "admin", password="pw", role=User.Role.ADMIN, is_superuser=True,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def test_agents_backward_compatible_returns_all_managers(self):
        resp = self.client.get("/api/conversations/agents/")
        self.assertEqual(resp.status_code, 200)
        ids = {a["id"] for a in resp.data}
        self.assertIn(self.op_ekb_online.id, ids)
        self.assertIn(self.op_ekb_offline.id, ids)
        self.assertIn(self.op_krd_online.id, ids)
        # admin не является MANAGER — должен быть отфильтрован
        self.assertNotIn(self.admin.id, ids)

    def test_agents_filter_by_branch(self):
        resp = self.client.get(
            f"/api/conversations/agents/?branch_id={self.ekb.id}"
        )
        self.assertEqual(resp.status_code, 200)
        ids = {a["id"] for a in resp.data}
        self.assertEqual(
            ids, {self.op_ekb_online.id, self.op_ekb_offline.id}
        )

    def test_agents_filter_online_only(self):
        resp = self.client.get("/api/conversations/agents/?online=1")
        self.assertEqual(resp.status_code, 200)
        ids = {a["id"] for a in resp.data}
        self.assertIn(self.op_ekb_online.id, ids)
        self.assertIn(self.op_krd_online.id, ids)
        self.assertNotIn(self.op_ekb_offline.id, ids)

    def test_agents_filter_branch_and_online(self):
        resp = self.client.get(
            f"/api/conversations/agents/?branch_id={self.ekb.id}&online=true"
        )
        self.assertEqual(resp.status_code, 200)
        ids = {a["id"] for a in resp.data}
        self.assertEqual(ids, {self.op_ekb_online.id})


@override_settings(MESSENGER_ENABLED=True)
class BranchesEndpointTests(TestCase):
    """GET /api/messenger/branches/."""

    def setUp(self):
        self.ekb = Branch.objects.create(name="ЕКБ", code="ekb")
        self.krd = Branch.objects.create(name="КРД", code="krd")
        self.inactive = Branch.objects.create(
            name="Неактивный", code="old", is_active=False,
        )
        self.user = User.objects.create_user(
            "u", password="pw", role=User.Role.MANAGER, branch=self.ekb,
        )
        self.client = APIClient()

    def test_requires_authentication(self):
        resp = self.client.get("/api/messenger/branches/")
        self.assertIn(resp.status_code, (401, 403))

    def test_authenticated_user_receives_branches(self):
        self.client.force_authenticate(self.user)
        resp = self.client.get("/api/messenger/branches/")
        self.assertEqual(resp.status_code, 200)
        codes = {b["code"] for b in resp.data}
        self.assertIn("ekb", codes)
        self.assertIn("krd", codes)
        # Неактивный филиал не должен возвращаться
        self.assertNotIn("old", codes)
        # Каждый элемент имеет нужные поля
        first = resp.data[0]
        self.assertIn("id", first)
        self.assertIn("name", first)
        self.assertIn("code", first)


@override_settings(MESSENGER_ENABLED=True)
class NeedsHelpActionTests(TestCase):
    """POST /api/conversations/{id}/needs-help/."""

    def setUp(self):
        # Отключаем авто-назначение, чтобы контролировать assignee вручную.
        post_save.disconnect(auto_assign_new_conversation, sender=Conversation)
        self.addCleanup(
            post_save.connect, auto_assign_new_conversation, sender=Conversation
        )

        self.branch = Branch.objects.create(name="ЕКБ", code="ekb")
        self.assignee = User.objects.create_user(
            "assignee",
            password="pw",
            role=User.Role.MANAGER,
            branch=self.branch,
        )
        self.other = User.objects.create_user(
            "other",
            password="pw",
            role=User.Role.MANAGER,
            branch=self.branch,
        )
        self.admin = User.objects.create_user(
            "admin",
            password="pw",
            role=User.Role.ADMIN,
            is_superuser=True,
        )
        self.inbox = Inbox.objects.create(name="Widget", branch=self.branch)
        self.contact = Contact.objects.create(name="Client")
        self.conv = Conversation.objects.create(
            inbox=self.inbox,
            contact=self.contact,
            assignee=self.assignee,
        )

        self.client = APIClient()

    def test_assignee_can_raise_needs_help(self):
        self.client.force_authenticate(self.assignee)
        resp = self.client.post(
            f"/api/conversations/{self.conv.id}/needs-help/"
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        self.conv.refresh_from_db()
        self.assertTrue(self.conv.needs_help)
        self.assertIsNotNone(self.conv.needs_help_at)

    def test_admin_can_raise_needs_help(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.post(
            f"/api/conversations/{self.conv.id}/needs-help/"
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        self.conv.refresh_from_db()
        self.assertTrue(self.conv.needs_help)

    def test_foreign_manager_forbidden(self):
        self.client.force_authenticate(self.other)
        resp = self.client.post(
            f"/api/conversations/{self.conv.id}/needs-help/"
        )
        self.assertEqual(resp.status_code, 403)
        self.conv.refresh_from_db()
        self.assertFalse(self.conv.needs_help)

    def test_needs_help_branch_director_not_assignee_allowed(self):
        """BRANCH_DIRECTOR, не являющийся assignee, может поднять флаг."""
        director = User.objects.create_user(
            "director",
            password="pw",
            role=User.Role.BRANCH_DIRECTOR,
            branch=self.branch,
        )
        self.client.force_authenticate(director)
        resp = self.client.post(
            f"/api/conversations/{self.conv.id}/needs-help/"
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        self.conv.refresh_from_db()
        self.assertTrue(self.conv.needs_help)

    def test_needs_help_sales_head_not_assignee_allowed(self):
        """SALES_HEAD, не являющийся assignee, может поднять флаг."""
        rop = User.objects.create_user(
            "rop",
            password="pw",
            role=User.Role.SALES_HEAD,
            branch=self.branch,
        )
        self.client.force_authenticate(rop)
        resp = self.client.post(
            f"/api/conversations/{self.conv.id}/needs-help/"
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        self.conv.refresh_from_db()
        self.assertTrue(self.conv.needs_help)
