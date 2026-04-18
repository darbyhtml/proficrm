"""F4 R3 tests: company_detail_v3_preview + contact_quick_create.

Проверяем:
- view v3 рендерится корректно для разных вариантов
- Permission decorators работают: чужая компания → 403/404
- contact_quick_create создаёт Contact + ContactPhone + ContactEmail
- contact_quick_create требует права на редактирование
- Edge cases: company.inn=None, contacts=empty, task=empty
"""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from accounts.models import Branch
from companies.models import Company, Contact, ContactEmail, ContactPhone

User = get_user_model()


@override_settings(
    SECURE_SSL_REDIRECT=False,
    SECURE_HSTS_SECONDS=0,
    SESSION_COOKIE_SECURE=False,
    MESSENGER_WIDGET_STRICT_ORIGIN=False,
)
class CompanyDetailV3PreviewTests(TestCase):
    def setUp(self):
        self.branch = Branch.objects.create(code="v3t", name="V3 Test Branch")
        self.admin = User.objects.create_superuser(
            username="v3_admin", email="a@v3.ru",
        )
        self.manager = User.objects.create_user(
            username="v3_mgr", email="m@v3.ru",
            role=User.Role.MANAGER, branch=self.branch,
        )
        self.other_mgr = User.objects.create_user(
            username="v3_other", email="o@v3.ru",
            role=User.Role.MANAGER, branch=self.branch,
        )
        self.company = Company.objects.create(
            name="Тестовая компания",
            inn="7701234567",
            branch=self.branch,
            responsible=self.manager,
        )

    def test_admin_can_open_any_variant(self):
        self.client.force_login(self.admin)
        for v in ("a", "b", "c"):
            r = self.client.get(f"/companies/{self.company.id}/v3/{v}/")
            self.assertEqual(r.status_code, 200, f"variant {v}")
            self.assertIn(b"\xd0\xa2\xd0\xb5\xd1\x81\xd1\x82\xd0\xbe\xd0\xb2\xd0\xb0\xd1\x8f", r.content)  # «Тестовая»

    def test_responsible_manager_can_open(self):
        self.client.force_login(self.manager)
        r = self.client.get(f"/companies/{self.company.id}/v3/b/")
        self.assertEqual(r.status_code, 200)

    def test_unknown_variant_returns_404(self):
        self.client.force_login(self.admin)
        r = self.client.get(f"/companies/{self.company.id}/v3/zz/")
        self.assertEqual(r.status_code, 404)

    def test_anonymous_redirected_to_login(self):
        r = self.client.get(f"/companies/{self.company.id}/v3/b/")
        # login_required → 302
        self.assertEqual(r.status_code, 302)

    def test_renders_with_empty_inn(self):
        company = Company.objects.create(
            name="Без ИНН", branch=self.branch, responsible=self.admin,
        )
        self.client.force_login(self.admin)
        r = self.client.get(f"/companies/{company.id}/v3/b/")
        self.assertEqual(r.status_code, 200)
        # Плейсхолдер «—» должен отображаться
        self.assertIn(b"\xe2\x80\x94", r.content)  # em-dash


@override_settings(
    SECURE_SSL_REDIRECT=False,
    SECURE_HSTS_SECONDS=0,
    SESSION_COOKIE_SECURE=False,
    MESSENGER_WIDGET_STRICT_ORIGIN=False,
)
class ContactQuickCreateTests(TestCase):
    def setUp(self):
        self.branch = Branch.objects.create(code="cqc", name="CQC Branch")
        self.admin = User.objects.create_superuser(
            username="cqc_admin", email="a@c.ru",
        )
        self.manager = User.objects.create_user(
            username="cqc_mgr", email="m@c.ru",
            role=User.Role.MANAGER, branch=self.branch,
        )
        self.foreign = User.objects.create_user(
            username="cqc_other", email="o@c.ru",
            role=User.Role.MANAGER, branch=self.branch,
        )
        self.company = Company.objects.create(
            name="CQC Test Co", branch=self.branch, responsible=self.manager,
            inn="7712345678",
        )

    def test_quick_create_single_name_splits_to_first_last(self):
        self.client.force_login(self.admin)
        r = self.client.post(
            f"/companies/{self.company.id}/contacts/quick-create/",
            {"name": "Иванов Иван", "position": "Директор",
             "phone": "+79999999999", "email": "i@i.ru"},
        )
        self.assertEqual(r.status_code, 302)
        self.assertIn("/v3/b/", r.url)
        c = Contact.objects.get(company=self.company)
        self.assertEqual(c.last_name, "Иванов")
        self.assertEqual(c.first_name, "Иван")
        self.assertEqual(c.position, "Директор")
        self.assertEqual(ContactPhone.objects.filter(contact=c).count(), 1)
        self.assertEqual(ContactEmail.objects.filter(contact=c).count(), 1)

    def test_quick_create_explicit_first_last(self):
        self.client.force_login(self.admin)
        r = self.client.post(
            f"/companies/{self.company.id}/contacts/quick-create/",
            {"first_name": "Пётр", "last_name": "Петров", "position": ""},
        )
        self.assertEqual(r.status_code, 302)
        c = Contact.objects.get(company=self.company)
        self.assertEqual(c.last_name, "Петров")
        self.assertEqual(c.first_name, "Пётр")
        # Без телефона/email — не создаются
        self.assertFalse(ContactPhone.objects.filter(contact=c).exists())
        self.assertFalse(ContactEmail.objects.filter(contact=c).exists())

    def test_quick_create_requires_name(self):
        self.client.force_login(self.admin)
        r = self.client.post(
            f"/companies/{self.company.id}/contacts/quick-create/",
            {"name": "", "position": "ЛПР"},
        )
        self.assertEqual(r.status_code, 400)
        self.assertFalse(Contact.objects.filter(company=self.company).exists())

    def test_quick_create_requires_post(self):
        self.client.force_login(self.admin)
        r = self.client.get(f"/companies/{self.company.id}/contacts/quick-create/")
        self.assertEqual(r.status_code, 405)  # Method not allowed

    def test_quick_create_foreign_manager_denied(self):
        # Менеджер другого branch не может добавить контакт
        other_branch = Branch.objects.create(code="cqc2", name="CQC2")
        other = User.objects.create_user(
            username="cqc_foreign", email="f@c.ru",
            role=User.Role.MANAGER, branch=other_branch,
        )
        self.client.force_login(other)
        r = self.client.post(
            f"/companies/{self.company.id}/contacts/quick-create/",
            {"name": "Нежелательный"},
        )
        # policy_required / require_can_view_company → 403 или 302
        self.assertIn(r.status_code, (302, 403))
        self.assertFalse(Contact.objects.filter(company=self.company).exists())
