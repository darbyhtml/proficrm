"""
Тесты для W0.4 observability:
- core.sentry_context.SentryContextMiddleware (5 tags propagation)
- core.request_id.RequestIdMiddleware (request_id UUID + header)
- core.celery_signals.register_signals (task_prerun → request_id)
- crm.health.health / ready / sentry_smoke

DoD Wave 0.4: 3+ тестов на middleware (request_id propagation).
"""

from __future__ import annotations

from unittest.mock import patch

from django.test import Client, RequestFactory, TestCase, override_settings

from accounts.models import Branch, User
from core.request_id import RequestIdMiddleware, _thread_local, get_request_id
from core.sentry_context import SentryContextMiddleware


def _mk_user(username: str = "u1", role=User.Role.MANAGER, branch=None) -> User:
    return User.objects.create_user(username=username, password="x", role=role, branch=branch)


# ---------------------------------------------------------------------------
# RequestIdMiddleware
# ---------------------------------------------------------------------------


class RequestIdMiddlewareTests(TestCase):
    """Проверка что request_id генерируется, доступен и попадает в header."""

    def setUp(self) -> None:
        self.factory = RequestFactory()

    def test_request_id_generated_on_each_request(self) -> None:
        """Каждый запрос получает свой request_id (8-символьный)."""
        mw = RequestIdMiddleware(lambda r: None)
        r1 = self.factory.get("/")
        r2 = self.factory.get("/")
        mw.process_request(r1)
        mw.process_request(r2)
        self.assertTrue(hasattr(r1, "request_id"))
        self.assertTrue(hasattr(r2, "request_id"))
        self.assertEqual(len(r1.request_id), 8)
        self.assertNotEqual(r1.request_id, r2.request_id)

    def test_request_id_in_thread_local(self) -> None:
        """get_request_id() возвращает текущий request_id."""
        mw = RequestIdMiddleware(lambda r: None)
        request = self.factory.get("/")
        mw.process_request(request)
        self.assertEqual(get_request_id(), request.request_id)

    def test_request_id_end_to_end_via_client(self) -> None:
        """GET / возвращает header X-Request-ID."""
        client = Client()
        response = client.get("/health/")
        self.assertIn("X-Request-ID", response.headers)
        self.assertEqual(len(response.headers["X-Request-ID"]), 8)

    def test_thread_local_cleaned_after_response(self) -> None:
        """После process_response thread-local очищен (не протекает между requests)."""
        mw = RequestIdMiddleware(lambda r: None)
        request = self.factory.get("/")
        response = object.__new__(type("FakeResp", (), {"__setitem__": lambda *a: None}))
        mw.process_request(request)
        self.assertIsNotNone(get_request_id())
        mw.process_response(request, response)
        self.assertIsNone(get_request_id())


# ---------------------------------------------------------------------------
# SentryContextMiddleware — установка 5 тегов
# ---------------------------------------------------------------------------


