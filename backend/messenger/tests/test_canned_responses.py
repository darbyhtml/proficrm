from django.test import TestCase
from django.contrib.auth import get_user_model
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
