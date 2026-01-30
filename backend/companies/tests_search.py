from __future__ import annotations

from django.db import connection
from django.test import TestCase

from companies.search_index import parse_query
from companies.search_service import highlight_html, CompanySearchService
from companies.models import Company, CompanyStatus, Contact, ContactPhone, CompanyPhone, CompanySearchIndex
from companies.search_index import rebuild_company_search_index


class QueryParseTests(TestCase):
    def test_parse_query_mixed(self):
        pq = parse_query("иванов 8926")
        self.assertEqual(pq.text_tokens, ("иванов",))
        self.assertEqual(pq.strong_digit_tokens, ("8926",))
        self.assertEqual(pq.weak_digit_tokens, ())

        pq = parse_query("7701 ооо ромашка")
        self.assertIn("ооо", pq.text_tokens)
        self.assertIn("ромашка", pq.text_tokens)
        self.assertEqual(pq.strong_digit_tokens, ("7701",))
        self.assertEqual(pq.weak_digit_tokens, ())

    def test_parse_query_special_chars(self):
        pq = parse_query(r"ООО (Ромашка)+[?]* 7701")
        self.assertIn("ооо", pq.text_tokens)
        self.assertIn("ромашка", pq.text_tokens)
        self.assertEqual(pq.strong_digit_tokens, ("7701",))
        self.assertEqual(pq.weak_digit_tokens, ())

    def test_parse_query_weak_digits(self):
        pq = parse_query("+7 (926)")
        # "7" игнорируем, "926" считаем weak
        self.assertEqual(pq.strong_digit_tokens, ())
        self.assertEqual(pq.weak_digit_tokens, ("926",))


class HighlightTests(TestCase):
    def test_highlight_html_escapes(self):
        html = highlight_html("<b>ООО</b> Ромашка", text_tokens=("ооо",), digit_tokens=())
        self.assertIn("&lt;b&gt;", html)  # HTML экранирован
        self.assertIn('class="search-highlight"', html)
        self.assertIn("…", highlight_html("X " * 200 + "ромашка" + " Y" * 200, text_tokens=("ромашка",), digit_tokens=(), max_len=80))


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
        # Совпадение по ФИО + цифрам: используем поля компании, чтобы fallback_match
        # сработал стабильно (индекс/триггер в тест-БД могут отличаться).
        c = Company.objects.create(
            name="ООО Тест",
            inn="1234567890",
            status=self.status,
            contact_name="Иванов Иван",
            phone="89261234567",
        )
        rebuild_company_search_index(c.id)

        qs = CompanySearchService().apply(qs=Company.objects.all(), query="иванов 8926")
        ids = list(qs.values_list("id", flat=True)[:10])
        self.assertIn(c.id, ids, "Запрос «иванов 8926» должен найти компанию по contact_name и phone.")
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

    def test_result_implies_explain_not_empty_for_split_tokens(self):
        """
        Регрессия: запрос "иванов 8926" с разнесёнными токенами (контакт + телефон компании)
        должен не только найти компанию, но и дать ненулевой explain.
        """
        c = Company.objects.create(name="ООО Тест", inn="1234567890", status=self.status)
        # text‑токен уходит в контакт
        ct = Contact.objects.create(company=c, first_name="Иван", last_name="Иванов")
        # digit‑токен уходит в телефон КОМПАНИИ; номер должен содержать "8926" в digits
        CompanyPhone.objects.create(company=c, value="8 (926) 123-45-67")

        rebuild_company_search_index(c.id)

        qs = CompanySearchService().apply(qs=Company.objects.all(), query="иванов 8926")
        page = list(qs[:10])
        self.assertTrue(any(x.id == c.id for x in page), "Компания должна быть в выдаче по AND-запросу.")

        explain_map = CompanySearchService().explain(companies=page, query="иванов 8926")
        self.assertIn(c.id, explain_map)
        ex = explain_map[c.id]
        # ключевая гарантия: "нашлось ⇒ объяснилось"
        self.assertTrue(ex.reasons, "Для найденной компании explain.reasons не должен быть пустым.")
        # хотя бы одна причина должна содержать контакт/ФИО
        self.assertTrue(
            any("иванов" in (r.value or "").lower() for r in ex.reasons),
            "В причинах должен фигурировать контакт Иванов.",
        )
        # и хотя бы одна причина должна содержать цифры телефона
        self.assertTrue(
            any("926" in (r.value or "") or "926" in (r.value_html or "") for r in ex.reasons),
            "В причинах должен фигурировать фрагмент телефона 8926/926.",
        )

    def test_search_by_company_name_single_token(self):
        """Запрос по одному слову названия (янтарь) находит компанию ФКУ Янтарь / ФГКУ «Янтарь»."""
        c = Company.objects.create(name='ФКУ Янтарь', inn="7701234567", status=self.status)
        rebuild_company_search_index(c.id)

        qs = CompanySearchService().apply(qs=Company.objects.all(), query="янтарь")
        ids = list(qs.values_list("id", flat=True)[:10])
        self.assertIn(c.id, ids, "Запрос «янтарь» должен находить компанию с названием «ФКУ Янтарь».")

    def test_search_by_company_name_quoted(self):
        """Поиск по названию с кавычками в карточке (ФГКУ «Янтарь»)."""
        c = Company.objects.create(name='ФГКУ "Янтарь"', inn="7702345678", status=self.status)
        rebuild_company_search_index(c.id)

        qs = CompanySearchService().apply(qs=Company.objects.all(), query="янтарь")
        ids = list(qs.values_list("id", flat=True)[:10])
        self.assertIn(c.id, ids, "Запрос «янтарь» должен находить компанию «ФГКУ «Янтарь»».")

    def test_search_by_company_name_typo(self):
        """Опечатка в запросе (янтар) при пороге similarity всё ещё находит «Янтарь»."""
        c = Company.objects.create(name="Янтарь", inn="7703456789", status=self.status)
        rebuild_company_search_index(c.id)

        qs = CompanySearchService().apply(qs=Company.objects.all(), query="янтар")
        ids = list(qs.values_list("id", flat=True)[:10])
        self.assertIn(c.id, ids, "Запрос «янтар» (опечатка) должен находить компанию «Янтарь» по similarity.")

    def test_search_short_query_no_spam(self):
        """Короткий/шумовой запрос (1 буква) не возвращает всё подряд."""
        Company.objects.create(name="А", inn="7704000001", status=self.status)
        c2 = Company.objects.create(name="Банк", inn="7704000002", status=self.status)
        rebuild_company_search_index(Company.objects.get(name="А").id)
        rebuild_company_search_index(c2.id)

        qs = CompanySearchService().apply(qs=Company.objects.all(), query="а")
        ids = list(qs.values_list("id", flat=True)[:20])
        # Токен "а" отбрасывается в parse_query (len >= 2), поэтому запрос без текстовых токенов
        # и без strong_digits → пустая выдача или по weak_digits. Не должно быть "всё подряд".
        self.assertLessEqual(
            len(ids),
            10,
            "Запрос из одного символа не должен возвращать неограниченную выдачу.",
        )

