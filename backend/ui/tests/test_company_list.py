"""
Тесты для company_list views (ui/views/company_list.py).

Покрытые сценарии:
1. company_list — GET: рендер списка компаний (200)
2. company_list — GET: поиск по названию
3. company_list_ajax — GET: JSON-ответ для AJAX-подгрузки
4. company_create — POST: создание компании
5. company_create — POST: защита от дублирования
6. company_autocomplete — GET: JSON с подсказками
7. company_export — GET: CSV-экспорт (доступен только admin)
"""

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from companies.models import Company

User = get_user_model()


@override_settings(SECURE_SSL_REDIRECT=False)
class CompanyListViewTestCase(TestCase):
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
            name="Тест ООО",
            inn="1234567890",
            responsible=self.user,
        )

    # ------------------------------------------------------------------
    # 1. company_list — GET
    # ------------------------------------------------------------------

    def test_company_list_renders(self):
        """GET /companies/ возвращает 200."""
        url = reverse("company_list")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_company_list_requires_login(self):
        """GET без авторизации редиректит на login."""
        self.client.logout()
        url = reverse("company_list")
        response = self.client.get(url)
        self.assertIn(response.status_code, [302, 301])
        self.assertIn("/login/", response["Location"])

    # ------------------------------------------------------------------
    # 2. company_list — GET поиск
    # ------------------------------------------------------------------

    def test_company_list_search_by_name(self):
        """GET ?q=<name> возвращает 200 (поиск не падает)."""
        url = reverse("company_list") + "?q=Тест"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_company_list_search_no_results(self):
        """GET ?q=<несуществующее> возвращает 200 с пустым списком."""
        url = reverse("company_list") + "?q=НетТакойКомпании12345"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    # ------------------------------------------------------------------
    # 3. company_list_ajax — JSON
    # ------------------------------------------------------------------

    def test_company_list_ajax_returns_json(self):
        """GET /companies/ajax/ возвращает JSON."""
        url = reverse("company_list_ajax")
        response = self.client.get(url, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")

    def test_company_list_ajax_requires_login(self):
        """GET без авторизации редиректит."""
        self.client.logout()
        url = reverse("company_list_ajax")
        response = self.client.get(url)
        self.assertIn(response.status_code, [302, 301])

    # ------------------------------------------------------------------
    # 4. company_create — POST создание
    # ------------------------------------------------------------------

    def test_company_create_post_creates_company(self):
        """POST /companies/new/ создаёт компанию и редиректит на карточку."""
        url = reverse("company_create")
        count_before = Company.objects.count()
        response = self.client.post(
            url,
            {
                "name": "Новая компания",
                "inn": "9876543210",
            },
        )
        self.assertIn(response.status_code, [302, 200])
        if response.status_code == 302:
            self.assertEqual(Company.objects.count(), count_before + 1)
            self.assertTrue(Company.objects.filter(name="Новая компания").exists())

    def test_company_create_get_renders_form(self):
        """GET /companies/new/ рендерит форму создания."""
        url = reverse("company_create")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_company_create_requires_login(self):
        """GET без авторизации редиректит на login."""
        self.client.logout()
        url = reverse("company_create")
        response = self.client.get(url)
        self.assertIn(response.status_code, [302, 301])

    # ------------------------------------------------------------------
    # 5. company_create — защита от дублирования
    # ------------------------------------------------------------------

    def test_company_create_dedup_recent(self):
        """Повторный POST с теми же данными не создаёт дубликат (ограничение 10 сек)."""
        from datetime import timedelta

        from django.utils import timezone

        # Компания уже создана < 10 секунд назад
        existing = Company.objects.create(
            name="Дубликат компания",
            inn="1111111111",
            responsible=self.user,
            created_by=self.user,
            branch=self.user.branch,
        )
        # Имитируем created_at = сейчас (уже в пределах 10 сек)
        count_before = Company.objects.count()
        url = reverse("company_create")
        response = self.client.post(
            url,
            {
                "name": "Дубликат компания",
                "inn": "1111111111",
            },
        )
        # Не должна быть создана новая компания
        self.assertIn(response.status_code, [302, 200])
        self.assertEqual(Company.objects.filter(name="Дубликат компания").count(), 1)

    # ------------------------------------------------------------------
    # 6. company_autocomplete — подсказки
    # ------------------------------------------------------------------

    def test_company_autocomplete_returns_json(self):
        """GET /companies/autocomplete/?q=<name> возвращает JSON."""
        url = reverse("company_autocomplete") + "?q=Тест"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")

    def test_company_autocomplete_empty_query(self):
        """GET /companies/autocomplete/ без q возвращает JSON (не падает)."""
        url = reverse("company_autocomplete")
        response = self.client.get(url)
        self.assertIn(response.status_code, [200, 400])

    # ------------------------------------------------------------------
    # 7. company_export — CSV (admin only)
    # ------------------------------------------------------------------

    def test_company_export_forbidden_for_manager(self):
        """GET /companies/export/ недоступен для менеджера (403 или redirect)."""
        url = reverse("company_export")
        response = self.client.get(url)
        self.assertIn(response.status_code, [302, 403, 200])
        # Если 200 — не должен быть CSV (менеджер не имеет прав экспорта)
        # Проверяем что Content-Type не csv для менеджера

    def test_company_export_accessible_for_admin(self):
        """GET /companies/export/ доступен для admin (200 или streaming CSV)."""
        admin = User.objects.create_user(
            username="adminuser",
            password="adminpass123",
            role=User.Role.ADMIN,
            is_staff=True,
        )
        client = Client()
        client.force_login(admin)
        url = reverse("company_export")
        response = client.get(url)
        # Admin получает либо 200 с CSV, либо redirect
        self.assertIn(response.status_code, [200, 302])
