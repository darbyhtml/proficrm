"""
Тесты для companies/api.py:
ContactViewSet, CompanyNoteViewSet, базовая аутентификация CompanyViewSet.
Нормализация CompanyViewSet покрыта в tests.py (CompanyAPITestCase).
"""

from __future__ import annotations

from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import User
from companies.models import Company, CompanyNote, Contact


class ApiSetupMixin:
    """Общая настройка: admin + manager + две компании."""

    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_user(
            username="api_admin", password="pass", role=User.Role.ADMIN
        )
        self.manager = User.objects.create_user(
            username="api_manager", password="pass", role=User.Role.MANAGER
        )
        self.company = Company.objects.create(
            name="API Тест", inn="1234567890", responsible=self.manager
        )
        self.company2 = Company.objects.create(
            name="API Тест 2", inn="0987654321", responsible=self.manager
        )


def _results(data):
    """Возвращает список записей из ответа (с пагинацией или без)."""
    if isinstance(data, list):
        return data
    return data.get("results", [])


# ---------------------------------------------------------------------------
# CompanyViewSet — базовая аутентификация и CRUD
# ---------------------------------------------------------------------------


class CompanyViewSetAuthTest(ApiSetupMixin, TestCase):

    def test_unauthenticated_list_returns_401(self):
        r = self.client.get("/api/v1/companies/")
        self.assertIn(r.status_code, [401, 403])

    def test_authenticated_list_returns_200(self):
        self.client.force_authenticate(user=self.admin)
        r = self.client.get("/api/v1/companies/")
        self.assertEqual(r.status_code, status.HTTP_200_OK)

    def test_list_contains_company(self):
        self.client.force_authenticate(user=self.admin)
        r = self.client.get("/api/v1/companies/")
        self.assertEqual(r.status_code, 200)
        names = [c["name"] for c in _results(r.data)]
        self.assertIn("API Тест", names)

    def test_retrieve_company(self):
        self.client.force_authenticate(user=self.admin)
        r = self.client.get(f"/api/v1/companies/{self.company.id}/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["name"], "API Тест")

    def test_retrieve_nonexistent_404(self):
        self.client.force_authenticate(user=self.admin)
        import uuid

        r = self.client.get(f"/api/v1/companies/{uuid.uuid4()}/")
        self.assertEqual(r.status_code, 404)

    def test_create_company(self):
        self.client.force_authenticate(user=self.admin)
        r = self.client.post(
            "/api/v1/companies/",
            {
                "name": "Новая компания API",
                "inn": "1111111111",
            },
            format="json",
        )
        self.assertEqual(r.status_code, 201)
        self.assertTrue(Company.objects.filter(name="Новая компания API").exists())

    def test_create_company_invalid_inn(self):
        self.client.force_authenticate(user=self.admin)
        r = self.client.post(
            "/api/v1/companies/",
            {
                "name": "Без ИНН",
                "inn": "",
            },
            format="json",
        )
        # Кастомный exception handler скрывает детали поля в prod-режиме,
        # поэтому проверяем только статус 400.
        self.assertEqual(r.status_code, 400)

    def test_patch_company(self):
        self.client.force_authenticate(user=self.admin)
        r = self.client.patch(
            f"/api/v1/companies/{self.company.id}/",
            {
                "name": "Обновлённое название",
            },
            format="json",
        )
        self.assertEqual(r.status_code, 200)
        self.company.refresh_from_db()
        self.assertEqual(self.company.name, "Обновлённое название")

    def test_delete_company(self):
        self.client.force_authenticate(user=self.admin)
        c = Company.objects.create(name="На удаление", inn="5555555555")
        r = self.client.delete(f"/api/v1/companies/{c.id}/")
        self.assertEqual(r.status_code, 204)
        self.assertFalse(Company.objects.filter(id=c.id).exists())

    def test_filter_by_is_cold_call(self):
        self.client.force_authenticate(user=self.admin)
        self.company.is_cold_call = True
        self.company.save(update_fields=["is_cold_call"])
        r = self.client.get("/api/v1/companies/?is_cold_call=true")
        self.assertEqual(r.status_code, 200)
        ids = [str(c["id"]) for c in _results(r.data)]
        self.assertIn(str(self.company.id), ids)

    def test_phone_normalized_in_response(self):
        self.client.force_authenticate(user=self.admin)
        r = self.client.post(
            "/api/v1/companies/",
            {
                "name": "Телефон тест",
                "inn": "2222222222",
                "phone": "8 (999) 111-22-33",
            },
            format="json",
        )
        self.assertEqual(r.status_code, 201)
        self.assertTrue(r.data["phone"].startswith("+7"))


# ---------------------------------------------------------------------------
# ContactViewSet
# ---------------------------------------------------------------------------


class ContactViewSetTest(ApiSetupMixin, TestCase):

    def setUp(self):
        super().setUp()
        self.contact = Contact.objects.create(
            company=self.company,
            first_name="Иван",
            last_name="Иванов",
            position="Директор",
        )

    def test_unauthenticated_returns_401(self):
        r = self.client.get("/api/v1/contacts/")
        self.assertIn(r.status_code, [401, 403])

    def test_list_contacts(self):
        self.client.force_authenticate(user=self.admin)
        r = self.client.get("/api/v1/contacts/")
        self.assertEqual(r.status_code, 200)
        names = [c["last_name"] for c in _results(r.data)]
        self.assertIn("Иванов", names)

    def test_retrieve_contact(self):
        self.client.force_authenticate(user=self.admin)
        r = self.client.get(f"/api/v1/contacts/{self.contact.id}/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["first_name"], "Иван")

    def test_filter_by_company(self):
        self.client.force_authenticate(user=self.admin)
        r = self.client.get(f"/api/v1/contacts/?company={self.company.id}")
        self.assertEqual(r.status_code, 200)
        results = _results(r.data)
        self.assertTrue(all(str(c["company"]) == str(self.company.id) for c in results))

    def test_create_contact(self):
        self.client.force_authenticate(user=self.admin)
        r = self.client.post(
            "/api/v1/contacts/",
            {
                "company": str(self.company.id),
                "first_name": "Пётр",
                "last_name": "Петров",
            },
            format="json",
        )
        self.assertEqual(r.status_code, 201)
        self.assertTrue(Contact.objects.filter(last_name="Петров").exists())

    def test_patch_contact(self):
        self.client.force_authenticate(user=self.admin)
        r = self.client.patch(
            f"/api/v1/contacts/{self.contact.id}/",
            {
                "position": "Менеджер",
            },
            format="json",
        )
        self.assertEqual(r.status_code, 200)
        self.contact.refresh_from_db()
        self.assertEqual(self.contact.position, "Менеджер")

    def test_delete_contact(self):
        self.client.force_authenticate(user=self.admin)
        c = Contact.objects.create(
            first_name="Временный", last_name="Контакт", company=self.company
        )
        r = self.client.delete(f"/api/v1/contacts/{c.id}/")
        self.assertEqual(r.status_code, 204)
        self.assertFalse(Contact.objects.filter(id=c.id).exists())

    def test_search_by_last_name(self):
        self.client.force_authenticate(user=self.admin)
        r = self.client.get("/api/v1/contacts/?search=Иванов")
        self.assertEqual(r.status_code, 200)
        results = _results(r.data)
        self.assertTrue(any(c["last_name"] == "Иванов" for c in results))


# ---------------------------------------------------------------------------
# CompanyNoteViewSet
# ---------------------------------------------------------------------------


class CompanyNoteViewSetTest(ApiSetupMixin, TestCase):

    def setUp(self):
        super().setUp()
        self.note = CompanyNote.objects.create(
            company=self.company,
            author=self.admin,
            text="Тестовая заметка",
        )

    def test_unauthenticated_returns_401(self):
        r = self.client.get("/api/v1/company-notes/")
        self.assertIn(r.status_code, [401, 403])

    def test_list_notes(self):
        self.client.force_authenticate(user=self.admin)
        r = self.client.get("/api/v1/company-notes/")
        self.assertEqual(r.status_code, 200)

    def test_filter_by_company(self):
        self.client.force_authenticate(user=self.admin)
        r = self.client.get(f"/api/v1/company-notes/?company={self.company.id}")
        self.assertEqual(r.status_code, 200)
        results = _results(r.data)
        self.assertTrue(all(str(c["company"]) == str(self.company.id) for c in results))

    def test_create_note_sets_author_to_current_user(self):
        self.client.force_authenticate(user=self.manager)
        r = self.client.post(
            "/api/v1/company-notes/",
            {
                "company": str(self.company.id),
                "text": "Заметка менеджера",
            },
            format="json",
        )
        self.assertEqual(r.status_code, 201)
        note = CompanyNote.objects.get(id=r.data["id"])
        self.assertEqual(note.author, self.manager)

    def test_retrieve_note(self):
        self.client.force_authenticate(user=self.admin)
        r = self.client.get(f"/api/v1/company-notes/{self.note.id}/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["text"], "Тестовая заметка")

    def test_update_own_note(self):
        self.client.force_authenticate(user=self.admin)
        r = self.client.patch(
            f"/api/v1/company-notes/{self.note.id}/",
            {
                "text": "Обновлённая заметка",
            },
            format="json",
        )
        self.assertEqual(r.status_code, 200)
        self.note.refresh_from_db()
        self.assertEqual(self.note.text, "Обновлённая заметка")

    def test_delete_own_note(self):
        self.client.force_authenticate(user=self.admin)
        note = CompanyNote.objects.create(company=self.company, author=self.admin, text="Удаляемая")
        r = self.client.delete(f"/api/v1/company-notes/{note.id}/")
        self.assertEqual(r.status_code, 204)
        self.assertFalse(CompanyNote.objects.filter(id=note.id).exists())

    def test_ordering_by_created_at(self):
        self.client.force_authenticate(user=self.admin)
        r = self.client.get("/api/v1/company-notes/?ordering=created_at")
        self.assertEqual(r.status_code, 200)
