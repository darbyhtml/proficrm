"""Тесты preview-страницы редизайна списка компаний."""

from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.contrib.auth import get_user_model

User = get_user_model()


@override_settings(SECURE_SSL_REDIRECT=False)
class CompanyListV2PreviewTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_user(
            username="admin_cl2", password="pw",
            role=User.Role.ADMIN, is_staff=True,
        )
        self.manager = User.objects.create_user(
            username="mgr_cl2", password="pw", role=User.Role.MANAGER,
        )

    def test_admin_sees_preview(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("company_list_v2_preview"))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "ui/company_list_v2.html")

    def test_manager_forbidden(self):
        self.client.force_login(self.manager)
        resp = self.client.get(reverse("company_list_v2_preview"))
        self.assertEqual(resp.status_code, 403)

    def test_regular_list_still_v1(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("company_list"))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "ui/company_list.html")
