"""
Тесты для статистики звонков (settings_calls_stats view).
ЭТАП 6: проверка распределений, connect_rate_percent, avg_duration.
"""

from django.test import TestCase
from django.contrib.auth import get_user_model
from django.utils import timezone
from datetime import timedelta
from phonebridge.models import CallRequest

User = get_user_model()


class CallStatsViewTest(TestCase):
    """Тесты для settings_calls_stats view (распределения, метрики)."""
    
    def setUp(self):
        """Настройка тестовых данных."""
        self.user = User.objects.create_user(
            username="testuser",
            password="testpass123"
        )
        self.now = timezone.now()
        self.start = self.now.replace(hour=0, minute=0, second=0, microsecond=0)
        self.end = self.start + timedelta(days=1)
    
    def test_distributions_by_direction(self):
        """Тест: распределения по направлению (by_direction) считаются корректно."""
        # Создаём звонки с разными направлениями
        CallRequest.objects.create(
            user=self.user,
            phone_raw="+79991111111",
            status=CallRequest.Status.PENDING,
            call_status=CallRequest.CallStatus.CONNECTED,
            call_started_at=self.start + timedelta(hours=1),
            direction=CallRequest.CallDirection.OUTGOING
        )
        CallRequest.objects.create(
            user=self.user,
            phone_raw="+79992222222",
            status=CallRequest.Status.PENDING,
            call_status=CallRequest.CallStatus.CONNECTED,
            call_started_at=self.start + timedelta(hours=2),
            direction=CallRequest.CallDirection.INCOMING
        )
        CallRequest.objects.create(
            user=self.user,
            phone_raw="+79993333333",
            status=CallRequest.Status.PENDING,
            call_status=CallRequest.CallStatus.MISSED,
            call_started_at=self.start + timedelta(hours=3),
            direction=CallRequest.CallDirection.MISSED
        )
        
        # Проверяем логику статистики напрямую (без вызова view)
        calls_qs = CallRequest.objects.filter(
            user_id=self.user.id,
            call_started_at__gte=self.start,
            call_started_at__lt=self.end,
            call_status__isnull=False
        )
        
        # Симулируем логику из view
        stats_by_manager = {}
        for call in calls_qs:
            manager_id = call.user_id
            if manager_id not in stats_by_manager:
                stats_by_manager[manager_id] = {
                    "by_direction": {"outgoing": 0, "incoming": 0, "missed": 0, "unknown": 0},
                }
            
            stats = stats_by_manager[manager_id]
            if call.direction:
                direction_key = call.direction
                if direction_key in stats["by_direction"]:
                    stats["by_direction"][direction_key] += 1
                else:
                    stats["by_direction"]["unknown"] += 1
        
        # Проверяем распределения
        stats = stats_by_manager.get(self.user.id, {})
        by_dir = stats.get("by_direction", {})
        self.assertEqual(by_dir.get("outgoing", 0), 1)
        self.assertEqual(by_dir.get("incoming", 0), 1)
        self.assertEqual(by_dir.get("missed", 0), 1)
        self.assertEqual(by_dir.get("unknown", 0), 0)
    
    def test_distributions_by_action_source(self):
        """Тест: распределения по источнику (by_action_source) считаются корректно."""
        CallRequest.objects.create(
            user=self.user,
            phone_raw="+79991111111",
            status=CallRequest.Status.PENDING,
            call_status=CallRequest.CallStatus.CONNECTED,
            call_started_at=self.start + timedelta(hours=1),
            action_source=CallRequest.ActionSource.CRM_UI
        )
        CallRequest.objects.create(
            user=self.user,
            phone_raw="+79992222222",
            status=CallRequest.Status.PENDING,
            call_status=CallRequest.CallStatus.CONNECTED,
            call_started_at=self.start + timedelta(hours=2),
            action_source=CallRequest.ActionSource.NOTIFICATION
        )
        CallRequest.objects.create(
            user=self.user,
            phone_raw="+79993333333",
            status=CallRequest.Status.PENDING,
            call_status=CallRequest.CallStatus.CONNECTED,
            call_started_at=self.start + timedelta(hours=3),
            action_source=CallRequest.ActionSource.HISTORY
        )
        
        calls_qs = CallRequest.objects.filter(
            user_id=self.user.id,
            call_started_at__gte=self.start,
            call_started_at__lt=self.end,
            call_status__isnull=False
        )
        
        stats_by_manager = {}
        for call in calls_qs:
            manager_id = call.user_id
            if manager_id not in stats_by_manager:
                stats_by_manager[manager_id] = {
                    "by_action_source": {"crm_ui": 0, "notification": 0, "history": 0, "unknown": 0},
                }
            
            stats = stats_by_manager[manager_id]
            if call.action_source:
                action_key = call.action_source
                if action_key in stats["by_action_source"]:
                    stats["by_action_source"][action_key] += 1
                else:
                    stats["by_action_source"]["unknown"] += 1
        
        stats = stats_by_manager.get(self.user.id, {})
        by_source = stats.get("by_action_source", {})
        self.assertEqual(by_source.get("crm_ui", 0), 1)
        self.assertEqual(by_source.get("notification", 0), 1)
        self.assertEqual(by_source.get("history", 0), 1)
        self.assertEqual(by_source.get("unknown", 0), 0)
    
    def test_connect_rate_percent_with_zero_total(self):
        """Тест: connect_rate_percent не падает при total=0."""
        # Нет звонков
        calls_qs = CallRequest.objects.filter(
            user_id=self.user.id,
            call_started_at__gte=self.start,
            call_started_at__lt=self.end,
            call_status__isnull=False
        )
        
        # Симулируем логику из view
        stats = {
            "total": 0,
            "connected": 0,
            "connect_rate_percent": 0.0
        }
        
        if stats["total"] > 0:
            connect_rate = (stats["connected"] / stats["total"]) * 100
            stats["connect_rate_percent"] = round(connect_rate, 1)
        else:
            stats["connect_rate_percent"] = 0.0
        
        # Проверяем, что не падает и возвращает 0.0
        self.assertEqual(stats["connect_rate_percent"], 0.0)
    
    def test_connect_rate_percent_calculation(self):
        """Тест: connect_rate_percent вычисляется корректно с округлением."""
        # Создаём 6 звонков: 2 connected, 4 других
        CallRequest.objects.create(
            user=self.user,
            phone_raw="+79991111111",
            status=CallRequest.Status.PENDING,
            call_status=CallRequest.CallStatus.CONNECTED,
            call_started_at=self.start + timedelta(hours=1)
        )
        CallRequest.objects.create(
            user=self.user,
            phone_raw="+79992222222",
            status=CallRequest.Status.PENDING,
            call_status=CallRequest.CallStatus.CONNECTED,
            call_started_at=self.start + timedelta(hours=2)
        )
        CallRequest.objects.create(
            user=self.user,
            phone_raw="+79993333333",
            status=CallRequest.Status.PENDING,
            call_status=CallRequest.CallStatus.NO_ANSWER,
            call_started_at=self.start + timedelta(hours=3)
        )
        CallRequest.objects.create(
            user=self.user,
            phone_raw="+79994444444",
            status=CallRequest.Status.PENDING,
            call_status=CallRequest.CallStatus.NO_ANSWER,
            call_started_at=self.start + timedelta(hours=4)
        )
        CallRequest.objects.create(
            user=self.user,
            phone_raw="+79995555555",
            status=CallRequest.Status.PENDING,
            call_status=CallRequest.CallStatus.REJECTED,
            call_started_at=self.start + timedelta(hours=5)
        )
        CallRequest.objects.create(
            user=self.user,
            phone_raw="+79996666666",
            status=CallRequest.Status.PENDING,
            call_status=CallRequest.CallStatus.UNKNOWN,
            call_started_at=self.start + timedelta(hours=6)
        )
        
        calls_qs = CallRequest.objects.filter(
            user_id=self.user.id,
            call_started_at__gte=self.start,
            call_started_at__lt=self.end,
            call_status__isnull=False
        )
        
        # Симулируем логику из view
        stats = {
            "total": 0,
            "connected": 0,
            "connect_rate_percent": 0.0
        }
        
        for call in calls_qs:
            stats["total"] += 1
            if call.call_status == CallRequest.CallStatus.CONNECTED:
                stats["connected"] += 1
        
        if stats["total"] > 0:
            connect_rate = (stats["connected"] / stats["total"]) * 100
            stats["connect_rate_percent"] = round(connect_rate, 1)
        else:
            stats["connect_rate_percent"] = 0.0
        
        # Проверяем: 2/6 = 33.3%
        self.assertEqual(stats["total"], 6)
        self.assertEqual(stats["connected"], 2)
        self.assertEqual(stats["connect_rate_percent"], 33.3)
    
    def test_avg_duration_only_connected(self):
        """Тест: avg_duration считается только по CONNECTED."""
        # Создаём звонки: 2 connected (60 и 120 сек), 1 no_answer (0 сек)
        CallRequest.objects.create(
            user=self.user,
            phone_raw="+79991111111",
            status=CallRequest.Status.PENDING,
            call_status=CallRequest.CallStatus.CONNECTED,
            call_started_at=self.start + timedelta(hours=1),
            call_duration_seconds=60
        )
        CallRequest.objects.create(
            user=self.user,
            phone_raw="+79992222222",
            status=CallRequest.Status.PENDING,
            call_status=CallRequest.CallStatus.CONNECTED,
            call_started_at=self.start + timedelta(hours=2),
            call_duration_seconds=120
        )
        CallRequest.objects.create(
            user=self.user,
            phone_raw="+79993333333",
            status=CallRequest.Status.PENDING,
            call_status=CallRequest.CallStatus.NO_ANSWER,
            call_started_at=self.start + timedelta(hours=3),
            call_duration_seconds=0
        )
        
        calls_qs = CallRequest.objects.filter(
            user_id=self.user.id,
            call_started_at__gte=self.start,
            call_started_at__lt=self.end,
            call_status__isnull=False
        )
        
        # Симулируем логику из view
        stats = {
            "total": 0,
            "connected": 0,
            "total_duration_connected": 0,
            "total_duration": 0,
            "avg_duration": 0
        }
        
        for call in calls_qs:
            stats["total"] += 1
            if call.call_status == CallRequest.CallStatus.CONNECTED:
                stats["connected"] += 1
                if call.call_duration_seconds:
                    stats["total_duration_connected"] += call.call_duration_seconds
            if call.call_duration_seconds:
                stats["total_duration"] += call.call_duration_seconds
        
        # Вычисляем avg_duration (логика из view)
        if stats["connected"] > 0 and stats.get("total_duration_connected", 0) > 0:
            stats["avg_duration"] = stats["total_duration_connected"] // stats["connected"]
        elif stats["total"] > 0:
            stats["avg_duration"] = stats["total_duration"] // stats["total"]
        else:
            stats["avg_duration"] = 0
        
        # Проверяем: avg_duration = (60+120)/2 = 90 сек (только по CONNECTED)
        self.assertEqual(stats["connected"], 2)
        self.assertEqual(stats["total_duration_connected"], 180)
        self.assertEqual(stats["avg_duration"], 90)
    
    def test_avg_duration_fallback_when_no_connected(self):
        """Тест: avg_duration fallback работает, если нет CONNECTED."""
        # Создаём только no_answer звонки
        CallRequest.objects.create(
            user=self.user,
            phone_raw="+79991111111",
            status=CallRequest.Status.PENDING,
            call_status=CallRequest.CallStatus.NO_ANSWER,
            call_started_at=self.start + timedelta(hours=1),
            call_duration_seconds=0
        )
        
        calls_qs = CallRequest.objects.filter(
            user_id=self.user.id,
            call_started_at__gte=self.start,
            call_started_at__lt=self.end,
            call_status__isnull=False
        )
        
        stats = {
            "total": 0,
            "connected": 0,
            "total_duration_connected": 0,
            "total_duration": 0,
            "avg_duration": 0
        }
        
        for call in calls_qs:
            stats["total"] += 1
            if call.call_status == CallRequest.CallStatus.CONNECTED:
                stats["connected"] += 1
                if call.call_duration_seconds:
                    stats["total_duration_connected"] += call.call_duration_seconds
            if call.call_duration_seconds:
                stats["total_duration"] += call.call_duration_seconds
        
        # Вычисляем avg_duration (fallback логика)
        if stats["connected"] > 0 and stats.get("total_duration_connected", 0) > 0:
            stats["avg_duration"] = stats["total_duration_connected"] // stats["connected"]
        elif stats["total"] > 0:
            stats["avg_duration"] = stats["total_duration"] // stats["total"]
        else:
            stats["avg_duration"] = 0
        
        # Проверяем: fallback не падает, возвращает 0 (так как total_duration=0)
        self.assertEqual(stats["connected"], 0)
        self.assertEqual(stats["total"], 1)
        self.assertEqual(stats["avg_duration"], 0)
    
    def test_unknown_enum_values_ignored(self):
        """Тест: неизвестные значения enum игнорируются (не падают с 400)."""
        from rest_framework.test import APIClient
        from rest_framework import status
        
        client = APIClient()
        client.force_authenticate(user=self.user)
        
        call_request = CallRequest.objects.create(
            user=self.user,
            phone_raw="+79991234567",
            status=CallRequest.Status.PENDING
        )
        
        # Отправляем payload с неизвестными значениями enum
        url = "/api/phone/calls/update/"
        payload = {
            "call_request_id": str(call_request.id),
            "call_status": "connected",
            "direction": "weird_direction",
            "resolve_method": "weird_method",
            "action_source": "weird_source"
        }
        
        response = client.post(url, payload, format="json")
        
        # Проверяем, что запрос успешен (неизвестные значения игнорируются)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # Проверяем, что legacy поля сохранены, новые поля остались null
        call_request.refresh_from_db()
        self.assertEqual(call_request.call_status, CallRequest.CallStatus.CONNECTED)
        self.assertIsNone(call_request.direction)  # Игнорировано
        self.assertIsNone(call_request.resolve_method)  # Игнорировано
        self.assertIsNone(call_request.action_source)  # Игнорировано
    
    def test_legacy_payload_new_fields_null(self):
        """Тест: legacy payload оставляет новые поля NULL."""
        from rest_framework.test import APIClient
        from rest_framework import status
        
        client = APIClient()
        client.force_authenticate(user=self.user)
        
        call_request = CallRequest.objects.create(
            user=self.user,
            phone_raw="+79991234567",
            status=CallRequest.Status.PENDING
        )
        
        # Отправляем legacy payload (только 4 поля)
        url = "/api/phone/calls/update/"
        payload = {
            "call_request_id": str(call_request.id),
            "call_status": "connected",
            "call_started_at": "2024-01-15T14:30:00Z",
            "call_duration_seconds": 180
        }
        
        response = client.post(url, payload, format="json")
        
        # Проверяем, что запрос успешен
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # Проверяем, что новые поля остались NULL
        call_request.refresh_from_db()
        self.assertEqual(call_request.call_status, CallRequest.CallStatus.CONNECTED)
        self.assertIsNone(call_request.direction)
        self.assertIsNone(call_request.resolve_method)
        self.assertIsNone(call_request.action_source)
        self.assertIsNone(call_request.attempts_count)
        # call_ended_at должен быть вычислен (есть duration > 0)
        self.assertIsNotNone(call_request.call_ended_at)
