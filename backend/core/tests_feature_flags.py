"""
Тесты для W0.3 feature flags:
- core.feature_flags.is_enabled
- core.feature_flags.active_flags_for_user
- core.permissions.FeatureFlagPermission
- core.templatetags.feature_flags (simple_tag + block tag)
- core.api.FeatureFlagsView

Цель: ≥90% покрытия `core/feature_flags.py` (DoD Wave 0.3).
"""

from __future__ import annotations

import os
from unittest.mock import patch

from django.template import Context, Template
from django.test import RequestFactory, TestCase
from rest_framework.test import APIClient
from rest_framework.views import APIView
from waffle.models import Flag

from accounts.models import Branch, User
from core.feature_flags import (
    EMAIL_BOUNCE_HANDLING,
    KNOWN_FLAGS,
    POLICY_DECISION_LOG_DASHBOARD,
    TWO_FACTOR_MANDATORY_FOR_ADMINS,
    UI_V3B_DEFAULT,
    active_flags_for_user,
    is_enabled,
)
from core.permissions import FeatureFlagPermission


def _mk_user(username: str = "u1", role=User.Role.MANAGER, branch=None) -> User:
    return User.objects.create_user(username=username, password="x", role=role, branch=branch)


class IsEnabledTests(TestCase):
    """Базовые сценарии is_enabled."""

    def setUp(self) -> None:
        self.user = _mk_user("user1")
        # Убеждаемся что все 4 dev-flag'а существуют (их создала миграция).
        for name in KNOWN_FLAGS:
            Flag.objects.get_or_create(name=name, defaults={"everyone": False})

    def test_flag_off_by_default(self) -> None:
        """Свежесозданный флаг → off."""
        self.assertFalse(is_enabled(UI_V3B_DEFAULT, user=self.user))

    def test_flag_on_everyone_true(self) -> None:
        """Если everyone=True — включён для всех."""
        Flag.objects.filter(name=UI_V3B_DEFAULT).update(everyone=True)
        self.assertTrue(is_enabled(UI_V3B_DEFAULT, user=self.user))
        # Без user тоже включён.
        self.assertTrue(is_enabled(UI_V3B_DEFAULT))

    def test_flag_on_everyone_false_explicit(self) -> None:
        """everyone=False — жёстко выключен."""
        Flag.objects.filter(name=UI_V3B_DEFAULT).update(everyone=False)
        self.assertFalse(is_enabled(UI_V3B_DEFAULT, user=self.user))

    def test_unknown_flag_returns_false(self) -> None:
        """Несуществующий флаг → False (WAFFLE_FLAG_DEFAULT=False)."""
        self.assertFalse(is_enabled("DOES_NOT_EXIST_FLAG"))

    def test_kill_switch_env_overrides_on_flag(self) -> None:
        """FEATURE_FLAG_KILL_<NAME>=1 выключает даже при everyone=True."""
        Flag.objects.filter(name=UI_V3B_DEFAULT).update(everyone=True)
        # Без env — включён.
        self.assertTrue(is_enabled(UI_V3B_DEFAULT, user=self.user))
        # С env kill-switch — выключен.
        with patch.dict(os.environ, {f"FEATURE_FLAG_KILL_{UI_V3B_DEFAULT}": "1"}):
            self.assertFalse(is_enabled(UI_V3B_DEFAULT, user=self.user))

    def test_kill_switch_only_reacts_to_exact_value_1(self) -> None:
        """Значения '0', 'true', 'yes' и пустое игнорируются — только ровно '1'."""
        Flag.objects.filter(name=UI_V3B_DEFAULT).update(everyone=True)
        for bad_val in ("", "0", "true", "yes", "on", "1 "):
            with patch.dict(
                os.environ, {f"FEATURE_FLAG_KILL_{UI_V3B_DEFAULT}": bad_val}
            ):
                self.assertTrue(
                    is_enabled(UI_V3B_DEFAULT, user=self.user),
                    f"Unexpected kill from value {bad_val!r}",
                )

    def test_is_enabled_with_request(self) -> None:
        """is_enabled с настоящим HttpRequest работает."""
        factory = RequestFactory()
        request = factory.get("/")
        request.user = self.user
        Flag.objects.filter(name=UI_V3B_DEFAULT).update(everyone=True)
        self.assertTrue(is_enabled(UI_V3B_DEFAULT, request=request))

    def test_is_enabled_without_user_and_request(self) -> None:
        """is_enabled без user/request → fallback на everyone."""
        Flag.objects.filter(name=POLICY_DECISION_LOG_DASHBOARD).update(everyone=True)
        self.assertTrue(is_enabled(POLICY_DECISION_LOG_DASHBOARD))
        Flag.objects.filter(name=POLICY_DECISION_LOG_DASHBOARD).update(everyone=False)
        self.assertFalse(is_enabled(POLICY_DECISION_LOG_DASHBOARD))
        # everyone=None (default в waffle) — возвращаем False
        Flag.objects.filter(name=POLICY_DECISION_LOG_DASHBOARD).update(everyone=None)
        self.assertFalse(is_enabled(POLICY_DECISION_LOG_DASHBOARD))

    def test_is_enabled_branch_argument_accepted(self) -> None:
        """Аргумент branch принимается (не должен ломать)."""
        branch = Branch.objects.create(code="ekb", name="Екатеринбург")
        # Просто проверка, что вызов не падает — семантика branch-based override
        # настраивается в waffle через Flag.groups/users; наш wrapper делает
        # pass-through.
        result = is_enabled(EMAIL_BOUNCE_HANDLING, user=self.user, branch=branch)
        self.assertFalse(result)  # флаг выключен


