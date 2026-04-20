"""
Smoke tests для auth flow и unit tests для accounts/security.py.

Покрывает:
- get_client_ip: прокси allowlist, spoofing protection, невалидные IP
- is_ip_rate_limited: счётчик, превышение лимита
- record_failed_login_attempt + is_user_locked_out: блокировка после MAX_LOGIN_ATTEMPTS
- clear_login_attempts: сброс счётчиков
- SecureLoginView: вход по access key, вход по паролю (только admin), lockout
- SecureTokenObtainPairView: JWT login success/fail, rate limit, lockout
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, RequestFactory, TestCase, override_settings

from accounts.models import MagicLinkToken
from accounts.security import (
    MAX_LOGIN_ATTEMPTS,
    clear_login_attempts,
    get_client_ip,
    is_ip_rate_limited,
    is_user_locked_out,
    record_failed_login_attempt,
)

User = get_user_model()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request(factory, remote_addr="1.2.3.4", xff=None, proxy_ips=None):
    """Создаёт GET-запрос с нужными заголовками."""
    req = factory.get("/")
    req.META["REMOTE_ADDR"] = remote_addr
    if xff:
        req.META["HTTP_X_FORWARDED_FOR"] = xff
    # Патчим settings.PROXY_IPS прямо в META запроса через override_settings —
    # вместо этого передаём proxy_ips через контекст теста.
    return req, proxy_ips or []


# ---------------------------------------------------------------------------
# Unit tests: security.py
# ---------------------------------------------------------------------------


class GetClientIpTest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        cache.clear()

    def test_returns_remote_addr_without_proxy(self):
        req = self.factory.get("/")
        req.META["REMOTE_ADDR"] = "10.0.0.1"
        with override_settings(PROXY_IPS=[]):
            self.assertEqual(get_client_ip(req), "10.0.0.1")

    def test_ignores_xff_when_remote_addr_not_in_proxy_list(self):
        """XFF не должен доверяться, если REMOTE_ADDR не в PROXY_IPS."""
        req = self.factory.get("/")
        req.META["REMOTE_ADDR"] = "8.8.8.8"
        req.META["HTTP_X_FORWARDED_FOR"] = "5.5.5.5"
        with override_settings(PROXY_IPS=["10.0.0.1"]):
            # должен вернуть REMOTE_ADDR, а не XFF
            self.assertEqual(get_client_ip(req), "8.8.8.8")

    def test_uses_xff_when_remote_addr_is_trusted_proxy(self):
        """XFF используется только когда REMOTE_ADDR является доверенным прокси."""
        req = self.factory.get("/")
        req.META["REMOTE_ADDR"] = "10.0.0.1"
        req.META["HTTP_X_FORWARDED_FOR"] = "5.5.5.5, 10.0.0.1"
        with override_settings(PROXY_IPS=["10.0.0.1"]):
            self.assertEqual(get_client_ip(req), "5.5.5.5")

    def test_returns_unknown_for_invalid_remote_addr(self):
        req = self.factory.get("/")
        req.META["REMOTE_ADDR"] = "not-an-ip"
        with override_settings(PROXY_IPS=[]):
            self.assertEqual(get_client_ip(req), "unknown")

    def test_skips_invalid_xff_entries(self):
        """Если первый IP в XFF невалиден — берёт следующий валидный."""
        req = self.factory.get("/")
        req.META["REMOTE_ADDR"] = "10.0.0.1"
        req.META["HTTP_X_FORWARDED_FOR"] = "invalid, 5.5.5.5"
        with override_settings(PROXY_IPS=["10.0.0.1"]):
            self.assertEqual(get_client_ip(req), "5.5.5.5")

    def test_ipv6_address(self):
        req = self.factory.get("/")
        req.META["REMOTE_ADDR"] = "::1"
        with override_settings(PROXY_IPS=[]):
            self.assertEqual(get_client_ip(req), "::1")


class IsIpRateLimitedTest(TestCase):
    def setUp(self):
        cache.clear()

    def test_allows_requests_within_limit(self):
        for _ in range(3):
            self.assertFalse(
                is_ip_rate_limited("1.2.3.4", "test", max_requests=5, window_seconds=60)
            )

    def test_blocks_when_limit_reached(self):
        ip = "9.9.9.9"
        for _ in range(5):
            is_ip_rate_limited(ip, "block_test", max_requests=5, window_seconds=60)
        self.assertTrue(is_ip_rate_limited(ip, "block_test", max_requests=5, window_seconds=60))

    def test_different_ips_are_independent(self):
        for _ in range(5):
            is_ip_rate_limited("1.1.1.1", "sep", max_requests=5, window_seconds=60)
        # другой IP не должен быть заблокирован
        self.assertFalse(is_ip_rate_limited("2.2.2.2", "sep", max_requests=5, window_seconds=60))

    def test_different_key_prefixes_are_independent(self):
        for _ in range(5):
            is_ip_rate_limited("3.3.3.3", "prefix_a", max_requests=5, window_seconds=60)
        self.assertFalse(
            is_ip_rate_limited("3.3.3.3", "prefix_b", max_requests=5, window_seconds=60)
        )


class RecordFailedLoginAndLockoutTest(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="testuser", password="pass123")

    def tearDown(self):
        cache.clear()

    def test_not_locked_before_max_attempts(self):
        for i in range(MAX_LOGIN_ATTEMPTS - 1):
            record_failed_login_attempt("testuser", "1.2.3.4")
        self.assertFalse(is_user_locked_out("testuser"))

    def test_locked_after_max_attempts(self):
        for _ in range(MAX_LOGIN_ATTEMPTS):
            record_failed_login_attempt("testuser", "1.2.3.4")
        self.assertTrue(is_user_locked_out("testuser"))

    def test_lockout_is_case_insensitive(self):
        for _ in range(MAX_LOGIN_ATTEMPTS):
            record_failed_login_attempt("TESTUSER", "1.2.3.4")
        self.assertTrue(is_user_locked_out("testuser"))
        self.assertTrue(is_user_locked_out("TESTUSER"))

    def test_clear_removes_lockout(self):
        for _ in range(MAX_LOGIN_ATTEMPTS):
            record_failed_login_attempt("testuser", "1.2.3.4")
        self.assertTrue(is_user_locked_out("testuser"))
        clear_login_attempts("testuser")
        self.assertFalse(is_user_locked_out("testuser"))

    def test_clear_allows_login_again(self):
        for _ in range(MAX_LOGIN_ATTEMPTS):
            record_failed_login_attempt("testuser", "1.2.3.4")
        clear_login_attempts("testuser")
        # ещё одна попытка — не должна сразу заблокировать
        record_failed_login_attempt("testuser", "1.2.3.4")
        self.assertFalse(is_user_locked_out("testuser"))


# ---------------------------------------------------------------------------
# Smoke tests: SecureLoginView — access key flow
# ---------------------------------------------------------------------------


class AccessKeyLoginSmokeTest(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client()
        self.admin = User.objects.create_user(
            username="admin", password="adminpass", role=User.Role.ADMIN
        )
        self.manager = User.objects.create_user(
            username="manager", password="mgrpass", role=User.Role.MANAGER
        )

    def tearDown(self):
        cache.clear()

    def _create_access_key(self, user):
        _, plain_token = MagicLinkToken.create_for_user(
            user=user, created_by=self.admin, ttl_minutes=30
        )
        return plain_token

    def test_login_with_valid_access_key(self):
        token = self._create_access_key(self.manager)
        response = self.client.post(
            "/login/",
            {"login_type": "access_key", "access_key": token},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.wsgi_request.user.is_authenticated)

    def test_login_with_invalid_access_key(self):
        response = self.client.post(
            "/login/",
            {"login_type": "access_key", "access_key": "totally-wrong-key"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.wsgi_request.user.is_authenticated)

    def test_login_with_used_access_key(self):
        token = self._create_access_key(self.manager)
        # Первый вход — успешный
        self.client.post(
            "/login/",
            {"login_type": "access_key", "access_key": token},
            follow=True,
        )
        self.client.logout()
        # Второй вход тем же токеном — должен отказать
        response = self.client.post(
            "/login/",
            {"login_type": "access_key", "access_key": token},
        )
        self.assertFalse(response.wsgi_request.user.is_authenticated)


# ---------------------------------------------------------------------------
# Smoke tests: SecureLoginView — password flow (admin only)
# ---------------------------------------------------------------------------


class PasswordLoginSmokeTest(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client()
        self.admin = User.objects.create_user(
            username="admin2", password="adminpass", role=User.Role.ADMIN
        )
        self.manager = User.objects.create_user(
            username="manager2", password="mgrpass", role=User.Role.MANAGER
        )

    def tearDown(self):
        cache.clear()

    def test_admin_can_login_with_password(self):
        response = self.client.post(
            "/login/",
            {"login_type": "password", "username": "admin2", "password": "adminpass"},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.wsgi_request.user.is_authenticated)

    def test_manager_cannot_login_with_password(self):
        response = self.client.post(
            "/login/",
            {"login_type": "password", "username": "manager2", "password": "mgrpass"},
        )
        self.assertFalse(response.wsgi_request.user.is_authenticated)

    def test_wrong_password_returns_error(self):
        response = self.client.post(
            "/login/",
            {"login_type": "password", "username": "admin2", "password": "wrong"},
        )
        self.assertFalse(response.wsgi_request.user.is_authenticated)


# ---------------------------------------------------------------------------
# Smoke tests: JWT — SecureTokenObtainPairView
# ---------------------------------------------------------------------------


class JwtLoginSmokeTest(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client()
        self.admin = User.objects.create_user(
            username="jwtadmin", password="jwtpass123", role=User.Role.ADMIN
        )

    def tearDown(self):
        cache.clear()

    def test_jwt_login_success_returns_tokens_and_is_admin(self):
        response = self.client.post(
            "/api/token/",
            {"username": "jwtadmin", "password": "jwtpass123"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("access", data)
        self.assertIn("refresh", data)
        self.assertTrue(data.get("is_admin"))

    def test_jwt_login_wrong_credentials_returns_401(self):
        response = self.client.post(
            "/api/token/",
            {"username": "jwtadmin", "password": "wrongpass"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)

    def test_jwt_login_locked_account_returns_423(self):
        for _ in range(MAX_LOGIN_ATTEMPTS):
            record_failed_login_attempt("jwtadmin", "1.2.3.4")
        # get_remaining_lockout_time вызывает cache.ttl() — Redis-only метод.
        # В тестах используется LocMemCache, поэтому мокаем.
        with patch("accounts.jwt_views.get_remaining_lockout_time", return_value=600):
            response = self.client.post(
                "/api/token/",
                {"username": "jwtadmin", "password": "jwtpass123"},
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 423)

    def test_jwt_login_rate_limit_returns_429(self):
        # Исчерпываем IP rate limit
        for _ in range(60):
            is_ip_rate_limited("127.0.0.1", "jwt_login", max_requests=5, window_seconds=60)
        response = self.client.post(
            "/api/token/",
            {"username": "jwtadmin", "password": "jwtpass123"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 429)
