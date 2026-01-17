"""
Тесты для phonebridge API (минимальные тесты совместимости для ЭТАП 1).
"""

from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from rest_framework import status
from phonebridge.models import CallRequest
import uuid
from datetime import datetime, timezone

User = get_user_model()


@override_settings(SECURE_SSL_REDIRECT=False)
class UpdateCallInfoViewTest(TestCase):
    """Тесты для UpdateCallInfoView (проверка совместимости legacy и extended форматов)."""
    
    def setUp(self):
        """Настройка тестовых данных."""
        self.user = User.objects.create_user(
            username="testuser",
            password="testpass123"
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        
        # Создаём тестовый CallRequest
        self.call_request = CallRequest.objects.create(
            user=self.user,
            phone_raw="+79991234567",
            status=CallRequest.Status.PENDING
        )
    
    def test_legacy_payload_acceptance(self):
        """Тест: legacy payload (4 поля) должен приниматься и обрабатываться как раньше."""
        url = "/api/phone/calls/update/"
        payload = {
            "call_request_id": str(self.call_request.id),
            "call_status": "connected",
            "call_started_at": "2024-01-15T14:30:00Z",
            "call_duration_seconds": 180
        }
        
        response = self.client.post(url, payload, format="json")
        
        # Проверяем, что запрос успешен
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["ok"], True)
        
        # Проверяем, что данные сохранены
        self.call_request.refresh_from_db()
        self.assertEqual(self.call_request.call_status, CallRequest.CallStatus.CONNECTED)
        self.assertIsNotNone(self.call_request.call_started_at)
        self.assertEqual(self.call_request.call_duration_seconds, 180)
    
    def test_extended_payload_acceptance(self):
        """Тест: extended payload (со всеми новыми полями) должен приниматься и сохраняться в БД."""
        url = "/api/phone/calls/update/"
        payload = {
            "call_request_id": str(self.call_request.id),
            "call_status": "connected",
            "call_started_at": "2024-01-15T14:30:00Z",
            "call_duration_seconds": 180,
            "call_ended_at": "2024-01-15T14:33:00Z",
            "direction": "outgoing",
            "resolve_method": "observer",
            "attempts_count": 1,
            "action_source": "crm_ui"
        }
        
        response = self.client.post(url, payload, format="json")
        
        # Проверяем, что запрос успешен
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["ok"], True)
        
        # Проверяем, что все поля сохранены в БД (ЭТАП 3)
        self.call_request.refresh_from_db()
        self.assertEqual(self.call_request.call_status, CallRequest.CallStatus.CONNECTED)
        self.assertIsNotNone(self.call_request.call_started_at)
        self.assertEqual(self.call_request.call_duration_seconds, 180)
        self.assertIsNotNone(self.call_request.call_ended_at)
        self.assertEqual(self.call_request.direction, CallRequest.CallDirection.OUTGOING)
        self.assertEqual(self.call_request.resolve_method, CallRequest.ResolveMethod.OBSERVER)
        self.assertEqual(self.call_request.attempts_count, 1)
        self.assertEqual(self.call_request.action_source, CallRequest.ActionSource.CRM_UI)
    
    def test_extended_payload_persists_new_fields(self):
        """Тест: extended payload должен сохранять все новые поля в БД."""
        url = "/api/phone/calls/update/"
        payload = {
            "call_request_id": str(self.call_request.id),
            "call_status": "no_answer",
            "call_started_at": "2024-01-15T14:30:00Z",
            "direction": "incoming",
            "resolve_method": "retry",
            "attempts_count": 3,
            "action_source": "notification"
        }
        
        response = self.client.post(url, payload, format="json")
        
        # Проверяем, что запрос успешен
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # Проверяем, что новые поля сохранены в БД
        self.call_request.refresh_from_db()
        self.assertEqual(self.call_request.direction, CallRequest.CallDirection.INCOMING)
        self.assertEqual(self.call_request.resolve_method, CallRequest.ResolveMethod.RETRY)
        self.assertEqual(self.call_request.attempts_count, 3)
        self.assertEqual(self.call_request.action_source, CallRequest.ActionSource.NOTIFICATION)
    
    def test_ended_at_autocompute_persists(self):
        """Тест: автоматически вычисленный call_ended_at должен сохраняться в БД."""
        url = "/api/phone/calls/update/"
        payload = {
            "call_request_id": str(self.call_request.id),
            "call_status": "connected",
            "call_started_at": "2024-01-15T14:30:00Z",
            "call_duration_seconds": 120
        }
        
        response = self.client.post(url, payload, format="json")
        
        # Проверяем, что запрос успешен
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # Проверяем, что call_ended_at вычислен и сохранён
        self.call_request.refresh_from_db()
        self.assertIsNotNone(self.call_request.call_ended_at)
        from datetime import timedelta
        expected_ended_at = self.call_request.call_started_at + timedelta(seconds=120)
        self.assertEqual(self.call_request.call_ended_at, expected_ended_at)
    
    def test_unknown_status_acceptance(self):
        """Тест: статус 'unknown' должен приниматься и сохраняться."""
        url = "/api/phone/calls/update/"
        payload = {
            "call_request_id": str(self.call_request.id),
            "call_status": "unknown",
            "call_started_at": "2024-01-15T14:30:00Z"
        }
        
        response = self.client.post(url, payload, format="json")
        
        # Проверяем, что запрос успешен
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # Проверяем, что статус сохранён как UNKNOWN
        self.call_request.refresh_from_db()
        self.assertEqual(self.call_request.call_status, CallRequest.CallStatus.UNKNOWN)
    
    def test_unknown_status_graceful_mapping(self):
        """Тест: неизвестный статус должен маппиться в UNKNOWN (не падать с 400)."""
        url = "/api/phone/calls/update/"
        payload = {
            "call_request_id": str(self.call_request.id),
            "call_status": "invalid_status_that_does_not_exist",
            "call_started_at": "2024-01-15T14:30:00Z"
        }
        
        response = self.client.post(url, payload, format="json")
        
        # Проверяем, что запрос успешен (не 400)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # Проверяем, что статус сохранён как UNKNOWN (graceful fallback)
        self.call_request.refresh_from_db()
        self.assertEqual(self.call_request.call_status, CallRequest.CallStatus.UNKNOWN)
    
    def test_minimal_payload_acceptance(self):
        """Тест: минимальный payload (только call_request_id) должен приниматься."""
        url = "/api/phone/calls/update/"
        payload = {
            "call_request_id": str(self.call_request.id)
        }
        
        response = self.client.post(url, payload, format="json")
        
        # Проверяем, что запрос успешен
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["ok"], True)
    
    def test_ended_at_computation(self):
        """Тест: если call_ended_at не передан, но есть started_at и duration, должен вычисляться и сохраняться."""
        url = "/api/phone/calls/update/"
        payload = {
            "call_request_id": str(self.call_request.id),
            "call_started_at": "2024-01-15T14:30:00Z",
            "call_duration_seconds": 180
        }
        
        response = self.client.post(url, payload, format="json")
        
        # Проверяем, что запрос успешен
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # Проверяем, что ended_at вычислен и сохранён в БД (ЭТАП 3)
        self.call_request.refresh_from_db()
        self.assertIsNotNone(self.call_request.call_started_at)
        self.assertEqual(self.call_request.call_duration_seconds, 180)
        self.assertIsNotNone(self.call_request.call_ended_at)
        # Проверяем, что ended_at = started_at + 180 секунд
        from datetime import timedelta
        expected_ended_at = self.call_request.call_started_at + timedelta(seconds=180)
        self.assertEqual(self.call_request.call_ended_at, expected_ended_at)
    
    def test_invalid_direction_graceful_handling(self):
        """Тест: неизвестный direction должен игнорироваться (не падать с 400)."""
        url = "/api/phone/calls/update/"
        payload = {
            "call_request_id": str(self.call_request.id),
            "call_status": "connected",
            "direction": "invalid_direction"
        }
        
        response = self.client.post(url, payload, format="json")
        
        # Проверяем, что запрос успешен (неизвестный direction игнорируется)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # Проверяем, что legacy поля сохранены
        self.call_request.refresh_from_db()
        self.assertEqual(self.call_request.call_status, CallRequest.CallStatus.CONNECTED)
    
    def test_ended_at_not_computed_if_duration_zero(self):
        """Тест: call_ended_at НЕ должен вычисляться, если duration == 0 (no_answer)."""
        url = "/api/phone/calls/update/"
        payload = {
            "call_request_id": str(self.call_request.id),
            "call_status": "no_answer",
            "call_started_at": "2024-01-15T14:30:00Z",
            "call_duration_seconds": 0  # Нет ответа, длительность = 0
        }
        
        response = self.client.post(url, payload, format="json")
        
        # Проверяем, что запрос успешен
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # Проверяем, что call_ended_at НЕ вычислен (остался null)
        self.call_request.refresh_from_db()
        self.assertIsNotNone(self.call_request.call_started_at)
        self.assertEqual(self.call_request.call_duration_seconds, 0)
        self.assertIsNone(self.call_request.call_ended_at)  # Должен остаться null
    
    def test_unknown_status_persists(self):
        """Тест: статус unknown должен сохраняться и не смешиваться с no_answer."""
        url = "/api/phone/calls/update/"
        payload = {
            "call_request_id": str(self.call_request.id),
            "call_status": "unknown",
            "call_started_at": "2024-01-15T14:30:00Z",
            "direction": "outgoing",
            "resolve_method": "retry",
            "attempts_count": 3,
            "action_source": "crm_ui"
        }
        
        response = self.client.post(url, payload, format="json")
        
        # Проверяем, что запрос успешен
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # Проверяем, что статус сохранён как UNKNOWN (не no_answer)
        self.call_request.refresh_from_db()
        self.assertEqual(self.call_request.call_status, CallRequest.CallStatus.UNKNOWN)
        self.assertEqual(self.call_request.direction, CallRequest.CallDirection.OUTGOING)
        self.assertEqual(self.call_request.resolve_method, CallRequest.ResolveMethod.RETRY)
        self.assertEqual(self.call_request.attempts_count, 3)
        self.assertEqual(self.call_request.action_source, CallRequest.ActionSource.CRM_UI)
    
    def test_invalid_resolve_method_graceful_handling(self):
        """Тест: неизвестный resolve_method должен игнорироваться (не падать с 400)."""
        url = "/api/phone/calls/update/"
        payload = {
            "call_request_id": str(self.call_request.id),
            "call_status": "connected",
            "resolve_method": "invalid_resolve_method"
        }
        
        response = self.client.post(url, payload, format="json")
        
        # Проверяем, что запрос успешен (неизвестный resolve_method игнорируется)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # Проверяем, что legacy поля сохранены, resolve_method остался null
        self.call_request.refresh_from_db()
        self.assertEqual(self.call_request.call_status, CallRequest.CallStatus.CONNECTED)
        self.assertIsNone(self.call_request.resolve_method)
    
    def test_invalid_action_source_graceful_handling(self):
        """Тест: неизвестный action_source должен игнорироваться (не падать с 400)."""
        url = "/api/phone/calls/update/"
        payload = {
            "call_request_id": str(self.call_request.id),
            "call_status": "connected",
            "action_source": "invalid_action_source"
        }
        
        response = self.client.post(url, payload, format="json")
        
        # Проверяем, что запрос успешен (неизвестный action_source игнорируется)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # Проверяем, что legacy поля сохранены, action_source остался null
        self.call_request.refresh_from_db()
        self.assertEqual(self.call_request.call_status, CallRequest.CallStatus.CONNECTED)
        self.assertIsNone(self.call_request.action_source)
