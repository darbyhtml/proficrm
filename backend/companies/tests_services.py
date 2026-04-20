"""
Тесты для companies/services.py:
- get_contract_alert()
- get_worktime_status()
- CompanyService.transfer()
- ColdCallService
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.utils import timezone

from accounts.models import Branch, User
from companies.models import (
    Company,
    CompanyHistoryEvent,
    CompanyPhone,
    Contact,
    ContactPhone,
    ContractType,
)
from companies.services import (
    ColdCallService,
    CompanyService,
    get_contract_alert,
    get_worktime_status,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _branch(code: str) -> Branch:
    return Branch.objects.create(code=code, name=code.upper())


def _user(username: str, role=User.Role.MANAGER, branch=None) -> User:
    return User.objects.create_user(username=username, password="x", role=role, branch=branch)


def _company(responsible=None, branch=None, **kwargs) -> Company:
    return Company.objects.create(
        name=kwargs.pop("name", "Test Co"),
        responsible=responsible,
        branch=branch,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# get_contract_alert
# ---------------------------------------------------------------------------


class GetContractAlertTest(TestCase):
    def setUp(self):
        self.company = _company(name="AlertCo")
        self.contract_type = ContractType.objects.create(
            name="Стандарт", warning_days=30, danger_days=7
        )

    def test_no_contract_until(self):
        """Без даты договора — нет предупреждений."""
        alert, days = get_contract_alert(self.company)
        self.assertEqual(alert, "")
        self.assertIsNone(days)

    def test_danger_with_contract_type(self):
        """Осталось 3 дня — danger (меньше danger_days=7)."""
        today = timezone.localdate(timezone.now())
        self.company.contract_until = today + timedelta(days=3)
        self.company.contract_type = self.contract_type
        alert, days = get_contract_alert(self.company)
        self.assertEqual(alert, "danger")
        self.assertEqual(days, 3)

    def test_warn_with_contract_type(self):
        """Осталось 15 дней — warn (> danger_days=7, <= warning_days=30)."""
        today = timezone.localdate(timezone.now())
        self.company.contract_until = today + timedelta(days=15)
        self.company.contract_type = self.contract_type
        alert, days = get_contract_alert(self.company)
        self.assertEqual(alert, "warn")
        self.assertEqual(days, 15)

    def test_ok_with_contract_type(self):
        """Осталось 60 дней — нет предупреждений."""
        today = timezone.localdate(timezone.now())
        self.company.contract_until = today + timedelta(days=60)
        self.company.contract_type = self.contract_type
        alert, days = get_contract_alert(self.company)
        self.assertEqual(alert, "")
        self.assertEqual(days, 60)

    def test_danger_fallback_no_contract_type(self):
        """Без contract_type, 5 дней — danger (< 14)."""
        today = timezone.localdate(timezone.now())
        self.company.contract_until = today + timedelta(days=5)
        alert, days = get_contract_alert(self.company)
        self.assertEqual(alert, "danger")

    def test_warn_fallback_no_contract_type(self):
        """Без contract_type, 20 дней — warn (>= 14, <= 30)."""
        today = timezone.localdate(timezone.now())
        self.company.contract_until = today + timedelta(days=20)
        alert, days = get_contract_alert(self.company)
        self.assertEqual(alert, "warn")


# ---------------------------------------------------------------------------
# get_worktime_status
# ---------------------------------------------------------------------------


class GetWorktimeStatusTest(TestCase):
    def test_no_schedule(self):
        """Без расписания: has=False, status=None."""
        company = _company(name="NoScheduleCo")
        result = get_worktime_status(company)
        self.assertFalse(result["has"])
        self.assertIsNone(result["status"])
        self.assertEqual(result["label"], "")

    def test_with_schedule_ok(self):
        """С расписанием и статусом 'ok' → label='Рабочее время'."""
        company = _company(name="ScheduleCo")
        company.work_schedule = "09:00-18:00"

        with patch("companies.services.get_worktime_status") as mock_svc:
            mock_svc.return_value = {"has": True, "status": "ok", "label": "Рабочее время"}
            result = mock_svc(company)

        self.assertTrue(result["has"])
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["label"], "Рабочее время")

    def test_exception_returns_unknown(self):
        """Если что-то бросает исключение — status='unknown'."""
        company = _company(name="ExcCo")
        company.work_schedule = "09:00-18:00"
        company.work_timezone = "Invalid/Timezone"

        result = get_worktime_status(company)
        self.assertEqual(result["status"], "unknown")


# ---------------------------------------------------------------------------
# CompanyService.transfer
# ---------------------------------------------------------------------------


class CompanyServiceTransferTest(TestCase):
    def setUp(self):
        self.branch1 = _branch("b1")
        self.branch2 = _branch("b2")
        self.manager1 = _user("mgr1", User.Role.MANAGER, self.branch1)
        self.manager2 = _user("mgr2", User.Role.MANAGER, self.branch2)
        self.admin = _user("adm", User.Role.ADMIN, self.branch1)
        self.company = _company(responsible=self.manager1, branch=self.branch1, name="TransferCo")

    def test_transfer_updates_responsible_and_branch(self):
        """После transfer() company.responsible и company.branch обновлены."""
        CompanyService.transfer(
            company=self.company,
            user=self.admin,
            new_responsible=self.manager2,
        )
        self.company.refresh_from_db()
        self.assertEqual(self.company.responsible_id, self.manager2.id)
        self.assertEqual(self.company.branch_id, self.branch2.id)

    def test_transfer_creates_history_event(self):
        """После transfer() создаётся CompanyHistoryEvent типа ASSIGNED."""
        CompanyService.transfer(
            company=self.company,
            user=self.admin,
            new_responsible=self.manager2,
        )
        event = CompanyHistoryEvent.objects.filter(
            company=self.company,
            event_type=CompanyHistoryEvent.EventType.ASSIGNED,
        ).first()
        self.assertIsNotNone(event)
        self.assertEqual(event.to_user_id, self.manager2.id)
        self.assertEqual(event.from_user_id, self.manager1.id)

    def test_transfer_raises_permission_denied_for_non_admin_manager(self):
        """Менеджер-не-ответственный не может передавать чужую компанию."""
        other_manager = _user("other", User.Role.MANAGER, self.branch1)
        with self.assertRaises(PermissionDenied):
            CompanyService.transfer(
                company=self.company,
                user=other_manager,
                new_responsible=self.manager2,
            )

    def test_transfer_raises_validation_error_for_wrong_role(self):
        """Нельзя назначить ответственным пользователя с ролью ADMIN."""
        with self.assertRaises(ValidationError):
            CompanyService.transfer(
                company=self.company,
                user=self.admin,
                new_responsible=self.admin,
            )

    def test_transfer_returns_success_dict(self):
        """transfer() возвращает dict с success=True и правильными ID."""
        result = CompanyService.transfer(
            company=self.company,
            user=self.admin,
            new_responsible=self.manager2,
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["new_responsible_id"], str(self.manager2.id))
        self.assertEqual(result["old_responsible_id"], str(self.manager1.id))


# ---------------------------------------------------------------------------
# ColdCallService tests
# ---------------------------------------------------------------------------


class ColdCallServiceCompanyTest(TestCase):
    def setUp(self):
        self.manager = User.objects.create_user(
            username="cc_mgr", password="pass", role=User.Role.MANAGER
        )
        self.company = Company.objects.create(
            name="Тест ХЗ", responsible=self.manager, phone="+79001234567"
        )

    def test_mark_company_sets_flag(self):
        result = ColdCallService.mark_company(company=self.company, user=self.manager)
        self.assertTrue(result["changed"])
        self.company.refresh_from_db()
        self.assertTrue(self.company.primary_contact_is_cold_call)
        self.assertIsNotNone(self.company.primary_cold_marked_at)
        self.assertEqual(self.company.primary_cold_marked_by, self.manager)

    def test_mark_company_idempotent(self):
        ColdCallService.mark_company(company=self.company, user=self.manager)
        self.company.refresh_from_db()
        result2 = ColdCallService.mark_company(company=self.company, user=self.manager)
        self.assertFalse(result2["changed"])
        self.assertTrue(result2["already_set"])

    def test_mark_company_no_phone(self):
        self.company.phone = ""
        self.company.save(update_fields=["phone"])
        result = ColdCallService.mark_company(company=self.company, user=self.manager)
        self.assertFalse(result["changed"])
        self.assertTrue(result.get("no_phone"))

    def test_reset_company_clears_flag(self):
        ColdCallService.mark_company(company=self.company, user=self.manager)
        self.company.refresh_from_db()
        result = ColdCallService.reset_company(company=self.company, user=self.manager)
        self.assertTrue(result["changed"])
        self.company.refresh_from_db()
        self.assertFalse(self.company.primary_contact_is_cold_call)
        self.assertIsNone(self.company.primary_cold_marked_at)
        self.assertIsNone(self.company.primary_cold_marked_by)

    def test_reset_company_idempotent(self):
        result = ColdCallService.reset_company(company=self.company, user=self.manager)
        self.assertFalse(result["changed"])
        self.assertTrue(result.get("already_reset"))


class ColdCallServiceContactTest(TestCase):
    def setUp(self):
        self.manager = User.objects.create_user(
            username="cc_mgr2", password="pass", role=User.Role.MANAGER
        )
        self.company = Company.objects.create(name="Тест ХЗ контакт", responsible=self.manager)
        self.contact = Contact.objects.create(
            company=self.company, first_name="Иван", last_name="Петров"
        )

    def test_mark_contact_sets_flag(self):
        result = ColdCallService.mark_contact(contact=self.contact, user=self.manager)
        self.assertTrue(result["changed"])
        self.contact.refresh_from_db()
        self.assertTrue(self.contact.is_cold_call)
        self.assertIsNotNone(self.contact.cold_marked_at)
        self.assertEqual(self.contact.cold_marked_by, self.manager)

    def test_mark_contact_idempotent(self):
        ColdCallService.mark_contact(contact=self.contact, user=self.manager)
        self.contact.refresh_from_db()
        result2 = ColdCallService.mark_contact(contact=self.contact, user=self.manager)
        self.assertFalse(result2["changed"])
        self.assertTrue(result2["already_set"])

    def test_reset_contact_clears_flag(self):
        ColdCallService.mark_contact(contact=self.contact, user=self.manager)
        self.contact.refresh_from_db()
        result = ColdCallService.reset_contact(contact=self.contact, user=self.manager)
        self.assertTrue(result["changed"])
        self.contact.refresh_from_db()
        self.assertFalse(self.contact.is_cold_call)
        self.assertIsNone(self.contact.cold_marked_at)

    def test_reset_contact_idempotent(self):
        result = ColdCallService.reset_contact(contact=self.contact, user=self.manager)
        self.assertFalse(result["changed"])
        self.assertTrue(result.get("already_reset"))


class ColdCallServiceContactPhoneTest(TestCase):
    def setUp(self):
        self.manager = User.objects.create_user(
            username="cc_mgr3", password="pass", role=User.Role.MANAGER
        )
        self.company = Company.objects.create(name="Тест ХЗ тел", responsible=self.manager)
        self.contact = Contact.objects.create(company=self.company, first_name="Анна")
        self.phone = ContactPhone.objects.create(contact=self.contact, value="+79009876543")

    def test_mark_contact_phone_sets_flag(self):
        result = ColdCallService.mark_contact_phone(contact_phone=self.phone, user=self.manager)
        self.assertTrue(result["changed"])
        self.phone.refresh_from_db()
        self.assertTrue(self.phone.is_cold_call)
        self.assertIsNotNone(self.phone.cold_marked_at)
        self.assertEqual(self.phone.cold_marked_by, self.manager)

    def test_mark_contact_phone_idempotent(self):
        ColdCallService.mark_contact_phone(contact_phone=self.phone, user=self.manager)
        self.phone.refresh_from_db()
        result2 = ColdCallService.mark_contact_phone(contact_phone=self.phone, user=self.manager)
        self.assertFalse(result2["changed"])
        self.assertTrue(result2["already_set"])

    def test_reset_contact_phone_clears_flag(self):
        ColdCallService.mark_contact_phone(contact_phone=self.phone, user=self.manager)
        self.phone.refresh_from_db()
        result = ColdCallService.reset_contact_phone(contact_phone=self.phone, user=self.manager)
        self.assertTrue(result["changed"])
        self.phone.refresh_from_db()
        self.assertFalse(self.phone.is_cold_call)
        self.assertIsNone(self.phone.cold_marked_at)

    def test_reset_contact_phone_idempotent(self):
        result = ColdCallService.reset_contact_phone(contact_phone=self.phone, user=self.manager)
        self.assertFalse(result["changed"])
        self.assertTrue(result.get("already_reset"))


class ColdCallServiceCompanyPhoneTest(TestCase):
    def setUp(self):
        self.manager = User.objects.create_user(
            username="cc_mgr4", password="pass", role=User.Role.MANAGER
        )
        self.company = Company.objects.create(name="Тест ХЗ тел компании", responsible=self.manager)
        self.phone = CompanyPhone.objects.create(company=self.company, value="+79001112233")

    def test_mark_company_phone_sets_flag(self):
        result = ColdCallService.mark_company_phone(company_phone=self.phone, user=self.manager)
        self.assertTrue(result["changed"])
        self.phone.refresh_from_db()
        self.assertTrue(self.phone.is_cold_call)
        self.assertIsNotNone(self.phone.cold_marked_at)
        self.assertEqual(self.phone.cold_marked_by, self.manager)

    def test_mark_company_phone_idempotent(self):
        ColdCallService.mark_company_phone(company_phone=self.phone, user=self.manager)
        self.phone.refresh_from_db()
        result2 = ColdCallService.mark_company_phone(company_phone=self.phone, user=self.manager)
        self.assertFalse(result2["changed"])
        self.assertTrue(result2["already_set"])

    def test_reset_company_phone_clears_flag(self):
        ColdCallService.mark_company_phone(company_phone=self.phone, user=self.manager)
        self.phone.refresh_from_db()
        result = ColdCallService.reset_company_phone(company_phone=self.phone, user=self.manager)
        self.assertTrue(result["changed"])
        self.phone.refresh_from_db()
        self.assertFalse(self.phone.is_cold_call)
        self.assertIsNone(self.phone.cold_marked_at)

    def test_reset_company_phone_idempotent(self):
        result = ColdCallService.reset_company_phone(company_phone=self.phone, user=self.manager)
        self.assertFalse(result["changed"])
        self.assertTrue(result.get("already_reset"))
