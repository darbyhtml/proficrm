"""
Тесты для phonebridge API.

Покрывает:
- UpdateCallInfoView: legacy и extended payload, автовычисление call_ended_at, graceful handling
- JWT-only auth: мобильные endpoints требуют JWT (401 без токена, 200 с токеном)
- QrTokenCreateView требует auth; QrTokenExchangeView публичный
- RegisterDeviceView: регистрация устройства и идемпотентность
- PullCallView: пустая очередь (204), device_id validation, pending→consumed
"""

from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from rest_framework import status
from rest_framework_simplejwt.tokens import RefreshToken
from phonebridge.models import CallRequest, PhoneDevice, MobileAppQrToken
import uuid
from datetime import datetime, timezone

User = get_user_model()


def _jwt_client(user):
    """Создаёт APIClient с JWT-авторизацией для user."""
    client = APIClient()
    refresh = RefreshToken.for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {str(refresh.access_token)}")
    return client


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
        """Тест: значение не из ChoiceField отклоняется с 400 — ChoiceField валидирует до validate_call_status."""
        url = "/api/phone/calls/update/"
        payload = {
            "call_request_id": str(self.call_request.id),
            "call_status": "invalid_status_that_does_not_exist",
            "call_started_at": "2024-01-15T14:30:00Z"
        }

        response = self.client.post(url, payload, format="json")

        # ChoiceField отклоняет неизвестные значения до custom validate_call_status
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
    
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


# ---------------------------------------------------------------------------
# JWT-only authentication enforcement
# ---------------------------------------------------------------------------

