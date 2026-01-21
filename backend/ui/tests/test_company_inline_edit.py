"""
Тесты для инлайн-редактирования компании (companies/<id>/inline/).
"""

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings

from companies.models import Company

User = get_user_model()


@override_settings(SECURE_SSL_REDIRECT=False)
class CompanyInlineEditTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.owner = User.objects.create_user(
            username="owner",
            password="pass12345",
            role=User.Role.MANAGER,
        )
        self.other = User.objects.create_user(
            username="other",
            password="pass12345",
            role=User.Role.MANAGER,
        )
        self.company = Company.objects.create(name="Компания", responsible=self.owner)

    def test_inline_update_success(self):
        self.client.force_login(self.owner)
        url = f"/companies/{self.company.id}/inline/"
        resp = self.client.post(
            url,
            data={"field": "legal_name", "value": "ООО Ромашка"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["field"], "legal_name")
        self.assertEqual(data["value"], "ООО Ромашка")

        self.company.refresh_from_db()
        self.assertEqual(self.company.legal_name, "ООО Ромашка")

    def test_inline_update_forbidden_for_not_owner(self):
        self.client.force_login(self.other)
        url = f"/companies/{self.company.id}/inline/"
        resp = self.client.post(
            url,
            data={"field": "legal_name", "value": "X"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(resp.status_code, 403)
        data = resp.json()
        self.assertFalse(data["ok"])

    def test_inline_update_validation_error_name_required(self):
        self.client.force_login(self.owner)
        url = f"/companies/{self.company.id}/inline/"
        resp = self.client.post(
            url,
            data={"field": "name", "value": "   "},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertFalse(data["ok"])
        self.assertIn("errors", data)
        self.assertIn("name", data["errors"])

    def test_inline_update_rejects_unknown_field(self):
        self.client.force_login(self.owner)
        url = f"/companies/{self.company.id}/inline/"
        resp = self.client.post(
            url,
            data={"field": "responsible_id", "value": "1"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertFalse(data["ok"])

