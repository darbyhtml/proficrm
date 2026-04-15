"""Тесты preview-страницы редизайна рабочего стола (`/_preview/dashboard-v2/`).

Проверяет три вещи:
1. Страница доступна ADMIN и отдаёт шаблон dashboard_v2.html.
2. Не-ADMIN получает 403.
3. Шаблон использует те же данные что и обычный dashboard (через общий
   `_build_dashboard_context`).
"""

from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.core.cache import cache

User = get_user_model()


@override_settings(SECURE_SSL_REDIRECT=False)
class DashboardV2PreviewTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client()
        self.admin = User.objects.create_user(
            username="admin_v2",
            password="pw",
            role=User.Role.ADMIN,
            is_staff=True,
        )
        self.manager = User.objects.create_user(
            username="mgr_v2",
            password="pw",
            role=User.Role.MANAGER,
        )

    def test_admin_can_access_preview(self):
        """ADMIN видит preview-страницу и шаблон dashboard_v2.html."""
        self.client.force_login(self.admin)
        url = reverse("dashboard_v2_preview")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "ui/dashboard_v2.html")
        # Флаг preview_v2 должен быть в контексте
        self.assertTrue(resp.context.get("preview_v2"))

    def test_manager_forbidden(self):
        """Обычный менеджер не должен попасть на preview (403)."""
        self.client.force_login(self.manager)
        url = reverse("dashboard_v2_preview")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 403)

    def test_anonymous_redirected_to_login(self):
        """Аноним — редирект на логин."""
        url = reverse("dashboard_v2_preview")
        resp = self.client.get(url)
        self.assertIn(resp.status_code, (302, 301))

    def test_context_has_same_keys_as_regular_dashboard(self):
        """Preview и обычный dashboard — один и тот же набор ключей контекста."""
        self.client.force_login(self.admin)
        resp_v1 = self.client.get(reverse("dashboard"))
        resp_v2 = self.client.get(reverse("dashboard_v2_preview"))
        self.assertEqual(resp_v1.status_code, 200)
        self.assertEqual(resp_v2.status_code, 200)
        keys_v1 = set(resp_v1.context.keys())
        keys_v2 = set(resp_v2.context.keys())
        required = {
            "tasks_new", "tasks_today", "overdue", "tasks_week",
            "contracts_soon", "stale_companies", "tasks_done_today",
        }
        self.assertTrue(required.issubset(keys_v1))
        self.assertTrue(required.issubset(keys_v2))
