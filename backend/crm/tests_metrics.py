"""F11 (2026-04-18): тесты /metrics endpoint (Prometheus exposition)."""

from __future__ import annotations

from django.test import TestCase, override_settings


@override_settings(
    SECURE_SSL_REDIRECT=False,
    SECURE_HSTS_SECONDS=0,
    SESSION_COOKIE_SECURE=False,
)
class MetricsEndpointTests(TestCase):
    def test_returns_503_when_token_not_configured(self):
        with override_settings(METRICS_TOKEN=""):
            resp = self.client.get("/metrics")
            self.assertEqual(resp.status_code, 503)

    def test_returns_401_when_bearer_missing(self):
        with override_settings(METRICS_TOKEN="secret123"):
            resp = self.client.get("/metrics")
            self.assertEqual(resp.status_code, 401)

    def test_returns_401_when_wrong_token(self):
        with override_settings(METRICS_TOKEN="secret123"):
            resp = self.client.get("/metrics", HTTP_AUTHORIZATION="Bearer wrong")
            self.assertEqual(resp.status_code, 401)

    def test_returns_200_and_prometheus_format_with_valid_token(self):
        with override_settings(METRICS_TOKEN="secret123"):
            resp = self.client.get("/metrics", HTTP_AUTHORIZATION="Bearer secret123")
            self.assertEqual(resp.status_code, 200)
            body = resp.content.decode("utf-8")
            # Prometheus exposition format:
            self.assertIn("# HELP crm_up", body)
            self.assertIn("# TYPE crm_up gauge", body)
            self.assertIn("crm_up 1", body)
            # Content-Type совместим с Prometheus parser.
            self.assertIn("text/plain", resp["Content-Type"])

    def test_includes_business_metrics(self):
        with override_settings(METRICS_TOKEN="secret123"):
            resp = self.client.get("/metrics", HTTP_AUTHORIZATION="Bearer secret123")
            body = resp.content.decode("utf-8")
            # Проверяем, что ключевые бизнес-метрики появились.
            for metric_name in [
                "crm_companies_total",
                "crm_tasks_open",
                "crm_conversations_waiting_offline",
                "crm_conversations_open",
                "crm_users_absent",
                "crm_mobile_app_builds_active",
            ]:
                self.assertIn(metric_name, body, f"Missing metric: {metric_name}")
