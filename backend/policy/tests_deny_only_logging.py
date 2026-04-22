"""W2.1.3a — deny-only policy logging tests (Q17).

Verify что `_log_decision()` в `policy.engine`:
- Skips allowed=True decisions (early return).
- Logs allowed=False (denied) decisions.
- Master flag POLICY_DECISION_LOGGING_ENABLED still controls overall on/off.
"""

from __future__ import annotations

from django.test import TestCase, override_settings

from accounts.models import User
from audit.models import ActivityEvent
from policy.engine import _log_decision, decide
from policy.models import PolicyConfig, PolicyRule


class DenyOnlyLoggingTests(TestCase):
    """Verify deny-only filter в _log_decision."""

    def setUp(self):
        self.manager = User.objects.create_user(
            username="dl_mgr", password="pass", role=User.Role.MANAGER
        )
        self.admin_user = User.objects.create_user(
            username="dl_admin", password="pass", role=User.Role.ADMIN
        )
        # Ensure enforce mode
        cfg = PolicyConfig.load()
        cfg.mode = PolicyConfig.Mode.ENFORCE
        cfg.save()

    def _count_policy_events(self):
        return ActivityEvent.objects.filter(entity_type="policy").count()

    @override_settings(POLICY_DECISION_LOGGING_ENABLED=True)
    def test_allowed_decision_not_logged(self):
        """Allowed decisions должны быть пропущены (early return after Q17)."""
        before = self._count_policy_events()

        # Admin has allow rule для ui:dashboard (baseline)
        decision = decide(user=self.admin_user, resource_type="page", resource="ui:dashboard")
        self.assertTrue(decision.allowed, "Admin should be allowed для dashboard")

        _log_decision(user=self.admin_user, decision=decision, context={})

        after = self._count_policy_events()
        self.assertEqual(before, after, "Allowed decisions should NOT be logged after Q17 filter")

    @override_settings(POLICY_DECISION_LOGGING_ENABLED=True)
    def test_denied_decision_is_logged(self):
        """Denied decisions должны быть залогированы."""
        # Create explicit deny rule для manager
        PolicyRule.objects.create(
            enabled=True,
            priority=1,
            subject_type=PolicyRule.SubjectType.ROLE,
            role=User.Role.MANAGER,
            resource_type=PolicyRule.ResourceType.ACTION,
            resource="ui:test:deny_only_logging",
            effect=PolicyRule.Effect.DENY,
        )
        before = self._count_policy_events()
        decision = decide(
            user=self.manager,
            resource_type="action",
            resource="ui:test:deny_only_logging",
        )
        self.assertFalse(decision.allowed, "Manager should be denied")

        _log_decision(user=self.manager, decision=decision, context={"path": "/test/"})

        after = self._count_policy_events()
        self.assertEqual(before + 1, after, "Denied decisions should be logged")

        # Check the logged event
        event = ActivityEvent.objects.filter(entity_type="policy").latest("created_at")
        self.assertEqual(event.meta["allowed"], False)
        self.assertEqual(event.meta["matched_effect"], PolicyRule.Effect.DENY)

    @override_settings(POLICY_DECISION_LOGGING_ENABLED=False)
    def test_master_flag_disables_even_denies(self):
        """Master flag OFF → ни allowed, ни denied не логируются."""
        PolicyRule.objects.create(
            enabled=True,
            priority=1,
            subject_type=PolicyRule.SubjectType.ROLE,
            role=User.Role.MANAGER,
            resource_type=PolicyRule.ResourceType.ACTION,
            resource="ui:test:master_flag_off",
            effect=PolicyRule.Effect.DENY,
        )
        before = self._count_policy_events()
        decision = decide(
            user=self.manager,
            resource_type="action",
            resource="ui:test:master_flag_off",
        )
        _log_decision(user=self.manager, decision=decision, context={})
        after = self._count_policy_events()
        self.assertEqual(before, after, "Master flag off should disable all policy logging")
