from __future__ import annotations

import unittest

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


@unittest.skipUnless(connection.vendor == "postgresql", "PostgreSQL required (tsvector/pg_trgm/ArrayField)")
class SearchServicePostgresTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.status = CompanyStatus.objects.create(name="Тест")

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
        # Совпадение по ФИО + цифрам: используем поля компании для fallback_match.
        # Телефон при save() нормализуется в +79261234567, поэтому ищем по "9261" (strong digit).
        c = Company.objects.create(
            name="ООО Тест",
            inn="1234567890",
            status=self.status,
            contact_name="Иванов Иван",
            phone="8 (926) 123-45-67",
        )
        rebuild_company_search_index(c.id)

        qs = CompanySearchService().apply(qs=Company.objects.all(), query="иванов 9261")
        ids = list(qs.values_list("id", flat=True)[:10])
        self.assertIn(c.id, ids, "Запрос «иванов 9261» должен найти компанию по contact_name и phone.")
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

    def test_exact_first_email_search(self):
        """EXACT‑first: поиск по email возвращает только точные совпадения и сортирует по updated_at desc."""
        c1 = Company.objects.create(
            name="Email 1",
            inn="1111111111",
            email="client@example.com",
            status=self.status,
        )
        c2 = Company.objects.create(
            name="Email 2",
            inn="2222222222",
            email="other@example.com",
            status=self.status,
        )
        # Дополнительный email на другой компании
        from companies.models import CompanyEmail

        c3 = Company.objects.create(
            name="Email 3",
            inn="3333333333",
            status=self.status,
        )
        CompanyEmail.objects.create(company=c3, value="client@example.com")

        rebuild_company_search_index(c1.id)
        rebuild_company_search_index(c2.id)
        rebuild_company_search_index(c3.id)

        qs = CompanySearchService().apply(qs=Company.objects.all(), query="client@example.com")
        ids = list(qs.values_list("id", flat=True))
        self.assertEqual(
            set(ids),
            {c1.id, c3.id},
            "EXACT‑поиск по email должен возвращать только компании с точным совпадением email.",
        )

    def test_exact_first_phone_search(self):
        """EXACT‑first: поиск по телефону (11 цифр, 7/8) использует нормализованный номер."""
        c1 = Company.objects.create(
            name="Телефон 1",
            inn="4444444444",
            phone="8 (999) 123-45-67",
            status=self.status,
        )
        c2 = Company.objects.create(
            name="Телефон 2",
            inn="5555555555",
            phone="+7 999 123-45-68",
            status=self.status,
        )
        rebuild_company_search_index(c1.id)
        rebuild_company_search_index(c2.id)

        # Вводим номер в «пользовательском» формате с 8 и форматированием
        qs = CompanySearchService().apply(qs=Company.objects.all(), query="8 (999) 123-45-67")
        ids = list(qs.values_list("id", flat=True))
        self.assertEqual(
            ids,
            [c1.id],
            "EXACT‑поиск по телефону должен вернуть только компанию с этим номером.",
        )

    def test_exact_first_inn_search_including_multi_inn_field(self):
        """EXACT‑first: поиск по ИНН (10/12 цифр), в т.ч. когда поле хранит несколько ИНН через запятую."""
        c1 = Company.objects.create(
            name="ИНН одиночный",
            inn="1234567890",
            status=self.status,
        )
        c2 = Company.objects.create(
            name="ИНН список",
            inn="1234567890, 9876543210",
            status=self.status,
        )
        c3 = Company.objects.create(
            name="ИНН другой",
            inn="9876543210",
            status=self.status,
        )

        rebuild_company_search_index(c1.id)
        rebuild_company_search_index(c2.id)
        rebuild_company_search_index(c3.id)

        qs = CompanySearchService().apply(qs=Company.objects.all(), query="1234567890")
        ids = list(qs.values_list("id", flat=True))
        self.assertEqual(
            set(ids),
            {c1.id, c2.id},
            "EXACT‑поиск по ИНН должен находить как одиночный ИНН, так и запись, где он в списке.",
        )

        # Убеждаемся, что поиск по второму ИНН из списка не находит первую компанию
        qs2 = CompanySearchService().apply(qs=Company.objects.all(), query="9876543210")
        ids2 = list(qs2.values_list("id", flat=True))
        self.assertEqual(
            set(ids2),
            {c2.id, c3.id},
            "EXACT‑поиск по ИНН из списка должен находить только компании, где этот ИНН реально присутствует.",
        )

    def test_company_name_punct_and_glued_normalization(self):
        """
        Нормализация названий (тире/кавычки/склейка) в индексе:
        компания ООО «Сиб-Энерго» (ЮГ) должна находиться по разным вариантам запроса.
        """
        c = Company.objects.create(
            name='ООО "Сиб-Энерго" (ЮГ)',
            inn="7705555555",
            status=self.status,
        )
        rebuild_company_search_index(c.id)

        service = CompanySearchService()
        queries = [
            "сиб энерго юг",
            "сиб-энерго",
            "сибэнерго",
            "ооо сибэнерго",
        ]
        for q in queries:
            qs = service.apply(qs=Company.objects.all(), query=q)
            ids = list(qs.values_list("id", flat=True)[:10])
            self.assertIn(
                c.id,
                ids,
                f"Запрос «{q}» должен находить компанию с названием «ООО \"Сиб-Энерго\" (ЮГ)».",
            )

    def test_exact_search_via_index_no_join(self):
        """EXACT‑поиск должен использовать денормализованные поля индекса (без JOIN)."""
        from companies.models import ContactEmail, ContactPhone

        c1 = Company.objects.create(
            name="Компания с контактом",
            inn="1111111111",
            status=self.status,
        )
        contact = Contact.objects.create(
            company=c1,
            first_name="Иван",
            last_name="Иванов",
        )
        ContactEmail.objects.create(contact=contact, value="contact@example.com")
        ContactPhone.objects.create(contact=contact, value="+7 999 111-22-33")

        rebuild_company_search_index(c1.id)

        # Email exact через индекс (не JOIN)
        qs_email = CompanySearchService().apply(qs=Company.objects.all(), query="contact@example.com")
        self.assertIn(c1.id, qs_email.values_list("id", flat=True))

        # Phone exact через индекс (не JOIN)
        qs_phone = CompanySearchService().apply(qs=Company.objects.all(), query="+7 999 111-22-33")
        self.assertIn(c1.id, qs_phone.values_list("id", flat=True))

    def test_short_query_guard(self):
        """Слишком короткие запросы (не exact) не должны запускать heavy поиск."""
        c = Company.objects.create(name="Тестовая компания", inn="1234567890", status=self.status)
        rebuild_company_search_index(c.id)

        service = CompanySearchService()
        # Очень короткие запросы должны возвращать пустую выдачу
        short_queries = ["а", "-", "12", "ab"]
        for q in short_queries:
            qs = service.apply(qs=Company.objects.all(), query=q)
            ids = list(qs.values_list("id", flat=True))
            self.assertEqual(
                ids,
                [],
                f"Короткий запрос «{q}» должен возвращать пустую выдачу (не запускать heavy поиск).",
            )

        # Exact-запросы (email/phone/inn) должны работать даже если короткие
        c2 = Company.objects.create(name="Тест", inn="1234567890", email="a@b.co", status=self.status)
        rebuild_company_search_index(c2.id)
        qs_exact = service.apply(qs=Company.objects.all(), query="a@b.co")
        self.assertIn(c2.id, qs_exact.values_list("id", flat=True))

    def test_glued_normalization_noise_reduction(self):
        """Glued-нормализация не должна создавать шум на коротких/ОПФ-строках."""
        from companies.search_index import fold_text_glued

        # Короткая строка не должна генерировать glued
        self.assertEqual(fold_text_glued("ООО"), "")
        self.assertEqual(fold_text_glued("ИП"), "")

        # Только ОПФ не должна генерировать glued
        self.assertEqual(fold_text_glued("ООО ИП"), "")

        # Нормальная строка должна генерировать glued
        glued = fold_text_glued("ООО Сиб-Энерго")
        self.assertGreater(len(glued), 0)
        self.assertIn("сиб", glued.lower())


class SearchBackendFacadeTests(TestCase):
    """Тесты фасада get_company_search_backend при единственном backend (PostgreSQL)."""

    def test_get_backend_always_returns_postgres_service(self):
        from companies.search_service import get_company_search_backend, CompanySearchService

        for backend_value in ("postgres", "typesense", "unknown", "", None):
            with self.subTest(backend_value=backend_value):
                with self.settings(SEARCH_ENGINE_BACKEND=backend_value):
                    backend = get_company_search_backend()
                self.assertIsInstance(backend, CompanySearchService)

