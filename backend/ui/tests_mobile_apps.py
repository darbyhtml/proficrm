"""F9 UI tests (2026-04-18): /admin/mobile-apps/ upload + toggle."""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from phonebridge.models import MobileAppBuild

User = get_user_model()


@override_settings(
    SECURE_SSL_REDIRECT=False,
    SECURE_HSTS_SECONDS=0,
    SESSION_COOKIE_SECURE=False,
)
class MobileAppsUIUploadTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="apk_admin", email="a@k.ru",
        )
        self.client.force_login(self.admin)

    def _apk(self, version_code=1, name="test.apk"):
        return SimpleUploadedFile(
            name, b"APK-binary-content-here", content_type="application/vnd.android.package-archive",
        )

    def test_list_page_renders(self):
        MobileAppBuild.objects.create(
            version_name="1.0.0", version_code=1,
            file=self._apk(), env="production", uploaded_by=self.admin,
        )
        resp = self.client.get("/admin/mobile-apps/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"1.0.0", resp.content)

    def test_upload_creates_build(self):
        resp = self.client.post(
            "/admin/mobile-apps/upload/",
            {"version_name": "2.0.0", "version_code": "10", "file": self._apk()},
        )
        self.assertEqual(resp.status_code, 302)
        build = MobileAppBuild.objects.get(version_code=10)
        self.assertEqual(build.version_name, "2.0.0")
        self.assertEqual(build.env, "production")
        self.assertTrue(build.is_active)
        self.assertEqual(build.uploaded_by, self.admin)
        self.assertTrue(build.sha256)  # авто-вычислен

    def test_upload_rejects_duplicate_version_code(self):
        MobileAppBuild.objects.create(
            version_name="1.0.0", version_code=5,
            file=self._apk(), env="production",
        )
        resp = self.client.post(
            "/admin/mobile-apps/upload/",
            {"version_name": "1.0.1", "version_code": "5", "file": self._apk()},
        )
        self.assertEqual(resp.status_code, 302)
        # Дубль не создан.
        self.assertEqual(MobileAppBuild.objects.filter(version_code=5).count(), 1)

    def test_upload_rejects_non_apk_extension(self):
        bad = SimpleUploadedFile("malware.exe", b"exe-content")
        resp = self.client.post(
            "/admin/mobile-apps/upload/",
            {"version_name": "1.0.0", "version_code": "1", "file": bad},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(MobileAppBuild.objects.exists())

    def test_upload_requires_admin(self):
        regular = User.objects.create_user(
            username="regular_apk", email="r@k.ru", role=User.Role.MANAGER,
        )
        self.client.force_login(regular)
        resp = self.client.post(
            "/admin/mobile-apps/upload/",
            {"version_name": "hack.0.0", "version_code": "1", "file": self._apk()},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(MobileAppBuild.objects.exists())

    def test_toggle_flips_is_active(self):
        build = MobileAppBuild.objects.create(
            version_name="3.0.0", version_code=30,
            file=self._apk(), env="production", is_active=True,
        )
        resp = self.client.post(f"/admin/mobile-apps/{build.id}/toggle/")
        self.assertEqual(resp.status_code, 302)
        build.refresh_from_db()
        self.assertFalse(build.is_active)
        # Повторный toggle — обратно.
        self.client.post(f"/admin/mobile-apps/{build.id}/toggle/")
        build.refresh_from_db()
        self.assertTrue(build.is_active)
