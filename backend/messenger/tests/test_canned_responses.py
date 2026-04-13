from django.conf import settings
from django.test import TestCase
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from accounts.models import Branch
from messenger.models import CannedResponse

User = get_user_model()


class CannedResponseFieldsTests(TestCase):
    def setUp(self):
        self.branch = Branch.objects.create(name="Br", code="br")
        self.user = User.objects.create_user("u", password="pw")

    def test_defaults(self):
        cr = CannedResponse.objects.create(
            title="Привет", body="Здравствуйте!",
            branch=self.branch, created_by=self.user,
        )
        self.assertFalse(cr.is_quick_button)
        self.assertEqual(cr.sort_order, 0)

    def test_quick_buttons_ordered_by_sort_order(self):
        CannedResponse.objects.create(
            title="B", body="b",
            branch=self.branch, created_by=self.user,
            is_quick_button=True, sort_order=10,
        )
        CannedResponse.objects.create(
            title="A", body="a",
            branch=self.branch, created_by=self.user,
            is_quick_button=True, sort_order=5,
        )
        titles = list(
            CannedResponse.objects.filter(is_quick_button=True)
            .values_list("title", flat=True)
        )
        self.assertEqual(titles, ["A", "B"])  # sort_order 5 before 10 через Meta.ordering


class QuickButtonFilterTests(TestCase):
    """Plan 2 Task 11 — фильтр ?quick=1 в CannedResponseViewSet."""

    def setUp(self):
        self._orig_messenger_enabled = getattr(settings, "MESSENGER_ENABLED", False)
        settings.MESSENGER_ENABLED = True
        self.branch = Branch.objects.create(name="Br", code="br")
        self.user = User.objects.create_user(
            username="quser",
            email="quser@test.com",
            password="pw",
            role=User.Role.ADMIN,
        )
        CannedResponse.objects.create(
            title="Quick A", body="a",
            branch=self.branch, created_by=self.user,
            is_quick_button=True, sort_order=2,
        )
        CannedResponse.objects.create(
            title="Quick B", body="b",
            branch=self.branch, created_by=self.user,
            is_quick_button=True, sort_order=1,
        )
        CannedResponse.objects.create(
            title="Regular", body="r",
            branch=self.branch, created_by=self.user,
            is_quick_button=False,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def tearDown(self):
        settings.MESSENGER_ENABLED = self._orig_messenger_enabled

    def _titles(self, response):
        data = response.json()
        items = data.get("results", data) if isinstance(data, dict) else data
        return [item["title"] for item in items]

    def test_quick_filter_returns_only_quick_buttons_ordered(self):
        resp = self.client.get("/api/canned-responses/?quick=1")
        self.assertEqual(resp.status_code, 200)
        titles = self._titles(resp)
        self.assertEqual(titles, ["Quick B", "Quick A"])  # sort_order 1, 2

    def test_no_filter_returns_all(self):
        resp = self.client.get("/api/canned-responses/")
        self.assertEqual(resp.status_code, 200)
        titles = self._titles(resp)
        self.assertEqual(set(titles), {"Quick A", "Quick B", "Regular"})
