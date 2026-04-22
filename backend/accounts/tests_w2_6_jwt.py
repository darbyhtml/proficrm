"""W2.6 — JWT role filter tests.

/api/token/ (SecureTokenObtainPairView) разрешён ТОЛЬКО для admin/superuser.
Non-admin users должны входить:
- через magic link на /login/ или /auth/magic/<token>/ (web)
- через /api/phone/qr/exchange/ (mobile app, НЕ использует password)
"""

from __future__ import annotations

import json

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken

from audit.models import ActivityEvent

User = get_user_model()


@override_settings(
    SECURE_SSL_REDIRECT=False,
    SECURE_HSTS_SECONDS=0,
    SESSION_COOKIE_SECURE=False,
)
class JWTRoleFilterTest(TestCase):
    """W2.6: /api/token/ блокирует non-admin password login."""

    def setUp(self):
        # Сбрасываем кеш rate-limit чтобы тесты не мешали друг другу
        cache.clear()

    def _create_user(self, username: str, role: str, is_superuser: bool = False):
        """Helper: создаёт user с usable password."""
        if is_superuser:
            user = User.objects.create_superuser(
                username=username,
                email=f"{username}@w26.ru",
                password="testpass123",
            )
            user.role = role
            user.save(update_fields=["role"])
        else:
            user = User.objects.create_user(
                username=username,
                email=f"{username}@w26.ru",
                password="testpass123",
                role=role,
            )
        return user

    # --- Admin path: allowed ---

    def test_admin_jwt_login_succeeds(self):
        """role=ADMIN (без superuser) — JWT login allowed."""
        self._create_user("w26_admin", User.Role.ADMIN)
        c = Client()
        r = c.post(
            "/api/token/",
            data=json.dumps({"username": "w26_admin", "password": "testpass123"}),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 200)
        data = json.loads(r.content)
        self.assertIn("access", data)
        self.assertIn("refresh", data)
        self.assertTrue(data.get("is_admin"))

    def test_superuser_jwt_login_succeeds(self):
        """is_superuser=True — JWT login allowed даже если role != ADMIN."""
        # Edge case: теоретически может существовать superuser с role=manager.
        # is_superuser должен override.
        self._create_user("w26_root", User.Role.MANAGER, is_superuser=True)
        c = Client()
        r = c.post(
            "/api/token/",
            data=json.dumps({"username": "w26_root", "password": "testpass123"}),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 200, r.content)
        data = json.loads(r.content)
        self.assertTrue(data.get("is_admin"))

    # --- Non-admin path: blocked (403) ---

    def test_manager_jwt_login_blocked(self):
        """role=MANAGER с valid password → 403, НЕ 200."""
        self._create_user("w26_mgr", User.Role.MANAGER)
        c = Client()
        r = c.post(
            "/api/token/",
            data=json.dumps({"username": "w26_mgr", "password": "testpass123"}),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 403, r.content)
        body = r.content.decode()
        self.assertIn("администраторов", body)
        self.assertIn("magic link", body)
        # Токены НЕ должны присутствовать в ответе
        data = json.loads(r.content)
        self.assertNotIn("access", data)
        self.assertNotIn("refresh", data)

    def test_branch_director_jwt_login_blocked(self):
        """role=BRANCH_DIRECTOR → 403."""
        self._create_user("w26_bd", User.Role.BRANCH_DIRECTOR)
        c = Client()
        r = c.post(
            "/api/token/",
            data=json.dumps({"username": "w26_bd", "password": "testpass123"}),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 403)

    def test_sales_head_jwt_login_blocked(self):
        """role=SALES_HEAD (РОП) → 403."""
        self._create_user("w26_sh", User.Role.SALES_HEAD)
        c = Client()
        r = c.post(
            "/api/token/",
            data=json.dumps({"username": "w26_sh", "password": "testpass123"}),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 403)

    def test_group_manager_jwt_login_blocked(self):
        """role=GROUP_MANAGER → 403."""
        self._create_user("w26_gm", User.Role.GROUP_MANAGER)
        c = Client()
        r = c.post(
            "/api/token/",
            data=json.dumps({"username": "w26_gm", "password": "testpass123"}),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 403)

    def test_tenderist_jwt_login_blocked(self):
        """role=TENDERIST → 403."""
        self._create_user("w26_tn", User.Role.TENDERIST)
        c = Client()
        r = c.post(
            "/api/token/",
            data=json.dumps({"username": "w26_tn", "password": "testpass123"}),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 403)

    # --- Invalid credentials: still 401 ---

    def test_invalid_password_returns_401(self):
        """Wrong password → 401, не 403 (не leak'аем существование user)."""
        self._create_user("w26_mgr2", User.Role.MANAGER)
        c = Client()
        r = c.post(
            "/api/token/",
            data=json.dumps({"username": "w26_mgr2", "password": "wrongpass"}),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 401)

    def test_nonexistent_user_returns_401(self):
        """Unknown username → 401."""
        c = Client()
        r = c.post(
            "/api/token/",
            data=json.dumps({"username": "nosuchuser", "password": "anything"}),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 401)

    # --- Refresh token flow preserved for admin ---

    def test_refresh_token_flow_for_admin(self):
        """Admin refresh token работает после W2.6."""
        self._create_user("w26_adm2", User.Role.ADMIN)
        c = Client()
        r = c.post(
            "/api/token/",
            data=json.dumps({"username": "w26_adm2", "password": "testpass123"}),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 200)
        refresh_token = json.loads(r.content)["refresh"]

        r2 = c.post(
            "/api/token/refresh/",
            data=json.dumps({"refresh": refresh_token}),
            content_type="application/json",
        )
        self.assertEqual(r2.status_code, 200, r2.content)
        self.assertIn("access", json.loads(r2.content))

    # --- Audit log + blacklist для non-admin attempt ---

    def test_non_admin_block_creates_audit_log(self):
        """При 403 non-admin'а создаётся ActivityEvent с entity_id jwt_non_admin_blocked."""
        mgr = self._create_user("w26_mgr3", User.Role.MANAGER)
        c = Client()
        c.post(
            "/api/token/",
            data=json.dumps({"username": "w26_mgr3", "password": "testpass123"}),
            content_type="application/json",
        )
        # Проверяем event
        events = ActivityEvent.objects.filter(
            entity_type="security", entity_id=f"jwt_non_admin_blocked:{mgr.id}"
        )
        self.assertEqual(events.count(), 1, "Expected exactly 1 audit event for non-admin block")
        evt = events.first()
        self.assertEqual(evt.meta.get("role"), "manager")

    def test_non_admin_block_blacklists_issued_refresh(self):
        """super().post() уже создал refresh, надо его blacklist'нуть чтобы
        attacker не использовал интерцепт."""
        # Baseline: сколько blacklisted refresh до теста
        before = BlacklistedToken.objects.count()
        before_outstanding = OutstandingToken.objects.count()

        self._create_user("w26_mgr4", User.Role.MANAGER)
        c = Client()
        r = c.post(
            "/api/token/",
            data=json.dumps({"username": "w26_mgr4", "password": "testpass123"}),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 403)
        # Добавился outstanding (от super().post), и он же сразу blacklist'нут
        after_outstanding = OutstandingToken.objects.count()
        after_blacklisted = BlacklistedToken.objects.count()
        self.assertEqual(after_outstanding, before_outstanding + 1)
        self.assertEqual(after_blacklisted, before + 1)

    # --- clear_login_attempts не вызывается для 403 ---

    def test_non_admin_block_does_not_increment_lockout(self):
        """Repeated non-admin JWT attempts не должны lockout'ить пользователя."""
        self._create_user("w26_mgr5", User.Role.MANAGER)
        c = Client()
        # 3 попытки JWT с правильным password (все blocked)
        for _ in range(3):
            r = c.post(
                "/api/token/",
                data=json.dumps({"username": "w26_mgr5", "password": "testpass123"}),
                content_type="application/json",
            )
            self.assertEqual(r.status_code, 403)
        # Проверяем что user НЕ locked (ключ не установлен в cache)
        from accounts.security import is_user_locked_out

        self.assertFalse(is_user_locked_out("w26_mgr5"), "Non-admin не должен lockout'иться")