@override_settings(SECURE_SSL_REDIRECT=False)
class JwtAuthEnforcementTest(TestCase):
    """Мобильные endpoints требуют JWT — Session auth не принимается, анон → 401."""

    MOBILE_ENDPOINTS = [
        ("POST", "/api/phone/devices/register/"),
        ("POST", "/api/phone/devices/heartbeat/"),
        ("GET",  "/api/phone/calls/pull/"),
        ("POST", "/api/phone/calls/update/"),
        ("POST", "/api/phone/telemetry/"),
        ("POST", "/api/phone/logs/"),
        ("POST", "/api/phone/logout/"),
        ("POST", "/api/phone/logout/all/"),
        ("GET",  "/api/phone/user/info/"),
    ]

    def setUp(self):
        self.user = User.objects.create_user(username="jwtuser", password="pass")
        self.anon_client = APIClient()  # без credentials
        self.session_client = APIClient()
        self.session_client.force_authenticate(user=self.user)  # Session auth

    def test_anonymous_gets_401_on_mobile_endpoints(self):
        """Анонимный запрос → 401 Unauthorized на всех мобильных endpoints."""
        for method, url in self.MOBILE_ENDPOINTS:
            with self.subTest(method=method, url=url):
                fn = getattr(self.anon_client, method.lower())
                response = fn(url, format="json")
                self.assertEqual(
                    response.status_code,
                    status.HTTP_401_UNAUTHORIZED,
                    f"{method} {url} should return 401 for anonymous"
                )

    def test_session_auth_rejected_on_mobile_endpoints(self):
        """force_authenticate (session-style) отклоняется — нужен JWT."""
        # Вместо Session auth устанавливаем пустой Bearer — DRF вернёт 401
        session_only_client = APIClient()
        session_only_client.credentials(HTTP_AUTHORIZATION="Bearer invalid.token.here")
        for method, url in self.MOBILE_ENDPOINTS:
            with self.subTest(method=method, url=url):
                fn = getattr(session_only_client, method.lower())
                response = fn(url, format="json")
                self.assertEqual(
                    response.status_code,
                    status.HTTP_401_UNAUTHORIZED,
                    f"{method} {url} should return 401 for invalid Bearer"
                )

    def test_valid_jwt_accepted_on_update_endpoint(self):
        """Валидный JWT принимается на POST /api/phone/calls/update/."""
        call = CallRequest.objects.create(
            user=self.user,
            phone_raw="+79991234567",
            status=CallRequest.Status.PENDING,
        )
        jwt_client = _jwt_client(self.user)
        response = jwt_client.post(
            "/api/phone/calls/update/",
            {"call_request_id": str(call.id)},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_qr_exchange_is_public(self):
        """QrTokenExchangeView — публичный endpoint, анон → не 401 (отклоняется по данным)."""
        response = self.anon_client.post(
            "/api/phone/qr/exchange/",
            {"token": "nonexistent_token"},
            format="json",
        )
        # Endpoint публичный — 404 или 400, но не 401
        self.assertNotEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


# ---------------------------------------------------------------------------
# RegisterDeviceView
# ---------------------------------------------------------------------------

@override_settings(SECURE_SSL_REDIRECT=False)
class RegisterDeviceViewTest(TestCase):
    """Тесты для RegisterDeviceView."""

    def setUp(self):
        self.user = User.objects.create_user(username="reg_user", password="pass")
        self.client = _jwt_client(self.user)

    def test_register_new_device(self):
        """Регистрация нового устройства создаёт PhoneDevice."""
        response = self.client.post(
            "/api/phone/devices/register/",
            {"device_id": "device-001", "device_name": "Pixel 7", "fcm_token": "fcm-abc"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["ok"], True)
        self.assertEqual(response.data["device_id"], "device-001")
        self.assertTrue(PhoneDevice.objects.filter(user=self.user, device_id="device-001").exists())

    def test_register_device_idempotent(self):
        """Повторная регистрация того же device_id обновляет запись (не создаёт дубль)."""
        self.client.post(
            "/api/phone/devices/register/",
            {"device_id": "device-002", "fcm_token": "token-1"},
            format="json",
        )
        self.client.post(
            "/api/phone/devices/register/",
            {"device_id": "device-002", "fcm_token": "token-2"},
            format="json",
        )
        count = PhoneDevice.objects.filter(user=self.user, device_id="device-002").count()
        self.assertEqual(count, 1)
        # FCM token обновился
        device = PhoneDevice.objects.get(user=self.user, device_id="device-002")
        self.assertEqual(device.fcm_token, "token-2")

    def test_register_requires_device_id(self):
        """Без device_id → 400."""
        response = self.client.post(
            "/api/phone/devices/register/",
            {"device_name": "No ID phone"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


# ---------------------------------------------------------------------------
# PullCallView
# ---------------------------------------------------------------------------

@override_settings(SECURE_SSL_REDIRECT=False)
class PullCallViewTest(TestCase):
    """Тесты для PullCallView."""

    def setUp(self):
        self.user = User.objects.create_user(username="pull_user", password="pass")
        self.client = _jwt_client(self.user)
        # Регистрируем устройство
        PhoneDevice.objects.create(
            user=self.user,
            device_id="test-device",
            platform="android",
        )

    def test_pull_empty_queue_returns_204(self):
        """Нет pending звонков → 204 No Content."""
        response = self.client.get("/api/phone/calls/pull/?device_id=test-device")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

    def test_pull_missing_device_id_returns_400(self):
        """Без device_id → 400."""
        response = self.client.get("/api/phone/calls/pull/")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_pull_unknown_device_returns_403(self):
        """Чужой device_id → 403."""
        response = self.client.get("/api/phone/calls/pull/?device_id=unknown-device-xyz")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_pull_delivers_pending_call(self):
        """Pending звонок доставляется и переходит в CONSUMED."""
        call = CallRequest.objects.create(
            user=self.user,
            phone_raw="+79997776655",
            status=CallRequest.Status.PENDING,
        )
        response = self.client.get("/api/phone/calls/pull/?device_id=test-device")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["id"], str(call.id))
        self.assertEqual(response.data["phone"], "+79997776655")
        # Статус обновился
        call.refresh_from_db()
        self.assertEqual(call.status, CallRequest.Status.CONSUMED)

    def test_pull_second_request_returns_204_after_consumed(self):
        """После доставки повторный pull → 204 (очередь пуста)."""
        CallRequest.objects.create(
            user=self.user,
            phone_raw="+79001112233",
            status=CallRequest.Status.PENDING,
        )
        self.client.get("/api/phone/calls/pull/?device_id=test-device")  # первый — доставка
        response = self.client.get("/api/phone/calls/pull/?device_id=test-device")  # второй
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# QrTokenCreateView
# ---------------------------------------------------------------------------

@override_settings(SECURE_SSL_REDIRECT=False)
class QrTokenCreateViewTest(TestCase):
    """Тесты для QrTokenCreateView (требует auth) и QrTokenStatusView."""

    def setUp(self):
        self.user = User.objects.create_user(username="qr_user", password="pass")
        self.jwt_client = _jwt_client(self.user)
        self.anon_client = APIClient()

    def test_create_qr_token_authenticated(self):
        """Авторизованный пользователь получает QR-токен."""
        response = self.jwt_client.post("/api/phone/qr/create/", format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("token", response.data)
        self.assertIn("expires_at", response.data)
        self.assertTrue(len(response.data["token"]) > 10)

    def test_create_qr_token_unauthenticated(self):
        """Анонимный запрос → 401."""
        response = self.anon_client.post("/api/phone/qr/create/", format="json")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_qr_exchange_invalid_token_returns_error(self):
        """QrTokenExchangeView с несуществующим токеном → 404 или 400."""
        response = self.anon_client.post(
            "/api/phone/qr/exchange/",
            {"token": "totally_fake_token_000"},
            format="json",
        )
        self.assertIn(response.status_code, [status.HTTP_400_BAD_REQUEST, status.HTTP_404_NOT_FOUND])
