"""UX-2 — Global search Ctrl+K endpoint + integration tests."""

from __future__ import annotations

from django.test import Client, TestCase

from companies.models import Company, Contact
from core.test_utils import make_disposable_user


class GlobalSearchEndpointTest(TestCase):
    """GET /api/search/global/?q= returns grouped results."""

    @classmethod
    def setUpTestData(cls):
        cls.admin = make_disposable_user(
            role="admin",
            prefix="ux2_search",
            is_staff=True,
            is_superuser=True,
        )
        cls.co = Company.objects.create(
            name="UX2 Ivanov Timber LLC",
            inn="7712345678",
        )
        cls.contact = Contact.objects.create(
            company=cls.co,
            first_name="Ivan",
            last_name="Petrov",
            position="CEO",
        )

    def _get_login(self) -> Client:
        c = Client()
        c.force_login(self.admin)
        return c

    def test_short_query_returns_hint(self):
        c = self._get_login()
        r = c.get("/api/search/global/?q=a")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data.get("companies"), [])
        self.assertIn("hint", data)

    def test_response_shape_has_all_categories(self):
        c = self._get_login()
        r = c.get("/api/search/global/?q=Ivanov")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("companies", data)
        self.assertIn("contacts", data)
        self.assertIn("tasks", data)
        self.assertIn("query", data)

    def test_company_search_finds_by_name(self):
        c = self._get_login()
        r = c.get("/api/search/global/?q=Ivanov")
        data = r.json()
        names = [row["name"] for row in data["companies"]]
        self.assertTrue(
            any("Ivanov" in n for n in names),
            f"Expected company с Ivanov в name, got {names}",
        )

    def test_contact_search_finds_by_name(self):
        c = self._get_login()
        r = c.get("/api/search/global/?q=Petrov")
        data = r.json()
        names = [row["name"] for row in data["contacts"]]
        self.assertTrue(
            any("Petrov" in n for n in names),
            f"Expected contact с Petrov, got {names}",
        )

    def test_company_result_has_url(self):
        c = self._get_login()
        r = c.get("/api/search/global/?q=Ivanov")
        data = r.json()
        for item in data["companies"]:
            self.assertTrue(item["url"].startswith("/companies/"))

    def test_require_login(self):
        c = Client()
        r = c.get("/api/search/global/?q=test")
        # Not authenticated → redirect к login
        self.assertIn(r.status_code, (302, 403))

    def test_require_get(self):
        c = self._get_login()
        r = c.post("/api/search/global/?q=test")
        self.assertEqual(r.status_code, 405)


class GlobalSearchIntegrationTest(TestCase):
    """Base.html includes JS + CSS everywhere для Ctrl+K availability."""

    def setUp(self):
        self.admin = make_disposable_user(
            role="admin",
            prefix="ux2_integ",
            is_staff=True,
            is_superuser=True,
        )

    def test_search_js_included_in_base(self):
        c = Client()
        c.force_login(self.admin)
        r = c.get("/")
        self.assertIn("global_search.js", r.content.decode())

    def test_search_css_included_in_base(self):
        c = Client()
        c.force_login(self.admin)
        r = c.get("/")
        self.assertIn("global-search-modal", r.content.decode())
