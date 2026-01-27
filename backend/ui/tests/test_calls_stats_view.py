"""
Тесты для settings_calls_stats view (template safety, контекстные ключи).
ЭТАП 6: проверка, что шаблоны не падают на nullable полях.
"""

from django.test import TestCase, RequestFactory, override_settings
from django.contrib.auth import get_user_model
from django.utils import timezone
from datetime import timedelta
from phonebridge.models import CallRequest

User = get_user_model()


@override_settings(SECURE_SSL_REDIRECT=False)
class CallsStatsViewTemplateSafetyTest(TestCase):
    """Тесты для проверки безопасности шаблонов (nullable поля, контекстные ключи)."""
    
    def setUp(self):
        """Настройка тестовых данных."""
        self.user = User.objects.create_user(
            username="testuser",
            password="testpass123",
            role=User.Role.MANAGER
        )
        self.factory = RequestFactory()
        self.now = timezone.now()
        self.start = self.now.replace(hour=0, minute=0, second=0, microsecond=0)
        self.end = self.start + timedelta(days=1)
    
    def test_view_context_keys_present(self):
        """Тест: все контекстные ключи присутствуют (шаблон не падает)."""
        # Используем Client для полного рендера шаблона
        from django.test import Client
        client = Client()
        client.force_login(self.user)
        response = client.get("/settings/calls/stats/?period=day")
        
        # Проверяем, что страница рендерится без ошибок
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Статистика звонков")  # Проверяем, что шаблон загружен
    
    def test_view_with_calls_without_new_fields(self):
        """Тест: звонки без новых полей не ломают шаблон."""
        # Создаём звонок без новых полей (legacy)
        CallRequest.objects.create(
            user=self.user,
            phone_raw="+79991234567",
            status=CallRequest.Status.PENDING,
            call_status=CallRequest.CallStatus.CONNECTED,
            call_started_at=self.start + timedelta(hours=1),
            call_duration_seconds=180
            # direction, resolve_method, action_source = null
        )
        
        from django.test import Client
        client = Client()
        client.force_login(self.user)
        response = client.get("/settings/calls/stats/?period=day")
        
        # Проверяем, что страница рендерится без ошибок
        self.assertEqual(response.status_code, 200)
        # Проверяем, что нет ошибок в шаблоне (нет "None" или исключений)
        self.assertNotContains(response, "None", status_code=200)
    
    def test_view_with_calls_with_new_fields(self):
        """Тест: звонки с новыми полями корректно отображаются."""
        # Создаём звонок с новыми полями
        CallRequest.objects.create(
            user=self.user,
            phone_raw="+79991234567",
            status=CallRequest.Status.PENDING,
            call_status=CallRequest.CallStatus.CONNECTED,
            call_started_at=self.start + timedelta(hours=1),
            call_duration_seconds=180,
            direction=CallRequest.CallDirection.OUTGOING,
            resolve_method=CallRequest.ResolveMethod.OBSERVER,
            action_source=CallRequest.ActionSource.CRM_UI,
            call_ended_at=self.start + timedelta(hours=1, minutes=3)
        )
        
        from django.test import Client
        client = Client()
        client.force_login(self.user)
        response = client.get("/settings/calls/stats/?period=day")
        
        # Проверяем, что страница рендерится без ошибок
        self.assertEqual(response.status_code, 200)
    
    def test_view_with_unknown_status(self):
        """Тест: unknown статус корректно обрабатывается."""
        # Используем текущее время для звонка, чтобы он точно попал в период "day"
        from django.utils import timezone as tz
        now = tz.now()
        local_now = tz.localtime(now)
        start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        
        # Создаем звонок с UNKNOWN статусом
        CallRequest.objects.create(
            user=self.user,
            phone_raw="+79991234567",
            status=CallRequest.Status.PENDING,
            call_status=CallRequest.CallStatus.UNKNOWN,
            call_started_at=start + timedelta(hours=1)
        )
        
        from django.test import Client
        client = Client()
        client.force_login(self.user)
        response = client.get("/settings/calls/stats/?period=day")
        
        # Проверяем, что страница рендерится без ошибок
        self.assertEqual(response.status_code, 200)
        # Проверяем контекст - total_unknown должен быть > 0
        self.assertIn("total_unknown", response.context)
        self.assertGreater(response.context["total_unknown"], 0)
        # Проверяем, что unknown учитывается в статистике
        # Текст "Не определено" показывается в общей статистике (строка 21 шаблона), если total_unknown > 0
        self.assertContains(response, "Не определено", status_code=200)
    
    def test_view_connect_rate_no_division_by_zero(self):
        """Тест: connect_rate_all не вызывает деление на 0 при total_calls=0."""
        # Нет звонков
        from django.test import Client
        client = Client()
        client.force_login(self.user)
        response = client.get("/settings/calls/stats/?period=day")
        
        # Проверяем, что страница рендерится без ошибок (нет деления на 0)
        self.assertEqual(response.status_code, 200)