class ActiveFlagsForUserTests(TestCase):
    def setUp(self) -> None:
        self.user = _mk_user("user1")
        for name in KNOWN_FLAGS:
            Flag.objects.get_or_create(name=name, defaults={"everyone": False})

    def test_returns_all_known_flags(self) -> None:
        result = active_flags_for_user(self.user)
        self.assertEqual(set(result.keys()), set(KNOWN_FLAGS))

    def test_all_off_by_default(self) -> None:
        result = active_flags_for_user(self.user)
        for name, enabled in result.items():
            self.assertFalse(enabled, f"Flag {name} unexpectedly enabled")

    def test_reflects_flag_state_change(self) -> None:
        Flag.objects.filter(name=UI_V3B_DEFAULT).update(everyone=True)
        result = active_flags_for_user(self.user)
        self.assertTrue(result[UI_V3B_DEFAULT])
        self.assertFalse(result[EMAIL_BOUNCE_HANDLING])

    def test_works_with_none_user(self) -> None:
        """active_flags_for_user(None) — не падает на анонимном."""
        result = active_flags_for_user(None)
        self.assertEqual(set(result.keys()), set(KNOWN_FLAGS))


class FeatureFlagPermissionTests(TestCase):
    def setUp(self) -> None:
        self.user = _mk_user("u1")
        for name in KNOWN_FLAGS:
            Flag.objects.get_or_create(name=name, defaults={"everyone": False})
        self.factory = RequestFactory()

    def _make_view(self, flag_name: str | None):
        class DummyView(APIView):
            feature_flag_required = flag_name

        return DummyView()

    def test_no_flag_required_allows(self) -> None:
        request = self.factory.get("/")
        request.user = self.user
        view = self._make_view(None)
        self.assertTrue(FeatureFlagPermission().has_permission(request, view))

    def test_flag_off_denies(self) -> None:
        request = self.factory.get("/")
        request.user = self.user
        view = self._make_view(UI_V3B_DEFAULT)
        self.assertFalse(FeatureFlagPermission().has_permission(request, view))

    def test_flag_on_allows(self) -> None:
        Flag.objects.filter(name=UI_V3B_DEFAULT).update(everyone=True)
        request = self.factory.get("/")
        request.user = self.user
        view = self._make_view(UI_V3B_DEFAULT)
        self.assertTrue(FeatureFlagPermission().has_permission(request, view))


