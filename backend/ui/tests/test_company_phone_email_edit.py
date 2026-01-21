"""
Тесты для инлайн-редактирования телефонов и email компании через AJAX эндпойнты.
"""

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings

from companies.models import Company, CompanyEmail, CompanyPhone

User = get_user_model()


@override_settings(SECURE_SSL_REDIRECT=False)
class CompanyPhoneEmailEditTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.owner = User.objects.create_user(username="owner2", password="pass12345", role=User.Role.MANAGER)
        self.other = User.objects.create_user(username="other2", password="pass12345", role=User.Role.MANAGER)
        self.company = Company.objects.create(name="C", responsible=self.owner, phone="+79990001122", email="main@example.com")
        self.phone2 = CompanyPhone.objects.create(company=self.company, value="+79990002233")
        self.email2 = CompanyEmail.objects.create(company=self.company, value="alt@example.com")

    def test_update_main_phone_success_and_format(self):
        self.client.force_login(self.owner)
        url = f"/companies/{self.company.id}/main-phone/update/"
        resp = self.client.post(url, data={"phone": "8 (999) 000-33-44"}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["success"])
        self.company.refresh_from_db()
        self.assertEqual(self.company.phone, "+79990003344")
        self.assertIn("+7", data.get("display", ""))

    def test_update_company_phone_reject_duplicate_with_main(self):
        self.client.force_login(self.owner)
        url = f"/company-phones/{self.phone2.id}/update/"
        resp = self.client.post(url, data={"phone": "+79990001122"}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.json()["success"])

    def test_update_main_email_invalid(self):
        self.client.force_login(self.owner)
        url = f"/companies/{self.company.id}/main-email/update/"
        resp = self.client.post(url, data={"email": "not-an-email"}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.json()["success"])

    def test_update_company_email_reject_duplicate_with_main(self):
        self.client.force_login(self.owner)
        url = f"/company-emails/{self.email2.id}/update/"
        resp = self.client.post(url, data={"email": "MAIN@example.com"}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.json()["success"])

    def test_forbidden_for_not_owner(self):
        self.client.force_login(self.other)
        url = f"/companies/{self.company.id}/main-phone/update/"
        resp = self.client.post(url, data={"phone": "+79990009988"}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(resp.json()["success"])

