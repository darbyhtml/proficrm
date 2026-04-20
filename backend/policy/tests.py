"""
Тесты для policy/engine.py.

Покрывает:
- decide(): суперпользователь, неавторизованный, неактивный
- baseline по ролям: страницы, actions, admin-only ресурсы
- PolicyRule ALLOW/DENY перекрывают baseline
- user-specific правила приоритетнее role-правил
- enforce(): OBSERVE_ONLY не бросает, ENFORCE бросает PermissionDenied при deny
- baseline_allowed_for_role(): проверка ключевых resource/role комбинаций
"""

from django.test import TestCase
from django.core.exceptions import PermissionDenied

from accounts.models import User
from policy.models import PolicyConfig, PolicyRule
from policy.engine import decide, enforce, baseline_allowed_for_role


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_user(username, role, is_superuser=False, is_active=True):
    u = User.objects.create_user(username=username, password="pass", role=role)
    u.is_superuser = is_superuser
    u.is_active = is_active
    u.save()
    return u


def _set_mode(mode):
    cfg = PolicyConfig.load()
    cfg.mode = mode
    cfg.save()


def _allow_rule(subject_type, resource, resource_type, *, role=None, user=None, priority=100):
    return PolicyRule.objects.create(
        subject_type=subject_type,
        role=role or "",
        user=user,
        resource_type=resource_type,
        resource=resource,
        effect=PolicyRule.Effect.ALLOW,
        enabled=True,
        priority=priority,
    )


def _deny_rule(subject_type, resource, resource_type, *, role=None, user=None, priority=100):
    return PolicyRule.objects.create(
        subject_type=subject_type,
        role=role or "",
        user=user,
        resource_type=resource_type,
        resource=resource,
        effect=PolicyRule.Effect.DENY,
        enabled=True,
        priority=priority,
    )


# ---------------------------------------------------------------------------
# decide() — базовые случаи
# ---------------------------------------------------------------------------


class DecideBasicTest(TestCase):
    def setUp(self):
        _set_mode(PolicyConfig.Mode.ENFORCE)

    def test_superuser_always_allowed(self):
        su = _make_user("su", User.Role.MANAGER, is_superuser=True)
        d = decide(user=su, resource_type=PolicyRule.ResourceType.PAGE, resource="ui:settings")
        self.assertTrue(d.allowed)
        self.assertEqual(d.matched_effect, "superuser_allow")

    def test_unauthenticated_denied(self):
        from django.contrib.auth.models import AnonymousUser

        anon = AnonymousUser()
        d = decide(user=anon, resource_type=PolicyRule.ResourceType.PAGE, resource="ui:dashboard")
        self.assertFalse(d.allowed)

    def test_inactive_user_denied(self):
        u = _make_user("inactive", User.Role.MANAGER, is_active=False)
        d = decide(user=u, resource_type=PolicyRule.ResourceType.PAGE, resource="ui:dashboard")
        self.assertFalse(d.allowed)

    def test_none_user_denied(self):
        d = decide(user=None, resource_type=PolicyRule.ResourceType.PAGE, resource="ui:dashboard")
        self.assertFalse(d.allowed)


# ---------------------------------------------------------------------------
# baseline по ролям — страницы
# ---------------------------------------------------------------------------


class BaselinePageTest(TestCase):
    def test_all_roles_can_access_dashboard(self):
        for role in User.Role.values:
            u = _make_user(f"u_{role}", role)
            d = decide(user=u, resource_type=PolicyRule.ResourceType.PAGE, resource="ui:dashboard")
            self.assertTrue(d.allowed, f"role={role} should access dashboard")

    def test_analytics_allowed_for_managers_and_above(self):
        allowed_roles = [
            User.Role.ADMIN,
            User.Role.GROUP_MANAGER,
            User.Role.BRANCH_DIRECTOR,
            User.Role.SALES_HEAD,
        ]
        for role in allowed_roles:
            u = _make_user(f"u_anl_{role}", role)
            d = decide(user=u, resource_type=PolicyRule.ResourceType.PAGE, resource="ui:analytics")
            self.assertTrue(d.allowed, f"role={role} should access analytics")

    def test_analytics_denied_for_manager(self):
        u = _make_user("mgr_anl", User.Role.MANAGER)
        d = decide(user=u, resource_type=PolicyRule.ResourceType.PAGE, resource="ui:analytics")
        self.assertFalse(d.allowed)

    def test_settings_page_admin_only(self):
        u_admin = _make_user("admin_s", User.Role.ADMIN)
        u_mgr = _make_user("mgr_s", User.Role.MANAGER)
        self.assertTrue(
            decide(
                user=u_admin, resource_type=PolicyRule.ResourceType.PAGE, resource="ui:settings"
            ).allowed
        )
        self.assertFalse(
            decide(
                user=u_mgr, resource_type=PolicyRule.ResourceType.PAGE, resource="ui:settings"
            ).allowed
        )


