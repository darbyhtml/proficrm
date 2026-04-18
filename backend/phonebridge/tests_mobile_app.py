"""F9 (2026-04-18): тесты MobileAppLatestView — endpoint для
CRMProfiDialer auto-update.

GET /api/phone/app/latest/ → JSON с version_name/code/sha256/size/
download_url последней активной production-сборки.
"""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from phonebridge.models import MobileAppBuild

User = get_user_model()


@override_settings(
    SECURE_SSL_REDIRECT=False,
    SECURE_HSTS_SECONDS=0,
    SESSION_COOKIE_SECURE=False,
)
class MobileAppLatestViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="mobile_tester",
            email="m@t.ru",
        )
        # JWT токен — endpoint требует аутентификации.
        refresh = RefreshToken.for_user(self.user)
        self.jwt = str(refresh.access_token)
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.jwt}")

    def _make_build(self, version_name="1.0.0", version_code=1, is_active=True, env="production"):
        apk = SimpleUploadedFile(
            f"test-{version_code}.apk",
            b"APK binary content for test",
            content_type="application/vnd.android.package-archive",
        )
        return MobileAppBuild.objects.create(
            version_name=version_name,
            version_code=version_code,
            file=apk,
            is_active=is_active,
            env=env,
            uploaded_by=self.user,
        )

    def test_returns_404_when_no_builds(self):
        resp = self.client.get("/api/phone/app/latest/")
        self.assertEqual(resp.status_code, 404)

    def test_returns_latest_active_production_build(self):
        self._make_build(version_name="1.0.0", version_code=1)
        self._make_build(version_name="1.2.0", version_code=5)
        resp = self.client.get("/api/phone/app/latest/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["version_name"], "1.2.0")
        self.assertEqual(resp.data["version_code"], 5)
        self.assertIn("download_url", resp.data)
        self.assertIn("sha256", resp.data)
        self.assertIn("size_bytes", resp.data)

    def test_excludes_inactive_builds(self):
        self._make_build(version_name="1.0.0", version_code=1, is_active=True)
        self._make_build(version_name="2.0.0", version_code=10, is_active=False)
        resp = self.client.get("/api/phone/app/latest/")
        # Последняя активная — 1.0.0 (хотя 2.0.0 свежее).
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["version_name"], "1.0.0")

    def test_excludes_non_production_env(self):
        self._make_build(version_name="1.0.0", version_code=1, env="staging")
        resp = self.client.get("/api/phone/app/latest/")
        self.assertEqual(resp.status_code, 404)

    def test_requires_authentication(self):
        anon = APIClient()
        resp = anon.get("/api/phone/app/latest/")
        self.assertIn(resp.status_code, (401, 403))
