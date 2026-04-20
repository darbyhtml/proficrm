from __future__ import annotations

from django.contrib import admin

from .models import PolicyConfig, PolicyRule


@admin.register(PolicyConfig)
class PolicyConfigAdmin(admin.ModelAdmin):
    list_display = ("id", "mode", "updated_at")
    fieldsets = (
        (None, {"fields": ("mode",)}),
        (
            "Live-chat эскалация",
            {
                "fields": ("livechat_escalation",),
                "description": (
                    "Пороги в минутах: warn_min, urgent_min, rop_alert_min, pool_return_min"
                ),
            },
        ),
    )


@admin.register(PolicyRule)
class PolicyRuleAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "enabled",
        "priority",
        "subject_type",
        "role",
        "user",
        "resource_type",
        "resource",
        "effect",
        "updated_at",
    )
    list_filter = ("enabled", "subject_type", "resource_type", "effect", "role")
    search_fields = ("resource", "role", "user__username", "user__first_name", "user__last_name")
    ordering = ("priority", "-updated_at")
