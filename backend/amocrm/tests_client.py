"""
Тесты для amocrm/client.py — AmoClient, AmoApiError, RateLimitError.

Все внешние HTTP-запросы мокируются через unittest.mock.
"""
from __future__ import annotations

import json
import time
import unittest
from unittest.mock import MagicMock, Mock, patch

from django.test import TestCase
from django.utils import timezone

from amocrm.client import AmoApiError, AmoClient, AmoResponse, RateLimitError, _json_loads


# ---------------------------------------------------------------------------
# Вспомогательная фабрика — создаёт AmoApiConfig-заглушку без БД
# ---------------------------------------------------------------------------

def _make_cfg(
    domain="test.amocrm.ru",
    long_lived_token="",
    access_token="",
    refresh_token="",
    token_type="Bearer",
    expires_at=None,
    client_id="client_id",
    client_secret="client_secret",
    redirect_uri="https://example.com/callback",
):
    cfg = Mock()
    cfg.domain = domain
    cfg.long_lived_token = long_lived_token
    cfg.access_token = access_token
    cfg.refresh_token = refresh_token
    cfg.token_type = token_type
    cfg.expires_at = expires_at
    cfg.client_id = client_id
    cfg.client_secret = client_secret
    cfg.get_client_secret = Mock(return_value=client_secret)
    cfg.redirect_uri = redirect_uri
    cfg.last_error = ""
    cfg.save = Mock()
    return cfg


# ---------------------------------------------------------------------------
# _json_loads
# ---------------------------------------------------------------------------

class TestJsonLoads(unittest.TestCase):
    def test_valid_json(self):
        result = _json_loads(b'{"key": "value"}')
        self.assertEqual(result, {"key": "value"})

    def test_invalid_json_returns_none(self):
        result = _json_loads(b"not json")
        self.assertIsNone(result)

    def test_empty_bytes_returns_none(self):
        result = _json_loads(b"")
        self.assertIsNone(result)

    def test_unicode_with_errors(self):
        # некорректный UTF-8 не должен бросать исключение
        result = _json_loads(b"\xff\xfe{}")
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# AmoClient.base — нормализация домена
# ---------------------------------------------------------------------------

class TestAmoClientBase(unittest.TestCase):
    def test_plain_domain(self):
        client = AmoClient(_make_cfg(domain="test.amocrm.ru"))
        self.assertEqual(client.base, "https://test.amocrm.ru")

    def test_domain_with_https_prefix(self):
        client = AmoClient(_make_cfg(domain="https://test.amocrm.ru"))
        self.assertEqual(client.base, "https://test.amocrm.ru")

    def test_domain_with_trailing_slash(self):
        client = AmoClient(_make_cfg(domain="test.amocrm.ru/"))
        self.assertEqual(client.base, "https://test.amocrm.ru")

    def test_empty_domain_raises(self):
        client = AmoClient(_make_cfg(domain=""))
        with self.assertRaises(AmoApiError):
            _ = client.base

    def test_whitespace_domain_raises(self):
        client = AmoClient(_make_cfg(domain="   "))
        with self.assertRaises(AmoApiError):
            _ = client.base


# ---------------------------------------------------------------------------
# AmoClient._token_valid
# ---------------------------------------------------------------------------

class TestTokenValid(unittest.TestCase):
    def test_long_lived_token_always_valid(self):
        cfg = _make_cfg(long_lived_token="my_long_lived_token")
        client = AmoClient(cfg)
        self.assertTrue(client._token_valid())

    def test_no_access_token_invalid(self):
        cfg = _make_cfg(long_lived_token="", access_token="")
        client = AmoClient(cfg)
        self.assertFalse(client._token_valid())

    def test_access_token_no_expires_valid(self):
        cfg = _make_cfg(access_token="tok", expires_at=None)
        client = AmoClient(cfg)
        self.assertTrue(client._token_valid())

    def test_access_token_expires_in_future_valid(self):
        future = timezone.now() + timezone.timedelta(hours=1)
        cfg = _make_cfg(access_token="tok", expires_at=future)
        client = AmoClient(cfg)
        self.assertTrue(client._token_valid())

    def test_access_token_expires_in_past_invalid(self):
        past = timezone.now() - timezone.timedelta(hours=1)
        cfg = _make_cfg(access_token="tok", expires_at=past)
        client = AmoClient(cfg)
        self.assertFalse(client._token_valid())

    def test_access_token_expires_in_30_seconds_invalid(self):
        # Рефреш за 60 сек до истечения
        soon = timezone.now() + timezone.timedelta(seconds=30)
        cfg = _make_cfg(access_token="tok", expires_at=soon)
        client = AmoClient(cfg)
        self.assertFalse(client._token_valid())


# ---------------------------------------------------------------------------
# AmoClient.ensure_token
# ---------------------------------------------------------------------------

