"""W2.2 — TOTP 2FA tests.

Covers:
- Model verify / provisioning_uri / recovery codes hash+verify.
- Views setup/verify happy + error paths.
- TwoFactorMandatoryMiddleware: admin redirect, non-admin pass-through,
  session flag prevents re-redirect, safe paths bypass.
"""

from __future__ import annotations

from unittest.mock import patch

import pyotp
from django.contrib.auth import get_user_model
from django.test import Client, RequestFactory, TestCase, override_settings

from accounts.middleware_2fa import TwoFactorMandatoryMiddleware, _user_requires_2fa
from accounts.models import AdminRecoveryCode, AdminTOTPDevice

User = get_user_model()


@override_settings(
    SECURE_SSL_REDIRECT=False,
    SECURE_HSTS_SECONDS=0,
    SESSION_COOKIE_SECURE=False,
)
class TOTPModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="totp_u", password="pw", email="totp@example.com"
        )

    def test_verify_correct_token(self):
        secret = pyotp.random_base32()
        device = AdminTOTPDevice.objects.create(user=self.user, secret_key=secret, confirmed=False)
        token = pyotp.TOTP(secret).now()
        self.assertTrue(device.verify(token))
        device.refresh_from_db()
        self.assertIsNotNone(device.last_verified_at)

    def test_verify_wrong_token(self):
        secret = pyotp.random_base32()
        device = AdminTOTPDevice.objects.create(user=self.user, secret_key=secret, confirmed=False)
        self.assertFalse(device.verify("000000"))

    def test_verify_empty_token(self):
        secret = pyotp.random_base32()
        device = AdminTOTPDevice.objects.create(user=self.user, secret_key=secret, confirmed=False)
        self.assertFalse(device.verify(""))
        self.assertFalse(device.verify("   "))

    def test_provisioning_uri_includes_issuer(self):
        secret = pyotp.random_base32()
        device = AdminTOTPDevice.objects.create(user=self.user, secret_key=secret, confirmed=False)
        uri = device.provisioning_uri(issuer="TEST")
        self.assertIn("otpauth://totp/", uri)
        self.assertIn("issuer=TEST", uri)
        self.assertIn(secret, uri)

    def test_recovery_code_hash_deterministic(self):
        h1 = AdminRecoveryCode.hash_code("ABCD1234")
        h2 = AdminRecoveryCode.hash_code("ABCD1234")
        self.assertEqual(h1, h2)
        self.assertEqual(len(h1), 64)

    def test_recovery_code_verify_and_consume(self):
        raw = "ABCD1234"
        AdminRecoveryCode.objects.create(user=self.user, code_hash=AdminRecoveryCode.hash_code(raw))
        # First use succeeds
        self.assertTrue(AdminRecoveryCode.verify_and_consume(self.user, raw))
        # Second use fails (used=True)
        self.assertFalse(AdminRecoveryCode.verify_and_consume(self.user, raw))

    def test_recovery_code_verify_normalizes_input(self):
        raw = "ABCD1234"
        AdminRecoveryCode.objects.create(user=self.user, code_hash=AdminRecoveryCode.hash_code(raw))
        # Lowercase + dashes normalized
        self.assertTrue(AdminRecoveryCode.verify_and_consume(self.user, "abcd-1234  "))