# ---------------------------------------------------------------------------
# baseline по ролям — actions
# ---------------------------------------------------------------------------


class BaselineActionTest(TestCase):
    def test_companies_create_allowed_for_manager(self):
        u = _make_user("mgr_cc", User.Role.MANAGER)
        d = decide(
            user=u, resource_type=PolicyRule.ResourceType.ACTION, resource="ui:companies:create"
        )
        self.assertTrue(d.allowed)

    def test_companies_delete_denied_for_manager(self):
        u = _make_user("mgr_cd", User.Role.MANAGER)
        d = decide(
            user=u, resource_type=PolicyRule.ResourceType.ACTION, resource="ui:companies:delete"
        )
        self.assertFalse(d.allowed)

    def test_companies_delete_allowed_for_admin(self):
        u = _make_user("admin_cd", User.Role.ADMIN)
        d = decide(
            user=u, resource_type=PolicyRule.ResourceType.ACTION, resource="ui:companies:delete"
        )
        self.assertTrue(d.allowed)

    def test_bulk_reassign_admin_only(self):
        u_admin = _make_user("admin_br", User.Role.ADMIN)
        u_head = _make_user("head_br", User.Role.SALES_HEAD)
        self.assertTrue(
            decide(
                user=u_admin,
                resource_type=PolicyRule.ResourceType.ACTION,
                resource="ui:tasks:bulk_reassign",
            ).allowed
        )
        self.assertFalse(
            decide(
                user=u_head,
                resource_type=PolicyRule.ResourceType.ACTION,
                resource="ui:tasks:bulk_reassign",
            ).allowed
        )

    def test_smtp_settings_admin_only(self):
        u_admin = _make_user("admin_sm", User.Role.ADMIN)
        u_mgr = _make_user("mgr_sm", User.Role.MANAGER)
        self.assertTrue(
            decide(
                user=u_admin,
                resource_type=PolicyRule.ResourceType.ACTION,
                resource="ui:mail:smtp_settings",
            ).allowed
        )
        self.assertFalse(
            decide(
                user=u_mgr,
                resource_type=PolicyRule.ResourceType.ACTION,
                resource="ui:mail:smtp_settings",
            ).allowed
        )

    def test_phone_endpoints_allowed_for_all_authenticated(self):
        u = _make_user("mgr_ph", User.Role.MANAGER)
        d = decide(
            user=u, resource_type=PolicyRule.ResourceType.ACTION, resource="phone:calls:pull"
        )
        self.assertTrue(d.allowed)


# ---------------------------------------------------------------------------
# PolicyRule перекрывает baseline
# ---------------------------------------------------------------------------


class PolicyRuleOverrideTest(TestCase):
    def setUp(self):
        _set_mode(PolicyConfig.Mode.ENFORCE)
        self.mgr = _make_user("mgr_ov", User.Role.MANAGER)

    def test_deny_rule_overrides_baseline_allow(self):
        # Менеджер по baseline может создавать компании — правило DENY это перекрывает
        _deny_rule(
            PolicyRule.SubjectType.ROLE,
            "ui:companies:create",
            PolicyRule.ResourceType.ACTION,
            role=User.Role.MANAGER,
        )
        d = decide(
            user=self.mgr,
            resource_type=PolicyRule.ResourceType.ACTION,
            resource="ui:companies:create",
        )
        self.assertFalse(d.allowed)

    def test_allow_rule_overrides_baseline_deny(self):
        # Менеджер по baseline не может удалять компании — правило ALLOW это перекрывает
        _allow_rule(
            PolicyRule.SubjectType.ROLE,
            "ui:companies:delete",
            PolicyRule.ResourceType.ACTION,
            role=User.Role.MANAGER,
        )
        d = decide(
            user=self.mgr,
            resource_type=PolicyRule.ResourceType.ACTION,
            resource="ui:companies:delete",
        )
        self.assertTrue(d.allowed)

    def test_user_rule_takes_priority_over_role_rule(self):
        # Роль DENY, но конкретный пользователь ALLOW — должен пройти
        _deny_rule(
            PolicyRule.SubjectType.ROLE,
            "ui:companies:create",
            PolicyRule.ResourceType.ACTION,
            role=User.Role.MANAGER,
        )
        _allow_rule(
            PolicyRule.SubjectType.USER,
            "ui:companies:create",
            PolicyRule.ResourceType.ACTION,
            user=self.mgr,
        )
        d = decide(
            user=self.mgr,
            resource_type=PolicyRule.ResourceType.ACTION,
            resource="ui:companies:create",
        )
        self.assertTrue(d.allowed)

    def test_disabled_rule_ignored(self):
        # Правило с enabled=False не должно влиять
        PolicyRule.objects.create(
            subject_type=PolicyRule.SubjectType.ROLE,
            role=User.Role.MANAGER,
            resource_type=PolicyRule.ResourceType.ACTION,
            resource="ui:companies:create",
            effect=PolicyRule.Effect.DENY,
            enabled=False,
        )
        d = decide(
            user=self.mgr,
            resource_type=PolicyRule.ResourceType.ACTION,
            resource="ui:companies:create",
        )
        self.assertTrue(d.allowed)  # baseline разрешает


