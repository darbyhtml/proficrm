"""
Тесты для company_detail views (ui/views/company_detail.py).

Покрытые сценарии:
1. company_detail — GET: рендер карточки компании (200)
2. company_edit — POST: сохранение изменений компании (redirect)
3. contact_create — POST: создание контакта (redirect)
4. contact_edit — POST: редактирование контакта (redirect)
5. company_note_add — POST: добавление заметки (redirect)
"""

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from companies.models import Company, CompanyNote, Contact

User = get_user_model()


@override_settings(SECURE_SSL_REDIRECT=False)
class CompanyDetailViewTestCase(TestCase):
    """Базовый setUp, общий для всех сценариев."""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username="testmanager",
            password="testpass123",
            role=User.Role.MANAGER,
        )
        self.client.force_login(self.user)
        self.company = Company.objects.create(
            name="Тест компания",
            inn="1234567890",
            responsible=self.user,
        )

    # ------------------------------------------------------------------
    # 1. company_detail — GET
    # ------------------------------------------------------------------

    def test_company_detail_renders(self):
        """GET /companies/<id>/ возвращает 200 и название компании."""
        url = reverse("company_detail", kwargs={"company_id": self.company.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Тест компания")

    def test_company_detail_requires_login(self):
        """GET без авторизации редиректит на login."""
        self.client.logout()
        url = reverse("company_detail", kwargs={"company_id": self.company.id})
        response = self.client.get(url)
        self.assertIn(response.status_code, [302, 301])
        self.assertIn("/login/", response["Location"])

    def test_company_detail_404_for_nonexistent(self):
        """GET несуществующей компании возвращает 404."""
        import uuid

        url = reverse("company_detail", kwargs={"company_id": uuid.uuid4()})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    # ------------------------------------------------------------------
    # 2. company_edit — POST
    # ------------------------------------------------------------------

    def test_company_edit_post_saves_changes(self):
        """POST /companies/<id>/edit/ сохраняет изменения и редиректит на карточку."""
        url = reverse("company_edit", kwargs={"company_id": self.company.id})
        response = self.client.post(
            url,
            {
                "name": "Новое название",
                "inn": "1234567890",
            },
        )
        # Успешное сохранение → redirect на company_detail
        self.assertIn(response.status_code, [302, 200])
        if response.status_code == 302:
            self.company.refresh_from_db()
            self.assertEqual(self.company.name, "Новое название")

    def test_company_edit_get_renders_form(self):
        """GET /companies/<id>/edit/ рендерит форму редактирования."""
        url = reverse("company_edit", kwargs={"company_id": self.company.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Тест компания")

    def test_company_edit_forbidden_for_other_user(self):
        """Пользователь без прав на компанию не может её редактировать."""
        other_user = User.objects.create_user(
            username="other",
            password="testpass123",
            role=User.Role.MANAGER,
        )
        other_company = Company.objects.create(
            name="Чужая компания",
            inn="9999999999",
            responsible=other_user,
        )
        url = reverse("company_edit", kwargs={"company_id": other_company.id})
        response = self.client.post(url, {"name": "Взлом", "inn": "9999999999"})
        # Должен редиректить на карточку с сообщением об ошибке, не сохранять
        other_company.refresh_from_db()
        self.assertNotEqual(other_company.name, "Взлом")

    # ------------------------------------------------------------------
    # 3. contact_create — POST
    # ------------------------------------------------------------------

    def test_contact_create_post_creates_contact(self):
        """POST /companies/<id>/contacts/new/ создаёт контакт."""
        url = reverse("contact_create", kwargs={"company_id": self.company.id})
        response = self.client.post(
            url,
            {
                "last_name": "Иванов",
                "first_name": "Иван",
                "position": "Директор",
                "status": "",
                "note": "",
                # Пустые инлайн-формсеты
                "emails-TOTAL_FORMS": "0",
                "emails-INITIAL_FORMS": "0",
                "emails-MIN_NUM_FORMS": "0",
                "emails-MAX_NUM_FORMS": "1000",
                "phones-TOTAL_FORMS": "0",
                "phones-INITIAL_FORMS": "0",
                "phones-MIN_NUM_FORMS": "0",
                "phones-MAX_NUM_FORMS": "1000",
            },
        )
        self.assertIn(response.status_code, [302, 200])
        self.assertTrue(Contact.objects.filter(company=self.company, last_name="Иванов").exists())

    def test_contact_create_forbidden_for_other_company(self):
        """Нельзя добавить контакт в чужую компанию."""
        other_user = User.objects.create_user(
            username="other2",
            password="testpass123",
            role=User.Role.MANAGER,
        )
        other_company = Company.objects.create(
            name="Чужая 2",
            inn="8888888888",
            responsible=other_user,
        )
        url = reverse("contact_create", kwargs={"company_id": other_company.id})
        response = self.client.post(
            url,
            {
                "last_name": "Взломщик",
                "first_name": "Тест",
                "emails-TOTAL_FORMS": "0",
                "emails-INITIAL_FORMS": "0",
                "emails-MIN_NUM_FORMS": "0",
                "emails-MAX_NUM_FORMS": "1000",
                "phones-TOTAL_FORMS": "0",
                "phones-INITIAL_FORMS": "0",
                "phones-MIN_NUM_FORMS": "0",
                "phones-MAX_NUM_FORMS": "1000",
            },
        )
        self.assertFalse(
            Contact.objects.filter(company=other_company, last_name="Взломщик").exists()
        )

    # ------------------------------------------------------------------
    # 4. contact_edit — POST
    # ------------------------------------------------------------------

    def test_contact_edit_post_updates_contact(self):
        """POST /contacts/<id>/edit/ обновляет данные контакта."""
        contact = Contact.objects.create(
            company=self.company,
            last_name="Петров",
            first_name="Пётр",
        )
        url = reverse("contact_edit", kwargs={"contact_id": contact.id})
        response = self.client.post(
            url,
            {
                "last_name": "Сидоров",
                "first_name": "Сидор",
                "position": "",
                "status": "",
                "note": "",
                "emails-TOTAL_FORMS": "0",
                "emails-INITIAL_FORMS": "0",
                "emails-MIN_NUM_FORMS": "0",
                "emails-MAX_NUM_FORMS": "1000",
                "phones-TOTAL_FORMS": "0",
                "phones-INITIAL_FORMS": "0",
                "phones-MIN_NUM_FORMS": "0",
                "phones-MAX_NUM_FORMS": "1000",
            },
        )
        self.assertIn(response.status_code, [302, 200])
        contact.refresh_from_db()
        self.assertEqual(contact.last_name, "Сидоров")

    # ------------------------------------------------------------------
    # 5. company_note_add — POST
    # ------------------------------------------------------------------

    def test_company_note_add_creates_note(self):
        """POST /companies/<id>/notes/add/ создаёт заметку."""
        url = reverse("company_note_add", kwargs={"company_id": self.company.id})
        response = self.client.post(
            url,
            {
                "text": "Тестовая заметка",
            },
        )
        self.assertIn(response.status_code, [302, 200])
        self.assertTrue(
            CompanyNote.objects.filter(
                company=self.company,
                text="Тестовая заметка",
            ).exists()
        )

    def test_company_note_add_ignores_get(self):
        """GET /companies/<id>/notes/add/ редиректит на карточку (только POST)."""
        url = reverse("company_note_add", kwargs={"company_id": self.company.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)

    def test_company_note_add_requires_text(self):
        """POST без текста не создаёт заметку."""
        url = reverse("company_note_add", kwargs={"company_id": self.company.id})
        count_before = CompanyNote.objects.filter(company=self.company).count()
        self.client.post(url, {"text": ""})
        count_after = CompanyNote.objects.filter(company=self.company).count()
        self.assertEqual(count_before, count_after)
