"""W2.3 Phase 1 — CSP infrastructure tests.

Tests covered:
- SecurityHeadersMiddleware emits both enforce + report-only headers.
- Nonce unique per request, present в оба headers.
- CDN allowlist (cdn.jsdelivr.net) present в script-src.
- Strict header omits 'unsafe-inline' для script-src.
- Strict header имеет report-uri.
- /csp-report/ endpoint принимает valid reports → 204.
- /csp-report/ rejects: oversized, invalid JSON, GET method.
"""

from __future__ import annotations

import json
import re

from django.test import Client, TestCase, override_settings

# Production-mode CSP headers активны только при DEBUG=False.
# Также CSP templates загружаются в settings.py блоке `if not DEBUG`, поэтому
# надо выставить их explicitly для test override.
CSP_ENFORCE_TEMPLATE = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' 'nonce-{nonce}' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: https: blob:; "
    "font-src 'self' data:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self';"
)
CSP_STRICT_TEMPLATE = (
    "default-src 'self'; "
    "script-src 'self' 'nonce-{nonce}' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: https: blob:; "
    "font-src 'self' data:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "report-uri /csp-report/;"
)


@override_settings(
    DEBUG=False,
    SECURE_SSL_REDIRECT=False,
    SECURE_HSTS_SECONDS=0,
    SESSION_COOKIE_SECURE=False,
    CSP_HEADER_ENFORCE_TEMPLATE=CSP_ENFORCE_TEMPLATE,
    CSP_HEADER_STRICT_TEMPLATE=CSP_STRICT_TEMPLATE,
)
class CSPHeadersTest(TestCase):
    """Middleware emits правильные CSP headers."""

    def _fetch_headers(self):
        """GET /login/ — publicly accessible page, triggers middleware."""
        c = Client()
        r = c.get("/login/")
        return r.headers

    def test_enforce_header_present(self):
        headers = self._fetch_headers()
        self.assertIn("Content-Security-Policy", headers)
        # Enforce keeps 'unsafe-inline' (safety net Phase 1)
        self.assertIn("'unsafe-inline'", headers["Content-Security-Policy"])

    def test_report_only_header_present(self):
        headers = self._fetch_headers()
        self.assertIn("Content-Security-Policy-Report-Only", headers)

    def test_strict_header_omits_unsafe_inline_in_script_src(self):
        """Shadow strict: script-src БЕЗ 'unsafe-inline'."""
        headers = self._fetch_headers()
        strict = headers["Content-Security-Policy-Report-Only"]
        # Extract script-src directive
        match = re.search(r"script-src\s+([^;]+);", strict)
        self.assertIsNotNone(match, "script-src directive должен присутствовать")
        script_src = match.group(1)
        self.assertNotIn(
            "'unsafe-inline'",
            script_src,
            f"Strict script-src НЕ должен содержать 'unsafe-inline': {script_src}",
        )
        self.assertIn("'self'", script_src)

    def test_nonce_present_in_both_headers(self):
        headers = self._fetch_headers()
        for key in ["Content-Security-Policy", "Content-Security-Policy-Report-Only"]:
            h = headers[key]
            m = re.search(r"'nonce-([A-Za-z0-9_-]+)'", h)
            self.assertIsNotNone(m, f"{key}: nonce должен присутствовать")
            self.assertGreaterEqual(len(m.group(1)), 20, "Nonce должен быть достаточно длинным")

    def test_nonce_unique_per_request(self):
        c = Client()
        r1 = c.get("/login/")
        r2 = c.get("/login/")
        n1 = re.search(r"'nonce-([^']+)'", r1.headers["Content-Security-Policy"]).group(1)
        n2 = re.search(r"'nonce-([^']+)'", r2.headers["Content-Security-Policy"]).group(1)
        self.assertNotEqual(n1, n2, "Nonce должен различаться между requests")

    def test_cdn_allowlist_in_both_headers(self):
        headers = self._fetch_headers()
        for key in ["Content-Security-Policy", "Content-Security-Policy-Report-Only"]:
            self.assertIn("https://cdn.jsdelivr.net", headers[key], f"{key}: CDN missing")

    def test_report_uri_in_strict_header(self):
        headers = self._fetch_headers()
        self.assertIn(
            "report-uri /csp-report/",
            headers["Content-Security-Policy-Report-Only"],
            "Strict policy должен иметь report-uri",
        )

    def test_frame_ancestors_in_enforce(self):
        """Regression: frame-ancestors 'none' preserved."""
        headers = self._fetch_headers()
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])


class CSPReportEndpointTest(TestCase):
    """/csp-report/ endpoint тестирование."""

    def test_accepts_valid_report_returns_204(self):
        c = Client()
        report = {
            "csp-report": {
                "violated-directive": "script-src",
                "blocked-uri": "inline",
                "document-uri": "https://example.com/page",
                "source-file": "https://example.com/page",
                "line-number": 42,
            }
        }
        r = c.post(
            "/csp-report/",
            data=json.dumps(report),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 204)

    def test_accepts_unwrapped_report(self):
        """Older browsers могут send report без 'csp-report' wrapper."""
        c = Client()
        report = {
            "violated-directive": "style-src",
            "blocked-uri": "inline",
        }
        r = c.post(
            "/csp-report/",
            data=json.dumps(report),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 204)

    def test_rejects_oversized_body(self):
        c = Client()
        big_body = "x" * 20_000
        r = c.post(
            "/csp-report/",
            data=big_body,
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 400)

    def test_rejects_invalid_json(self):
        c = Client()
        r = c.post(
            "/csp-report/",
            data="not json at all",
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 400)

    def test_rejects_get_method(self):
        c = Client()
        r = c.get("/csp-report/")
        self.assertEqual(r.status_code, 405, "GET должен return 405 (require_POST)")

    def test_csrf_exempt(self):
        """Браузеры не отправляют CSRF token для CSP reports."""
        c = Client(enforce_csrf_checks=True)
        report = {"csp-report": {"violated-directive": "script-src"}}
        r = c.post(
            "/csp-report/",
            data=json.dumps(report),
            content_type="application/json",
        )
        # CSRF exempt → 204 (не 403 CSRF failure).
        self.assertEqual(r.status_code, 204)

    def test_non_dict_payload_returns_400(self):
        """Invalid shape (e.g. array instead of object) → 400."""
        c = Client()
        r = c.post(
            "/csp-report/",
            data=json.dumps(["not", "a", "dict"]),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 400)