# ---------------------------------------------------------------------------
# enforce() — OBSERVE_ONLY vs ENFORCE
# ---------------------------------------------------------------------------


class EnforceTest(TestCase):
    def setUp(self):
        self.mgr = _make_user("mgr_enf", User.Role.MANAGER)

    def test_observe_only_does_not_raise_on_deny(self):
        _set_mode(PolicyConfig.Mode.OBSERVE_ONLY)
        _deny_rule(
            PolicyRule.SubjectType.ROLE,
            "ui:companies:create",
            PolicyRule.ResourceType.ACTION,
            role=User.Role.MANAGER,
        )
        # Не должен бросать PermissionDenied в observe_only
        decision = enforce(
            user=self.mgr,
            resource_type=PolicyRule.ResourceType.ACTION,
            resource="ui:companies:create",
            log=False,
        )
        self.assertFalse(decision.allowed)

    def test_enforce_mode_raises_on_deny(self):
        _set_mode(PolicyConfig.Mode.ENFORCE)
        _deny_rule(
            PolicyRule.SubjectType.ROLE,
            "ui:companies:delete",
            PolicyRule.ResourceType.ACTION,
            role=User.Role.MANAGER,
        )
        with self.assertRaises(PermissionDenied):
            enforce(
                user=self.mgr,
                resource_type=PolicyRule.ResourceType.ACTION,
                resource="ui:companies:delete",
                log=False,
            )

    def test_enforce_mode_does_not_raise_on_allow(self):
        _set_mode(PolicyConfig.Mode.ENFORCE)
        decision = enforce(
            user=self.mgr,
            resource_type=PolicyRule.ResourceType.ACTION,
            resource="ui:companies:create",
            log=False,
        )
        self.assertTrue(decision.allowed)

    def test_superuser_never_raises(self):
        _set_mode(PolicyConfig.Mode.ENFORCE)
        su = _make_user("su_enf", User.Role.MANAGER, is_superuser=True)
        _deny_rule(
            PolicyRule.SubjectType.ROLE,
            "ui:companies:delete",
            PolicyRule.ResourceType.ACTION,
            role=User.Role.MANAGER,
        )
        # Суперюзер игнорирует правила
        decision = enforce(
            user=su,
            resource_type=PolicyRule.ResourceType.ACTION,
            resource="ui:companies:delete",
            log=False,
        )
        self.assertTrue(decision.allowed)


# ---------------------------------------------------------------------------
# baseline_allowed_for_role() — публичная функция без User объекта
# ---------------------------------------------------------------------------


class BaselineAllowedForRoleTest(TestCase):
    def test_superuser_always_true(self):
        self.assertTrue(
            baseline_allowed_for_role(
                role="",
                resource_type=PolicyRule.ResourceType.PAGE,
                resource_key="ui:settings",
                is_superuser=True,
            )
        )

    def test_manager_cannot_access_settings_page(self):
        self.assertFalse(
            baseline_allowed_for_role(
                role=User.Role.MANAGER,
                resource_type=PolicyRule.ResourceType.PAGE,
                resource_key="ui:settings",
            )
        )

    def test_admin_can_access_settings_page(self):
        self.assertTrue(
            baseline_allowed_for_role(
                role=User.Role.ADMIN,
                resource_type=PolicyRule.ResourceType.PAGE,
                resource_key="ui:settings",
            )
        )

    def test_delete_request_create_manager_only(self):
        self.assertTrue(
            baseline_allowed_for_role(
                role=User.Role.MANAGER,
                resource_type=PolicyRule.ResourceType.ACTION,
                resource_key="ui:companies:delete_request:create",
            )
        )
        self.assertFalse(
            baseline_allowed_for_role(
                role=User.Role.ADMIN,
                resource_type=PolicyRule.ResourceType.ACTION,
                resource_key="ui:companies:delete_request:create",
            )
        )
