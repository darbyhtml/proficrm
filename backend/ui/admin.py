from django.contrib import admin

from .models import UiGlobalConfig, UiUserPreference


@admin.register(UiGlobalConfig)
class UiGlobalConfigAdmin(admin.ModelAdmin):
    list_display = ("id", "updated_at")
    readonly_fields = ("id",)


# AmoApiConfigAdmin removed 2026-04-21 (AmoApiConfig model deleted).


@admin.register(UiUserPreference)
class UiUserPreferenceAdmin(admin.ModelAdmin):
    list_display = ("user", "font_scale", "updated_at")
    search_fields = ("user__username", "user__first_name", "user__last_name")
    list_select_related = ("user",)
