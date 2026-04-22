"""W2.1.5 — Verify inline enforce() codification.

39 views now routed через @policy_required decorator. Inline enforce()
preserved как defense-in-depth layer.
"""

from __future__ import annotations

import inspect

from django.test import TestCase


class W215DefenseInDepthTest(TestCase):
    """Source-level verify: inline enforce() calls preserved alongside
    new @policy_required decorators."""

    # Files + endpoint functions с @policy_required в W2.1.5
    CODIFIED = [
        # (module path, function name, expected_resource substring)
        ("mailer.views.polling", "mail_progress_poll", "ui:mail:progress:poll"),
        ("mailer.views.polling", "campaign_progress_poll", "ui:mail:campaigns:detail"),
        ("mailer.views.campaigns.list_detail", "campaigns", "ui:mail:campaigns"),
        (
            "mailer.views.campaigns.list_detail",
            "campaign_detail",
            "ui:mail:campaigns:detail",
        ),
        ("mailer.views.unsubscribe", "mail_unsubscribes_list", "ui:mail:unsubscribes:list"),
        (
            "mailer.views.unsubscribe",
            "mail_unsubscribes_delete",
            "ui:mail:unsubscribes:delete",
        ),
        ("mailer.views.unsubscribe", "mail_unsubscribes_clear", "ui:mail:unsubscribes:clear"),
        (
            "mailer.views.campaigns.templates_views",
            "campaign_save_as_template",
            "ui:mail:campaigns:create",
        ),
        (
            "mailer.views.campaigns.templates_views",
            "campaign_create_from_template",
            "ui:mail:campaigns:create",
        ),
        (
            "mailer.views.campaigns.templates_views",
            "campaign_templates",
            "ui:mail:campaigns",
        ),
        ("mailer.views.sending", "campaign_start", "ui:mail:campaigns:start"),
        ("mailer.views.sending", "campaign_pause", "ui:mail:campaigns:pause"),
        ("mailer.views.sending", "campaign_resume", "ui:mail:campaigns:resume"),
        ("mailer.views.sending", "campaign_test_send", "ui:mail:campaigns:test_send"),
        ("mailer.views.campaigns.crud", "campaign_create", "ui:mail:campaigns:create"),
        ("mailer.views.campaigns.crud", "campaign_edit", "ui:mail:campaigns:edit"),
        ("mailer.views.campaigns.crud", "campaign_delete", "ui:mail:campaigns:delete"),
        ("mailer.views.campaigns.crud", "campaign_clone", "ui:mail:campaigns:create"),
        (
            "mailer.views.campaigns.files",
            "campaign_html_preview",
            "ui:mail:campaigns:detail",
        ),
        (
            "mailer.views.campaigns.files",
            "campaign_attachment_download",
            "ui:mail:campaigns:attachment:download",
        ),
        (
            "mailer.views.campaigns.files",
            "campaign_attachment_delete",
            "ui:mail:campaigns:edit",
        ),
        (
            "mailer.views.campaigns.files",
            "campaign_export_failed",
            "ui:mail:campaigns:export_failed",
        ),
        (
            "mailer.views.campaigns.files",
            "campaign_retry_failed",
            "ui:mail:campaigns:retry_failed",
        ),
        ("notifications.views", "mark_all_read", "ui:notifications:mark_all_read"),
        ("notifications.views", "mark_read", "ui:notifications:mark_read"),
        ("notifications.views", "poll", "ui:notifications:poll"),
        ("notifications.views", "all_notifications", "ui:notifications:all"),
        ("notifications.views", "all_reminders", "ui:notifications:reminders"),
        ("mailer.views.settings", "mail_signature", "ui:mail:signature"),
        ("mailer.views.settings", "mail_settings", "ui:mail:settings"),
        ("mailer.views.settings", "mail_admin", "ui:mail:admin"),
        ("mailer.views.settings", "mail_quota_poll", "ui:mail:quota:poll"),
        ("mailer.views.recipients", "campaign_pick", "ui:mail:campaigns:pick"),
        ("mailer.views.recipients", "campaign_add_email", "ui:mail:campaigns:add_email"),
        (
            "mailer.views.recipients",
            "campaign_recipient_add",
            "ui:mail:campaigns:recipients:add",
        ),
        (
            "mailer.views.recipients",
            "campaign_recipient_delete",
            "ui:mail:campaigns:recipients:delete",
        ),
        (
            "mailer.views.recipients",
            "campaign_recipients_bulk_delete",
            "ui:mail:campaigns:recipients:bulk_delete",
        ),
        (
            "mailer.views.recipients",
            "campaign_generate_recipients",
            "ui:mail:campaigns:recipients:generate",
        ),
        ("mailer.views.recipients", "campaign_clear", "ui:mail:campaigns:clear"),
    ]

    def test_all_codified_endpoints_have_decorator(self):
        """Each of 39 codified views имеет @policy_required в source."""
        import importlib

        for module_path, func_name, expected_resource in self.CODIFIED:
            module = importlib.import_module(module_path)
            func = getattr(module, func_name, None)
            self.assertIsNotNone(func, f"{module_path}.{func_name} не найден")
            source = inspect.getsource(func)
            self.assertIn(
                "@policy_required",
                source,
                f"{module_path}.{func_name}: @policy_required missing",
            )
            self.assertIn(
                expected_resource,
                source,
                f"{module_path}.{func_name}: resource {expected_resource} missing",
            )

    def test_all_codified_endpoints_have_inline_enforce(self):
        """Defense-in-depth: inline enforce() preserved в каждом codified view."""
        import importlib

        for module_path, func_name, _ in self.CODIFIED:
            module = importlib.import_module(module_path)
            func = getattr(module, func_name)
            source = inspect.getsource(func)
            self.assertIn(
                "enforce(",
                source,
                f"{module_path}.{func_name}: inline enforce() missing",
            )

    def test_count_matches_inventory(self):
        """Inventory docs: 39 decorated views."""
        self.assertEqual(len(self.CODIFIED), 39, "CODIFIED list должен содержать ровно 39 entries")


class W215PhoneBridgeExclusionTest(TestCase):
    """phonebridge/api.py APIView classes deferred — документируем exclusion."""

    def test_phonebridge_api_has_enforce_no_decorator(self):
        """phonebridge APIView subclasses retain inline enforce(),
        не имеют @policy_required (per W2.1.5 scope adjustment)."""
        import inspect as _ins

        from phonebridge.api import (
            DeviceHeartbeatView,
            QrTokenCreateView,
            QrTokenExchangeView,
            RegisterDeviceView,
        )

        for view_cls in [
            RegisterDeviceView,
            DeviceHeartbeatView,
            QrTokenCreateView,
            QrTokenExchangeView,
        ]:
            source = _ins.getsource(view_cls)
            # Inline enforce() должен быть
            self.assertIn("enforce(", source, f"{view_cls.__name__}: enforce missing")
