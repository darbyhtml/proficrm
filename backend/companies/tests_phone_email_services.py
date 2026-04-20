"""
Тесты для phase 2 сервисов:
- company_phones.validate_phone_strict / validate_phone_main
- company_phones.check_phone_duplicate
- company_phones.validate_phone_comment
- company_emails.validate_email_value
- company_emails.check_email_duplicate

Покрывают все правила валидации, которые раньше жили в трёх местах
``ui/views/company_detail.py``.
"""
from __future__ import annotations

from django.test import TestCase

from accounts.models import Branch, User
from companies.models import Company, CompanyEmail, CompanyPhone
from companies.services import (
    validate_phone_strict,
    validate_phone_main,
    check_phone_duplicate,
    validate_phone_comment,
    validate_email_value,
    check_email_duplicate,
)


class ValidatePhoneStrictTests(TestCase):
    """company_phones.validate_phone_strict — для доп. телефонов."""

    def test_empty_rejected(self):
        norm, err = validate_phone_strict("")
        self.assertIsNone(norm)
        self.assertEqual(err, "Телефон не может быть пустым.")

    def test_whitespace_only_rejected(self):
        norm, err = validate_phone_strict("   ")
        self.assertIsNone(norm)
        self.assertIn("пустым", err)

    def test_cyrillic_rejected(self):
        norm, err = validate_phone_strict("+7телефон905")
        self.assertIsNone(norm)
        self.assertIn("недопустимые", err)

    def test_too_many_latin_rejected(self):
        norm, err = validate_phone_strict("+7phonex905")
        self.assertIsNone(norm)
        self.assertIn("недопустимые", err)

    def test_null_byte_rejected(self):
        norm, err = validate_phone_strict("+7\x00905")
        self.assertIsNone(norm)
        self.assertIn("недопустимые", err)

    def test_valid_ru_format(self):
        norm, err = validate_phone_strict("+7 (905) 123-45-67")
        self.assertIsNone(err)
        self.assertEqual(norm, "+79051234567")

    def test_too_short_rejected(self):
        # +7 999 — слишком короткий после нормализации
        norm, err = validate_phone_strict("+79991")
        self.assertIsNone(norm)
        self.assertEqual(err, "Некорректный формат телефона.")

    def test_obvious_garbage_rejected(self):
        # После normalize_phone мусор вида "xxxxxxxxx" возвращается как original[:50]
        # и не матчит E.164-regex → Некорректный формат.
        norm, err = validate_phone_strict("not-a-phone-at-all-123")
        self.assertIsNone(norm)
        self.assertEqual(err, "Некорректный формат телефона.")


class ValidatePhoneMainTests(TestCase):
    """company_phones.validate_phone_main — для основного (разрешает пустое)."""

    def test_empty_allowed(self):
        norm, err = validate_phone_main("")
        self.assertEqual(norm, "")
        self.assertIsNone(err)

    def test_cyrillic_rejected(self):
        norm, err = validate_phone_main("+7абв905")
        self.assertIsNotNone(err)
        self.assertIn("недопустимые", err)

    def test_valid_digits(self):
        norm, err = validate_phone_main("+7 (905) 123-45-67")
        self.assertIsNone(err)
        self.assertTrue(norm)

    def test_fewer_than_10_digits_rejected(self):
        norm, err = validate_phone_main("+79")
        self.assertIsNotNone(err)
        self.assertIn("10 цифр", err)


class CheckPhoneDuplicateTests(TestCase):
    def setUp(self):
        self.branch = Branch.objects.create(code="ekb", name="EKB")
        self.company = Company.objects.create(name="ACME", branch=self.branch, phone="+79051234567")

    def test_no_duplicate_when_empty(self):
        self.assertIsNone(check_phone_duplicate(company=self.company, normalized=""))

    def test_main_phone_conflict(self):
        err = check_phone_duplicate(company=self.company, normalized="+79051234567")
        self.assertIn("основной", err)

    def test_additional_phone_conflict(self):
        CompanyPhone.objects.create(company=self.company, value="+79999999999", order=0)
        err = check_phone_duplicate(company=self.company, normalized="+79999999999")
        self.assertIn("дополнительных", err)

    def test_exclude_phone_id_self(self):
        p = CompanyPhone.objects.create(company=self.company, value="+79999999999", order=0)
        # При обновлении «сам себя» — не дубль
        err = check_phone_duplicate(
            company=self.company,
            normalized="+79999999999",
            exclude_phone_id=p.id,
        )
        self.assertIsNone(err)


class ValidatePhoneCommentTests(TestCase):
    def test_trims_and_caps_255(self):
        comment, err = validate_phone_comment("  x" + "y" * 500)
        self.assertIsNone(err)
        self.assertEqual(len(comment), 255)

    def test_null_byte_rejected(self):
        comment, err = validate_phone_comment("bad\x00stuff")
        self.assertEqual(comment, "")
        self.assertIn("недопустимые", err)

    def test_empty_ok(self):
        comment, err = validate_phone_comment("")
        self.assertEqual(comment, "")
        self.assertIsNone(err)


class ValidateEmailValueTests(TestCase):
    def test_empty_not_allowed_by_default(self):
        email, err = validate_email_value("")
        self.assertEqual(email, "")
        self.assertIn("пустым", err)

    def test_empty_allowed_when_flag_set(self):
        email, err = validate_email_value("", allow_empty=True)
        self.assertEqual(email, "")
        self.assertIsNone(err)

    def test_lowercased(self):
        email, err = validate_email_value("  John.Doe@Example.COM  ")
        self.assertIsNone(err)
        self.assertEqual(email, "john.doe@example.com")

    def test_invalid_rejected(self):
        email, err = validate_email_value("not-an-email")
        self.assertIn("Некорректный", err)


class CheckEmailDuplicateTests(TestCase):
    def setUp(self):
        self.branch = Branch.objects.create(code="ekb", name="EKB")
        self.company = Company.objects.create(name="ACME", branch=self.branch, email="main@acme.ru")

    def test_no_duplicate_when_empty(self):
        self.assertIsNone(check_email_duplicate(company=self.company, email=""))

    def test_main_email_conflict(self):
        err = check_email_duplicate(company=self.company, email="main@acme.ru")
        self.assertIn("основной", err)

    def test_main_email_conflict_case_insensitive(self):
        err = check_email_duplicate(company=self.company, email="MAIN@acme.ru")
        # Это именно CI: если вдруг кто-то сломал lowercase — тест красный.
        self.assertIn("основной", err)

    def test_additional_email_conflict(self):
        CompanyEmail.objects.create(company=self.company, value="extra@acme.ru", order=0)
        err = check_email_duplicate(company=self.company, email="extra@acme.ru")
        self.assertIn("дополнительных", err)

    def test_check_main_disabled(self):
        # При обновлении основного email — мы проверяем только доп.
        err = check_email_duplicate(
            company=self.company,
            email="main@acme.ru",
            check_main=False,
        )
        self.assertIsNone(err)

    def test_exclude_self(self):
        em = CompanyEmail.objects.create(company=self.company, value="extra@acme.ru", order=0)
        err = check_email_duplicate(
            company=self.company,
            email="extra@acme.ru",
            exclude_email_id=em.id,
        )
        self.assertIsNone(err)
