from __future__ import annotations

from django.db import connection
from django.test import TestCase

from companies.search_index import parse_query
from companies.search_service import highlight_html, CompanySearchService
from companies.models import Company, CompanyStatus, Contact, ContactPhone, CompanySearchIndex
from companies.search_index import rebuild_company_search_index


class QueryParseTests(TestCase):
    def test_parse_query_mixed(self):
        pq = parse_query("иванов 8926")
        self.assertEqual(pq.text_tokens, ("иванов",))
        self.assertEqual(pq.digit_tokens, ("8926",))

        pq = parse_query("7701 ооо ромашка")
        self.assertIn("ооо", pq.text_tokens)
        self.assertIn("ромашка", pq.text_tokens)
        self.assertEqual(pq.digit_tokens, ("7701",))

    def test_parse_query_special_chars(self):
        pq = parse_query(r"ООО (Ромашка)+[?]* 7701")
        self.assertIn("ооо", pq.text_tokens)
        self.assertIn("ромашка", pq.text_tokens)
        self.assertEqual(pq.digit_tokens, ("7701",))


class HighlightTests(TestCase):
    def test_highlight_html_escapes(self):
        html = highlight_html("<b>ООО</b> Ромашка", text_tokens=("ооо",), digit_tokens=())
        self.assertIn("&lt;b&gt;", html)  # HTML экранирован
        self.assertIn('class="search-highlight"', html)


class SearchServicePostgresTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.status = CompanyStatus.objects.create(name="Тест")

    def setUp(self):
        if connection.vendor != "postgresql":
            self.skipTest("Поиск v2 требует PostgreSQL (tsvector/pg_trgm).")

    def test_search_by_inn_has_priority(self):
        c1 = Company.objects.create(name="ООО Ромашка", inn="7701000000", kpp="770101001", status=self.status)
        c2 = Company.objects.create(name="Ромашка", inn="6600000000", status=self.status)

        rebuild_company_search_index(c1.id)
        rebuild_company_search_index(c2.id)

        qs = CompanySearchService().apply(qs=Company.objects.all(), query="7701 ромашка")
        ids = list(qs.values_list("id", flat=True)[:10])
        self.assertEqual(ids[0], c1.id)
        self.assertNotIn(c2.id, ids)  # AND: 7701 обязателен

    def test_search_mixed_contact_phone(self):
        c = Company.objects.create(name="ООО Тест", inn="1234567890", status=self.status)
        ct = Contact.objects.create(company=c, first_name="Иван", last_name="Иванов")
        ContactPhone.objects.create(contact=ct, value="+79261234567")

        rebuild_company_search_index(c.id)

        qs = CompanySearchService().apply(qs=Company.objects.all(), query="иванов 8926")
        ids = list(qs.values_list("id", flat=True)[:10])
        self.assertEqual(ids[0], c.id)

    def test_explain_returns_reasons(self):
        c = Company.objects.create(name="ООО Ромашка", inn="7701000000", address="г. Москва", status=self.status)
        rebuild_company_search_index(c.id)
        # обеспечим наличие индекса (на всякий)
        self.assertTrue(CompanySearchIndex.objects.filter(company=c).exists())

        page = list(Company.objects.filter(id=c.id))
        explain = CompanySearchService().explain(companies=page, query="7701 ромашка")
        ex = explain[c.id]
        self.assertTrue(ex.reasons)
        self.assertIn("search-highlight", ex.name_html)