@override_settings(
    SECURE_SSL_REDIRECT=False,
    SECURE_HSTS_SECONDS=0,
    SESSION_COOKIE_SECURE=False,
)
class TwoFactorMiddlewareTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.admin = User.objects.create_superuser(
            username="mw_admin", email="a@m.ru", password="pw"
        )
        self.admin.role = User.Role.ADMIN
        self.admin.save(update_fields=["role"])
        self.manager = User.objects.create_user(
            username="mw_mgr",
            email="m@m.ru",
            password="pw",
            role=User.Role.MANAGER,
        )

    def test_user_requires_2fa_helper(self):
        self.assertTrue(_user_requires_2fa(self.admin))
        self.assertFalse(_user_requires_2fa(self.manager))

    def test_anonymous_passes_through(self):
        mw = TwoFactorMandatoryMiddleware(lambda r: None)
        req = self.factory.get("/")
        from django.contrib.auth.models import AnonymousUser

        req.user = AnonymousUser()
        req.session = {}
        result = mw.process_request(req)
        self.assertIsNone(result)

    def test_manager_passes_through(self):
        mw = TwoFactorMandatoryMiddleware(lambda r: None)
        req = self.factory.get("/")
        req.user = self.manager
        req.session = {}
        self.assertIsNone(mw.process_request(req))

    def test_admin_without_device_redirected_to_setup(self):
        mw = TwoFactorMandatoryMiddleware(lambda r: None)
        req = self.factory.get("/companies/")
        req.user = self.admin
        req.session = {}
        response = mw.process_request(req)
        self.assertIsNotNone(response)
        self.assertEqual(response.status_code, 302)
        self.assertIn("2fa/setup/", response.url)

    def test_admin_with_device_no_session_redirected_to_verify(self):
        secret = pyotp.random_base32()
        AdminTOTPDevice.objects.create(user=self.admin, secret_key=secret, confirmed=True)
        mw = TwoFactorMandatoryMiddleware(lambda r: None)
        req = self.factory.get("/companies/")
        req.user = self.admin
        req.session = {}
        response = mw.process_request(req)
        self.assertEqual(response.status_code, 302)
        self.assertIn("2fa/verify/", response.url)

    def test_admin_with_verified_session_passes(self):
        secret = pyotp.random_base32()
        AdminTOTPDevice.objects.create(user=self.admin, secret_key=secret, confirmed=True)
        mw = TwoFactorMandatoryMiddleware(lambda r: None)
        req = self.factory.get("/companies/")
        req.user = self.admin
        req.session = {"otp_verified": True}
        self.assertIsNone(mw.process_request(req))

    def test_safe_paths_bypass(self):
        mw = TwoFactorMandatoryMiddleware(lambda r: None)
        for path in ["/accounts/2fa/setup/", "/static/x.css", "/live/", "/api/v1/x/"]:
            req = self.factory.get(path)
            req.user = self.admin
            req.session = {}
            result = mw.process_request(req)
            self.assertIsNone(result, f"Safe path {path} не bypass'ил")


@override_settings(
    SECURE_SSL_REDIRECT=False,
    SECURE_HSTS_SECONDS=0,
    SESSION_COOKIE_SECURE=False,
)
class TOTPViewTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="v_admin", email="v@ex.ru", password="pw"
        )
        self.admin.role = User.Role.ADMIN
        self.admin.save(update_fields=["role"])
        self.manager = User.objects.create_user(
            username="v_mgr",
            email="vm@ex.ru",
            password="pw",
            role=User.Role.MANAGER,
        )

    def test_setup_forbidden_for_non_admin(self):
        c = Client()
        c.force_login(self.manager)
        r = c.get("/accounts/2fa/setup/")
        self.assertEqual(r.status_code, 403)

    def test_setup_get_shows_qr(self):
        c = Client()
        c.force_login(self.admin)
        r = c.get("/accounts/2fa/setup/")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"QR", r.content)
        device = AdminTOTPDevice.objects.get(user=self.admin)
        self.assertFalse(device.confirmed)

    def test_setup_post_valid_token_confirms_device(self):
        c = Client()
        c.force_login(self.admin)
        # GET first to create device + secret
        c.get("/accounts/2fa/setup/")
        device = AdminTOTPDevice.objects.get(user=self.admin)
        token = pyotp.TOTP(device.secret_key).now()
        r = c.post("/accounts/2fa/setup/", {"token": token})
        self.assertEqual(r.status_code, 200)
        device.refresh_from_db()
        self.assertTrue(device.confirmed)
        # Recovery codes created
        self.assertEqual(AdminRecoveryCode.objects.filter(user=self.admin).count(), 10)

    def test_verify_redirects_if_no_device(self):
        c = Client()
        c.force_login(self.admin)
        r = c.get("/accounts/2fa/verify/")
        self.assertEqual(r.status_code, 302)
        self.assertIn("setup", r.url)

    def test_verify_post_valid_token_sets_session(self):
        secret = pyotp.random_base32()
        AdminTOTPDevice.objects.create(user=self.admin, secret_key=secret, confirmed=True)
        c = Client()
        c.force_login(self.admin)
        token = pyotp.TOTP(secret).now()
        r = c.post("/accounts/2fa/verify/", {"token": token, "next": "/companies/"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.url, "/companies/")
        # Session should have flag
        self.assertTrue(c.session.get("otp_verified"))

    def test_verify_recovery_code(self):
        secret = pyotp.random_base32()
        AdminTOTPDevice.objects.create(user=self.admin, secret_key=secret, confirmed=True)
        AdminRecoveryCode.objects.create(
            user=self.admin, code_hash=AdminRecoveryCode.hash_code("ABCD1234")
        )
        c = Client()
        c.force_login(self.admin)
        r = c.post(
            "/accounts/2fa/verify/",
            {"recovery_code": "ABCD-1234", "next": "/"},
        )
        self.assertEqual(r.status_code, 302)
        self.assertTrue(c.session.get("otp_verified"))
        # Code consumed
        self.assertEqual(AdminRecoveryCode.objects.filter(user=self.admin, used=True).count(), 1)