class TemplateTagTests(TestCase):
    def setUp(self) -> None:
        self.user = _mk_user("tpl_user")
        for name in KNOWN_FLAGS:
            Flag.objects.get_or_create(name=name, defaults={"everyone": False})
        self.factory = RequestFactory()

    def _render(self, template_src: str, with_user: bool = True) -> str:
        tpl = Template(template_src)
        request = self.factory.get("/")
        if with_user:
            request.user = self.user
        ctx = Context({"request": request})
        return tpl.render(ctx)

    def test_simple_tag_returns_false(self) -> None:
        tpl = (
            "{% load feature_flags %}"
            '{% feature_flag "UI_V3B_DEFAULT" as ff %}'
            "{% if ff %}YES{% else %}NO{% endif %}"
        )
        self.assertEqual(self._render(tpl).strip(), "NO")

    def test_simple_tag_returns_true_when_flag_on(self) -> None:
        Flag.objects.filter(name=UI_V3B_DEFAULT).update(everyone=True)
        tpl = (
            "{% load feature_flags %}"
            '{% feature_flag "UI_V3B_DEFAULT" as ff %}'
            "{% if ff %}YES{% else %}NO{% endif %}"
        )
        self.assertEqual(self._render(tpl).strip(), "YES")

    def test_block_tag_renders_content_when_on(self) -> None:
        Flag.objects.filter(name=UI_V3B_DEFAULT).update(everyone=True)
        tpl = (
            "{% load feature_flags %}"
            '{% feature_enabled "UI_V3B_DEFAULT" %}v3b-content{% endfeature_enabled %}'
        )
        self.assertEqual(self._render(tpl).strip(), "v3b-content")

    def test_block_tag_empty_when_off(self) -> None:
        tpl = (
            "{% load feature_flags %}"
            '{% feature_enabled "UI_V3B_DEFAULT" %}v3b-content{% endfeature_enabled %}'
        )
        self.assertEqual(self._render(tpl).strip(), "")

    def test_works_with_anonymous_user(self) -> None:
        """Рендер без authenticated user — просто False (не падение)."""
        from django.contrib.auth.models import AnonymousUser

        tpl_src = (
            "{% load feature_flags %}"
            '{% feature_flag "UI_V3B_DEFAULT" as ff %}'
            "{% if ff %}YES{% else %}NO{% endif %}"
        )
        tpl = Template(tpl_src)
        request = self.factory.get("/")
        request.user = AnonymousUser()
        output = tpl.render(Context({"request": request}))
        self.assertEqual(output.strip(), "NO")


class FeatureFlagsApiTests(TestCase):
    def setUp(self) -> None:
        self.user = _mk_user("api_user")
        for name in KNOWN_FLAGS:
            Flag.objects.get_or_create(name=name, defaults={"everyone": False})

    def test_get_returns_all_flags(self) -> None:
        client = APIClient()
        client.force_login(self.user)
        response = client.get("/api/v1/feature-flags/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(set(data.keys()), set(KNOWN_FLAGS))
        # Все false изначально.
        for name, enabled in data.items():
            self.assertFalse(enabled, f"{name} should be off")

    def test_reflects_enabled_flag(self) -> None:
        Flag.objects.filter(name=TWO_FACTOR_MANDATORY_FOR_ADMINS).update(everyone=True)
        client = APIClient()
        client.force_login(self.user)
        response = client.get("/api/v1/feature-flags/")
        data = response.json()
        self.assertTrue(data[TWO_FACTOR_MANDATORY_FOR_ADMINS])
        self.assertFalse(data[UI_V3B_DEFAULT])

    def test_requires_authentication(self) -> None:
        """Анонимам 401/403 (IsAuthenticated)."""
        client = APIClient()
        response = client.get("/api/v1/feature-flags/")
        self.assertIn(response.status_code, (401, 403))


class MigrationSeedTests(TestCase):
    """Убеждаемся что 4 начальных флага созданы data-миграцией."""

    def test_all_four_initial_flags_exist(self) -> None:
        for name in KNOWN_FLAGS:
            self.assertTrue(
                Flag.objects.filter(name=name).exists(),
                f"Initial flag {name} missing from migration seed",
            )

    def test_all_initial_flags_off(self) -> None:
        for name in KNOWN_FLAGS:
            flag = Flag.objects.get(name=name)
            self.assertFalse(
                flag.everyone,
                f"Initial flag {name} should be off by default",
            )

    def test_initial_flags_have_notes(self) -> None:
        """Каждый флаг должен иметь непустой note — критично для audit."""
        for name in KNOWN_FLAGS:
            flag = Flag.objects.get(name=name)
            self.assertTrue(flag.note, f"Flag {name} has empty note")
            self.assertGreater(
                len(flag.note),
                30,
                f"Flag {name} note should describe purpose (>30 chars)",
            )
