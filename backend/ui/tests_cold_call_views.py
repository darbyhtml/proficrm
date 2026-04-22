"""URL-layer tests для cold_call views (W1.4 safety net before dedup).

Coverage: 8 URL endpoints в `backend/ui/views/pages/company/cold_call.py`:
- company_cold_call_toggle / reset
- contact_cold_call_toggle / reset
- contact_phone_cold_call_toggle / reset
- company_phone_cold_call_toggle / reset

Service-layer (`ColdCallService`) уже тестируется в `companies/tests_services.py`.
Здесь — только view behavior: HTTP status, permission enforcement, AJAX vs redirect,
idempotency flow, edge cases (no_phone, not_confirmed, not_marked).

W1.4 purpose: safety net для последующего dedup. После refactor 8 функций в
single generic handler + 8 thin wrappers — эти tests должны продолжать pass без
изменений (zero behavior change).
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from accounts.models import Branch
from companies.models import Company, CompanyPhone, Contact, ContactPhone

User = get_user_model()


AJAX_HEADERS = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}


@override_settings(
    SECURE_SSL_REDIRECT=False,
    SECURE_HSTS_SECONDS=0,
    SESSION_COOKIE_SECURE=False,
)
class ColdCallCompanyViewTests(TestCase):
    """Company cold-call toggle/reset views."""

    def setUp(self):
        self.branch = Branch.objects.create(code="cct", name="CC Test Branch")
        self.admin = User.objects.create_superuser(username="cc_admin_c", email="a@cc.ru")
        self.manager = User.objects.create_user(
            username="cc_mgr_c",
            email="m@cc.ru",
            role=User.Role.MANAGER,
            branch=self.branch,
        )
        self.other_mgr = User.objects.create_user(
            username="cc_other_c",
            email="o@cc.ru",
            role=User.Role.MANAGER,
            branch=self.branch,
        )
        self.company = Company.objects.create(
            name="CC Test Co",
            phone="+79001234501",
            responsible=self.manager,
            branch=self.branch,
        )

    # --- toggle ---

    def test_toggle_get_redirects(self):
        self.client.force_login(self.manager)
        r = self.client.get(f"/companies/{self.company.id}/cold-call/toggle/")
        self.assertEqual(r.status_code, 302)

    def test_toggle_ajax_without_confirmation_returns_400(self):
        self.client.force_login(self.manager)
        r = self.client.post(f"/companies/{self.company.id}/cold-call/toggle/", **AJAX_HEADERS)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["ok"], False)

    def test_toggle_ajax_confirmed_success(self):
        self.client.force_login(self.manager)
        r = self.client.post(
            f"/companies/{self.company.id}/cold-call/toggle/",
            {"confirmed": "1"},
            **AJAX_HEADERS,
        )
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue(data["ok"])
        self.assertTrue(data["is_cold_call"])
        self.company.refresh_from_db()
        self.assertTrue(self.company.primary_contact_is_cold_call)

    def test_toggle_non_ajax_redirect(self):
        self.client.force_login(self.manager)
        r = self.client.post(f"/companies/{self.company.id}/cold-call/toggle/", {"confirmed": "1"})
        self.assertEqual(r.status_code, 302)
        self.company.refresh_from_db()
        self.assertTrue(self.company.primary_contact_is_cold_call)

    def test_toggle_already_marked_ajax(self):
        self.client.force_login(self.manager)
        # First mark
        self.client.post(
            f"/companies/{self.company.id}/cold-call/toggle/",
            {"confirmed": "1"},
            **AJAX_HEADERS,
        )
        # Second attempt — already marked
        r = self.client.post(
            f"/companies/{self.company.id}/cold-call/toggle/",
            {"confirmed": "1"},
            **AJAX_HEADERS,
        )
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue(data["is_cold_call"])
        self.assertIn("уже", data["message"].lower())

    def test_toggle_ajax_no_phone(self):
        self.company.phone = ""
        self.company.save(update_fields=["phone"])
        self.client.force_login(self.manager)
        r = self.client.post(
            f"/companies/{self.company.id}/cold-call/toggle/",
            {"confirmed": "1"},
            **AJAX_HEADERS,
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn("телефон", r.json()["error"].lower())

    def test_toggle_no_permission(self):
        # other_mgr не ответственный, не админ, другая компания
        other_company = Company.objects.create(
            name="Other Co", responsible=self.manager, branch=self.branch
        )
        self.client.force_login(self.other_mgr)
        r = self.client.post(
            f"/companies/{other_company.id}/cold-call/toggle/",
            {"confirmed": "1"},
            **AJAX_HEADERS,
        )
        # Policy может блокировать раньше через policy_required или permission check
        self.assertIn(r.status_code, (302, 403))

    # --- reset ---

    def test_reset_requires_admin_non_admin_403(self):
        self.client.force_login(self.manager)
        r = self.client.post(f"/companies/{self.company.id}/cold-call/reset/", **AJAX_HEADERS)
        self.assertEqual(r.status_code, 403)

    def test_reset_admin_success(self):
        # First mark
        self.client.force_login(self.manager)
        self.client.post(
            f"/companies/{self.company.id}/cold-call/toggle/",
            {"confirmed": "1"},
            **AJAX_HEADERS,
        )
        # Reset as admin
        self.client.force_login(self.admin)
        r = self.client.post(f"/companies/{self.company.id}/cold-call/reset/", **AJAX_HEADERS)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["is_cold_call"])
        self.company.refresh_from_db()
        self.assertFalse(self.company.primary_contact_is_cold_call)

    def test_reset_not_marked(self):
        self.client.force_login(self.admin)
        r = self.client.post(f"/companies/{self.company.id}/cold-call/reset/", **AJAX_HEADERS)
        self.assertEqual(r.status_code, 200)
        self.assertIn("не отмечен", r.json()["message"].lower())

    def test_reset_get_redirects(self):
        self.client.force_login(self.admin)
        r = self.client.get(f"/companies/{self.company.id}/cold-call/reset/")
        self.assertEqual(r.status_code, 302)


@override_settings(
    SECURE_SSL_REDIRECT=False,
    SECURE_HSTS_SECONDS=0,
    SESSION_COOKIE_SECURE=False,
)
class ColdCallContactViewTests(TestCase):
    """Contact cold-call toggle/reset views."""

    def setUp(self):
        self.branch = Branch.objects.create(code="ccc", name="CCC Test Branch")
        self.admin = User.objects.create_superuser(username="cc_admin_ct", email="a@ccc.ru")
        self.manager = User.objects.create_user(
            username="cc_mgr_ct",
            email="m@ccc.ru",
            role=User.Role.MANAGER,
            branch=self.branch,
        )
        self.company = Company.objects.create(
            name="Contact Test Co",
            responsible=self.manager,
            branch=self.branch,
        )
        self.contact = Contact.objects.create(
            company=self.company,
            first_name="Иван",
            last_name="Иванов",
        )

    def test_contact_toggle_ajax_success(self):
        self.client.force_login(self.manager)
        r = self.client.post(
            f"/contacts/{self.contact.id}/cold-call/toggle/",
            {"confirmed": "1"},
            **AJAX_HEADERS,
        )
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["is_cold_call"])
        self.contact.refresh_from_db()
        self.assertTrue(self.contact.is_cold_call)

    def test_contact_toggle_without_confirmation(self):
        self.client.force_login(self.manager)
        r = self.client.post(f"/contacts/{self.contact.id}/cold-call/toggle/", **AJAX_HEADERS)
        self.assertEqual(r.status_code, 400)

    def test_contact_toggle_already_marked(self):
        self.client.force_login(self.manager)
        self.client.post(
            f"/contacts/{self.contact.id}/cold-call/toggle/",
            {"confirmed": "1"},
            **AJAX_HEADERS,
        )
        r = self.client.post(
            f"/contacts/{self.contact.id}/cold-call/toggle/",
            {"confirmed": "1"},
            **AJAX_HEADERS,
        )
        self.assertEqual(r.status_code, 200)
        self.assertIn("уже", r.json()["message"].lower())

    def test_contact_reset_requires_admin(self):
        self.client.force_login(self.manager)
        r = self.client.post(f"/contacts/{self.contact.id}/cold-call/reset/", **AJAX_HEADERS)
        self.assertEqual(r.status_code, 403)

    def test_contact_reset_admin_success(self):
        self.client.force_login(self.manager)
        self.client.post(
            f"/contacts/{self.contact.id}/cold-call/toggle/",
            {"confirmed": "1"},
            **AJAX_HEADERS,
        )
        self.client.force_login(self.admin)
        r = self.client.post(f"/contacts/{self.contact.id}/cold-call/reset/", **AJAX_HEADERS)
        self.assertEqual(r.status_code, 200)
        self.contact.refresh_from_db()
        self.assertFalse(self.contact.is_cold_call)


@override_settings(
    SECURE_SSL_REDIRECT=False,
    SECURE_HSTS_SECONDS=0,
    SESSION_COOKIE_SECURE=False,
)
class ColdCallContactPhoneViewTests(TestCase):
    """ContactPhone cold-call toggle/reset views."""

    def setUp(self):
        self.branch = Branch.objects.create(code="ccp", name="CCP Test Branch")
        self.admin = User.objects.create_superuser(username="cc_admin_cp", email="a@ccp.ru")
        self.manager = User.objects.create_user(
            username="cc_mgr_cp",
            email="m@ccp.ru",
            role=User.Role.MANAGER,
            branch=self.branch,
        )
        self.company = Company.objects.create(
            name="CP Test Co",
            responsible=self.manager,
            branch=self.branch,
        )
        self.contact = Contact.objects.create(
            company=self.company,
            first_name="Пётр",
            last_name="Петров",
        )
        self.contact_phone = ContactPhone.objects.create(
            contact=self.contact,
            value="+79001234502",
        )

    def test_contact_phone_toggle_ajax_success(self):
        self.client.force_login(self.manager)
        r = self.client.post(
            f"/contact-phones/{self.contact_phone.id}/cold-call/toggle/",
            {"confirmed": "1"},
            **AJAX_HEADERS,
        )
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["is_cold_call"])
        self.contact_phone.refresh_from_db()
        self.assertTrue(self.contact_phone.is_cold_call)

    def test_contact_phone_toggle_already_marked(self):
        self.client.force_login(self.manager)
        self.client.post(
            f"/contact-phones/{self.contact_phone.id}/cold-call/toggle/",
            {"confirmed": "1"},
            **AJAX_HEADERS,
        )
        r = self.client.post(
            f"/contact-phones/{self.contact_phone.id}/cold-call/toggle/",
            {"confirmed": "1"},
            **AJAX_HEADERS,
        )
        self.assertEqual(r.status_code, 200)
        self.assertIn("уже", r.json()["message"].lower())

    def test_contact_phone_reset_requires_admin(self):
        self.client.force_login(self.manager)
        r = self.client.post(
            f"/contact-phones/{self.contact_phone.id}/cold-call/reset/", **AJAX_HEADERS
        )
        self.assertEqual(r.status_code, 403)

    def test_contact_phone_reset_admin_success(self):
        self.client.force_login(self.manager)
        self.client.post(
            f"/contact-phones/{self.contact_phone.id}/cold-call/toggle/",
            {"confirmed": "1"},
            **AJAX_HEADERS,
        )
        self.client.force_login(self.admin)
        r = self.client.post(
            f"/contact-phones/{self.contact_phone.id}/cold-call/reset/", **AJAX_HEADERS
        )
        self.assertEqual(r.status_code, 200)
        self.contact_phone.refresh_from_db()
        self.assertFalse(self.contact_phone.is_cold_call)


@override_settings(
    SECURE_SSL_REDIRECT=False,
    SECURE_HSTS_SECONDS=0,
    SESSION_COOKIE_SECURE=False,
)
class ColdCallCompanyPhoneViewTests(TestCase):
    """CompanyPhone cold-call toggle/reset views."""

    def setUp(self):
        self.branch = Branch.objects.create(code="cmp", name="CMP Test Branch")
        self.admin = User.objects.create_superuser(username="cc_admin_mp", email="a@cmp.ru")
        self.manager = User.objects.create_user(
            username="cc_mgr_mp",
            email="m@cmp.ru",
            role=User.Role.MANAGER,
            branch=self.branch,
        )
        self.company = Company.objects.create(
            name="MP Test Co",
            responsible=self.manager,
            branch=self.branch,
        )
        self.company_phone = CompanyPhone.objects.create(
            company=self.company,
            value="+79001234503",
        )

    def test_company_phone_toggle_ajax_success(self):
        self.client.force_login(self.manager)
        r = self.client.post(
            f"/company-phones/{self.company_phone.id}/cold-call/toggle/",
            {"confirmed": "1"},
            **AJAX_HEADERS,
        )
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["is_cold_call"])
        self.company_phone.refresh_from_db()
        self.assertTrue(self.company_phone.is_cold_call)

    def test_company_phone_toggle_already_marked(self):
        self.client.force_login(self.manager)
        self.client.post(
            f"/company-phones/{self.company_phone.id}/cold-call/toggle/",
            {"confirmed": "1"},
            **AJAX_HEADERS,
        )
        r = self.client.post(
            f"/company-phones/{self.company_phone.id}/cold-call/toggle/",
            {"confirmed": "1"},
            **AJAX_HEADERS,
        )
        self.assertEqual(r.status_code, 200)
        self.assertIn("уже", r.json()["message"].lower())

    def test_company_phone_reset_requires_admin(self):
        self.client.force_login(self.manager)
        r = self.client.post(
            f"/company-phones/{self.company_phone.id}/cold-call/reset/", **AJAX_HEADERS
        )
        self.assertEqual(r.status_code, 403)

    def test_company_phone_reset_admin_success(self):
        self.client.force_login(self.manager)
        self.client.post(
            f"/company-phones/{self.company_phone.id}/cold-call/toggle/",
            {"confirmed": "1"},
            **AJAX_HEADERS,
        )
        self.client.force_login(self.admin)
        r = self.client.post(
            f"/company-phones/{self.company_phone.id}/cold-call/reset/", **AJAX_HEADERS
        )
        self.assertEqual(r.status_code, 200)
        self.company_phone.refresh_from_db()
        self.assertFalse(self.company_phone.is_cold_call)
