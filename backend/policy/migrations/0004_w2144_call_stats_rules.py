"""W2.1.4.4 seed PolicyRule: call stats role-mixed access.

Resources ui:settings:calls:stats + ui:settings:calls:manager_detail — НЕ
admin-only (unlike остальные ui:settings:*). Blanket admin-only default
(engine.baseline_allowed_for_role) daёт DENY всем non-admin, поэтому нужны
explicit ALLOW rules для team leads.

User decision (W2.1.4.4 prompt): allow MANAGER + SALES_HEAD +
BRANCH_DIRECTOR + GROUP_MANAGER + ADMIN, deny TENDERIST.

TENDERIST deny — unchanged (falls to blanket default).
ADMIN — redundant с blanket но explicit для ясности + audit.
"""

from __future__ import annotations

from django.db import migrations


CALL_RESOURCES = ("ui:settings:calls:stats", "ui:settings:calls:manager_detail")
ALLOWED_ROLES = ("manager", "sales_head", "branch_director", "group_manager", "admin")


def seed_call_stats_rules(apps, schema_editor):
    PolicyRule = apps.get_model("policy", "PolicyRule")
    for resource in CALL_RESOURCES:
        for role in ALLOWED_ROLES:
            PolicyRule.objects.update_or_create(
                subject_type="role",
                role=role,
                resource_type="page",
                resource=resource,
                defaults={
                    "effect": "allow",
                    "enabled": True,
                    "priority": 100,
                },
            )


def remove_call_stats_rules(apps, schema_editor):
    PolicyRule = apps.get_model("policy", "PolicyRule")
    PolicyRule.objects.filter(
        resource__in=CALL_RESOURCES,
        subject_type="role",
        role__in=ALLOWED_ROLES,
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("policy", "0003_policyconfig_livechat_escalation"),
    ]

    operations = [
        migrations.RunPython(seed_call_stats_rules, remove_call_stats_rules),
    ]
