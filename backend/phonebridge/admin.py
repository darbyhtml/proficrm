from django.contrib import admin
from .models import MobileAppBuild, MobileAppQrToken


@admin.register(MobileAppBuild)
class MobileAppBuildAdmin(admin.ModelAdmin):
    list_display = ("version_name", "version_code", "env", "uploaded_at", "uploaded_by", "is_active", "get_file_size_display")
    list_filter = ("env", "is_active", "uploaded_at")
    search_fields = ("version_name", "version_code")
    readonly_fields = ("id", "sha256", "uploaded_at")
    fieldsets = (
        ("Основная информация", {
            "fields": ("env", "version_name", "version_code", "file", "is_active")
        }),
        ("Метаданные", {
            "fields": ("id", "sha256", "uploaded_at", "uploaded_by")
        }),
    )

    def save_model(self, request, obj, form, change):
        if not change:  # При создании
            obj.uploaded_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(MobileAppQrToken)
class MobileAppQrTokenAdmin(admin.ModelAdmin):
    list_display = ("user", "token_short", "created_at", "expires_at", "used_at", "is_valid_display")
    list_filter = ("used_at", "created_at", "expires_at")
    search_fields = ("user__username", "token")
    readonly_fields = ("id", "token", "created_at", "expires_at", "used_at", "ip_address", "user_agent")
    fieldsets = (
        ("Токен", {
            "fields": ("user", "token", "created_at", "expires_at", "used_at")
        }),
        ("Метаданные", {
            "fields": ("id", "ip_address", "user_agent")
        }),
    )

    def token_short(self, obj):
        return f"{obj.token[:16]}..." if obj.token else "-"
    token_short.short_description = "Токен"

    def is_valid_display(self, obj):
        return "Да" if obj.is_valid() else "Нет"
    is_valid_display.short_description = "Валиден"