class SentryContextMiddlewareTests(TestCase):
    """Тэги user_id/role/branch/request_id/feature_flags пишутся в sentry scope."""

    def setUp(self) -> None:
        self.factory = RequestFactory()
        self.branch = Branch.objects.create(code="ekb", name="Екатеринбург")
        self.user = _mk_user("mw_user", role=User.Role.MANAGER, branch=self.branch)

    def _call_middleware(self, request) -> dict:
        """Вызвать middleware и вернуть tags переданные в scope.set_tag."""
        captured_tags: dict[str, str] = {}
        captured_user: dict = {}

        class FakeScope:
            def set_tag(self, name, value):
                captured_tags[name] = value

            def set_user(self, data):
                captured_user.update(data)

        fake_scope = FakeScope()

        with patch("sentry_sdk.Scope.get_current_scope", return_value=fake_scope):
            mw = SentryContextMiddleware(lambda r: None)
            mw(request)

        return {"tags": captured_tags, "user": captured_user}

    def test_tags_written_for_authenticated_user(self) -> None:
        """Все 5 тегов присутствуют для залогиненного юзера."""
        request = self.factory.get("/")
        request.user = self.user
        request.request_id = "abc12345"
        result = self._call_middleware(request)
        tags = result["tags"]
        self.assertEqual(tags["user_id"], str(self.user.id))
        self.assertEqual(tags["role"], str(User.Role.MANAGER))
        self.assertEqual(tags["branch"], "ekb")
        self.assertEqual(tags["request_id"], "abc12345")
        self.assertIn("feature_flags", tags)  # пустая строка тоже ок
        self.assertEqual(result["user"]["id"], str(self.user.id))

    def test_anonymous_user_has_only_request_id_and_feature_flags(self) -> None:
        """Анонимный юзер → user_id/role/branch не пишутся."""
        from django.contrib.auth.models import AnonymousUser

        request = self.factory.get("/")
        request.user = AnonymousUser()
        request.request_id = "xyz99999"
        result = self._call_middleware(request)
        tags = result["tags"]
        self.assertEqual(tags["request_id"], "xyz99999")
        self.assertIn("feature_flags", tags)
        self.assertNotIn("user_id", tags)
        self.assertNotIn("role", tags)
        self.assertNotIn("branch", tags)

    def test_user_without_branch_skips_branch_tag(self) -> None:
        """User без branch → тег 'branch' не пишется."""
        user_no_branch = _mk_user("no_branch", role=User.Role.ADMIN, branch=None)
        request = self.factory.get("/")
        request.user = user_no_branch
        request.request_id = "r1"
        result = self._call_middleware(request)
        tags = result["tags"]
        self.assertEqual(tags["role"], str(User.Role.ADMIN))
        self.assertNotIn("branch", tags)

    def test_middleware_survives_missing_sentry_sdk(self) -> None:
        """Если sentry_sdk нет в окружении — middleware не падает."""
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "sentry_sdk":
                raise ImportError("simulated absence")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            mw = SentryContextMiddleware(lambda r: None)
            request = self.factory.get("/")
            request.user = self.user
            request.request_id = "r1"
            # Should not raise.
            mw(request)

    def test_feature_flags_tag_reflects_enabled(self) -> None:
        """При включении флага его имя попадает в feature_flags CSV."""
        from waffle.testutils import override_flag

        request = self.factory.get("/")
        request.user = self.user
        request.request_id = "r1"

        with override_flag("UI_V3B_DEFAULT", active=True):
            result = self._call_middleware(request)
        tags = result["tags"]
        self.assertIn("UI_V3B_DEFAULT", tags["feature_flags"])


# ---------------------------------------------------------------------------
# /live/ /ready/ /_debug/sentry-error/
# ---------------------------------------------------------------------------


class HealthEndpointsTests(TestCase):
    """Wave 0.4 — new endpoints /live/ /ready/ /_debug/sentry-error/."""

    def setUp(self) -> None:
        self.client = Client()

    def test_live_always_200(self) -> None:
        """Liveness — всегда 200, JSON {'status': 'ok'}."""
        response = self.client.get("/live/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "ok")

    def test_ready_200_when_services_ok(self) -> None:
        """Readiness — 200 + по чекам DB/Redis OK в test-env."""
        response = self.client.get("/ready/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["checks"]["database"]["status"], "ok")
        self.assertEqual(data["checks"]["redis"]["status"], "ok")

    @override_settings(DEBUG=False)
    def test_sentry_smoke_404_when_not_debug(self) -> None:
        """/_debug/sentry-error/ закрыт в проде (DEBUG=False)."""
        response = self.client.get("/_debug/sentry-error/")
        self.assertEqual(response.status_code, 404)

    @override_settings(DEBUG=True)
    def test_sentry_smoke_raises_when_debug(self) -> None:
        """/_debug/sentry-error/ бросает Exception при DEBUG=True."""
        with self.assertRaises(RuntimeError):
            self.client.raise_request_exception = True
            self.client.get("/_debug/sentry-error/")