class TestEnsureToken(unittest.TestCase):
    def test_long_lived_token_no_refresh(self):
        cfg = _make_cfg(long_lived_token="lltoken")
        client = AmoClient(cfg)
        client.refresh_token = Mock()  # не должен вызываться
        client.ensure_token()
        client.refresh_token.assert_not_called()

    def test_empty_long_lived_token_raises(self):
        cfg = _make_cfg(long_lived_token="   ")
        client = AmoClient(cfg)
        with self.assertRaises(AmoApiError):
            client.ensure_token()

    def test_no_access_token_raises(self):
        cfg = _make_cfg(long_lived_token="", access_token="")
        client = AmoClient(cfg)
        with self.assertRaises(AmoApiError):
            client.ensure_token()

    def test_valid_token_no_refresh(self):
        future = timezone.now() + timezone.timedelta(hours=1)
        cfg = _make_cfg(access_token="tok", expires_at=future)
        client = AmoClient(cfg)
        client.refresh_token = Mock()
        client.ensure_token()
        client.refresh_token.assert_not_called()

    def test_expired_token_triggers_refresh(self):
        past = timezone.now() - timezone.timedelta(hours=1)
        cfg = _make_cfg(access_token="tok", expires_at=past)
        client = AmoClient(cfg)
        client.refresh_token = Mock()
        client.ensure_token()
        client.refresh_token.assert_called_once()


# ---------------------------------------------------------------------------
# AmoClient.authorize_url
# ---------------------------------------------------------------------------

class TestAuthorizeUrl(unittest.TestCase):
    def test_returns_amocrm_oauth_url(self):
        cfg = _make_cfg(client_id="my_client_id")
        client = AmoClient(cfg)
        url = client.authorize_url()
        self.assertIn("https://www.amocrm.ru/oauth", url)
        self.assertIn("my_client_id", url)
        self.assertIn("proficrm_migrate", url)


# ---------------------------------------------------------------------------
# AmoClient._request — базовые HTTP сценарии
# ---------------------------------------------------------------------------

class TestAmoClientRequest(TestCase):
    """
    Тестирует _request() мокируя urllib.request.build_opener.
    """

    def _mock_opener(self, status: int, body: bytes, headers: dict | None = None):
        """Создаёт mock opener, который возвращает указанный ответ."""
        headers = headers or {}

        mock_response = MagicMock()
        mock_response.status = status
        mock_response.read.return_value = body
        mock_response.headers.items.return_value = list(headers.items())
        mock_response.__enter__ = Mock(return_value=mock_response)
        mock_response.__exit__ = Mock(return_value=False)

        mock_opener = MagicMock()
        mock_opener.open.return_value = mock_response
        return mock_opener

    @patch("amocrm.client.urllib.request.build_opener")
    def test_request_returns_response(self, mock_build_opener):
        mock_build_opener.return_value = self._mock_opener(200, b'{"id": 1}')
        cfg = _make_cfg(long_lived_token="lltoken")
        client = AmoClient(cfg)
        resp = client._request("GET", "https://test.amocrm.ru/api/v4/contacts", auth=True)
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.data, {"id": 1})

    @patch("amocrm.client.urllib.request.build_opener")
    def test_request_without_auth(self, mock_build_opener):
        mock_build_opener.return_value = self._mock_opener(200, b'{}')
        cfg = _make_cfg(long_lived_token="", access_token="")
        client = AmoClient(cfg)
        # auth=False не должен вызывать ensure_token
        resp = client._request("POST", "https://test.amocrm.ru/oauth2/access_token",
                               json_body={"grant_type": "authorization_code"}, auth=False)
        self.assertEqual(resp.status, 200)

    @patch("amocrm.client.urllib.request.build_opener")
    def test_request_raises_on_network_error(self, mock_build_opener):
        mock_opener = MagicMock()
        mock_opener.open.side_effect = Exception("Connection refused")
        mock_build_opener.return_value = mock_opener
        cfg = _make_cfg(long_lived_token="lltoken")
        client = AmoClient(cfg)
        with self.assertRaises(AmoApiError) as ctx:
            client._request("GET", "https://test.amocrm.ru/api/v4/contacts", auth=True)
        self.assertIn("Connection refused", str(ctx.exception))


# ---------------------------------------------------------------------------
# AmoClient.get — retry logic на 429
# ---------------------------------------------------------------------------

class TestAmoClientGet(TestCase):
    @patch("amocrm.client.time.sleep")
    def test_get_retries_on_429_then_succeeds(self, mock_sleep):
        cfg = _make_cfg(long_lived_token="lltoken")
        client = AmoClient(cfg)

        responses = [
            AmoResponse(status=429, data=None, headers={"retry-after": "1"}),
            AmoResponse(status=200, data=[{"id": 1}], headers={}),
        ]
        client._request = Mock(side_effect=responses)

        result = client.get("/api/v4/contacts")
        self.assertEqual(result, [{"id": 1}])
        self.assertEqual(client._request.call_count, 2)

    @patch("amocrm.client.time.sleep")
    def test_get_raises_rate_limit_after_max_retries(self, mock_sleep):
        cfg = _make_cfg(long_lived_token="lltoken")
        client = AmoClient(cfg)
        client._request = Mock(return_value=AmoResponse(status=429, data=None, headers={}))

        with self.assertRaises(RateLimitError):
            client.get("/api/v4/contacts")

    def test_get_raises_on_403(self):
        cfg = _make_cfg(long_lived_token="lltoken")
        client = AmoClient(cfg)
        client._request = Mock(return_value=AmoResponse(status=403, data=None, headers={}))

        with self.assertRaises(AmoApiError):
            client.get("/api/v4/contacts")

    def test_get_returns_data_on_200(self):
        cfg = _make_cfg(long_lived_token="lltoken")
        client = AmoClient(cfg)
        client._request = Mock(return_value=AmoResponse(
            status=200, data={"_embedded": {"contacts": [{"id": 1}]}}, headers={}
        ))
        result = client.get("/api/v4/contacts")
        self.assertIsNotNone(result)
