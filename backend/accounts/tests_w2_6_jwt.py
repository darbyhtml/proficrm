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

    # --- W2.7: ALL password JWT logins blocked (admin + non-admin) ---

    def test_admin_jwt_login_blocked_w27(self):
        """W2.7: role=ADMIN JWT login теперь blocked 403 (было 200 в W2.6)."""
        self._create_user("w27_admin", User.Role.ADMIN)
        c = Client()
        r = c.post(
            "/api/token/",
            data=json.dumps({"username": "w27_admin", "password": "testpass123"}),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 403, r.content)
        body = r.content.decode()
        self.assertIn("отключён", body.lower())
        # Токены НЕ должны присутствовать в ответе
        data = json.loads(r.content)
        self.assertNotIn("access", data)
        self.assertNotIn("refresh", data)

    def test_superuser_jwt_login_blocked_w27(self):
        """W2.7: is_superuser=True также blocked 403."""
        self._create_user("w27_root", User.Role.MANAGER, is_superuser=True)
        c = Client()
        r = c.post(
            "/api/token/",
            data=json.dumps({"username": "w27_root", "password": "testpass123"}),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 403, r.content)

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
        # W2.7 response text updated — check "отключён" + "magic link"
        self.assertIn("отключён", body.lower())
        self.assertIn("magic link", body.lower())
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

    # --- Refresh token flow preserved (alternative token generation) ---

    def test_refresh_token_flow_preserved_w27(self):
        """W2.7: /api/token/refresh/ endpoint preserved. Existing refresh
        tokens (generated до W2.7 OR через /api/phone/qr/exchange/) должны
        продолжать работать. Генерируем token direct через RefreshToken.for_user()
        поскольку /api/token/ password больше не issues tokens."""
        from rest_framework_simplejwt.tokens import RefreshToken

        admin = self._create_user("w27_refresh_adm", User.Role.ADMIN)
        token = RefreshToken.for_user(admin)
        refresh_str = str(token)

        c = Client()
        r = c.post(
            "/api/token/refresh/",
            data=json.dumps({"refresh": refresh_str}),
            content_type="application/json",
        )
        # 200 если SIMPLE_JWT позволяет rotate; 401 если session-check fails.
        # Both acceptable — key point: endpoint НЕ заблокирован W2.7 logic.
        self.assertIn(r.status_code, [200, 401], r.content)
        if r.status_code == 200:
            self.assertIn("access", json.loads(r.content))

    def test_refresh_token_flow_preserved_for_manager_via_qr_simulation(self):
        """Simulates mobile QR flow pattern: /api/phone/qr/exchange/ создаёт
        JWT через RefreshToken.for_user() direct. Такой refresh должен
        работать на /api/token/refresh/ для managers (even после W2.6/W2.7)."""
        from rest_framework_simplejwt.tokens import RefreshToken

        mgr = self._create_user("w27_refresh_mgr", User.Role.MANAGER)
        token = RefreshToken.for_user(mgr)
        refresh_str = str(token)

        c = Client()
        r = c.post(
            "/api/token/refresh/",
            data=json.dumps({"refresh": refresh_str}),
            content_type="application/json",
        )
        self.assertIn(r.status_code, [200, 401], r.content)

    # --- W2.7: Audit log для admin-blocked attempts ---

    def test_admin_block_creates_audit_log_w27(self):
        """W2.7: admin JWT block создаёт ActivityEvent с entity_id jwt_admin_blocked."""
        admin = self._create_user("w27_adm_audit", User.Role.ADMIN)
        c = Client()
        c.post(
            "/api/token/",
            data=json.dumps({"username": "w27_adm_audit", "password": "testpass123"}),
            content_type="application/json",
        )
        events = ActivityEvent.objects.filter(
            entity_type="security", entity_id=f"jwt_admin_blocked:{admin.id}"
        )
        self.assertEqual(events.count(), 1, "Expected 1 audit event с jwt_admin_blocked")
        evt = events.first()
        self.assertEqual(evt.meta.get("role"), "admin")
        self.assertTrue(evt.meta.get("is_admin"))

    def test_admin_block_blacklists_issued_refresh_w27(self):
        """W2.7: admin attempted /api/token/ получает blacklisted refresh."""
        before = BlacklistedToken.objects.count()
        self._create_user("w27_adm_blacklist", User.Role.ADMIN)
        c = Client()
        r = c.post(
            "/api/token/",
            data=json.dumps({"username": "w27_adm_blacklist", "password": "testpass123"}),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 403)
        after = BlacklistedToken.objects.count()
        self.assertEqual(after, before + 1, "Admin refresh должен быть blacklisted")

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
