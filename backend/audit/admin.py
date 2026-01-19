from django.contrib import admin

from .models import ActivityEvent, ErrorLog


@admin.register(ActivityEvent)
class ActivityEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "actor", "verb", "entity_type", "entity_id", "company_id", "message")
    list_filter = ("verb", "entity_type")
    search_fields = ("message", "entity_id", "actor__username")


@admin.register(ErrorLog)
class ErrorLogAdmin(admin.ModelAdmin):
    list_display = ["created_at", "level", "exception_type", "path", "user", "resolved"]
    list_filter = ["level", "resolved", "created_at", "method"]
    search_fields = ["message", "exception_type", "path", "traceback"]
    readonly_fields = ["id", "created_at", "traceback", "request_data"]
    date_hierarchy = "created_at"
    fieldsets = (
        ("Основная информация", {
            "fields": ("level", "message", "exception_type", "traceback")
        }),
        ("Запрос", {
            "fields": ("path", "method", "user", "ip_address", "user_agent", "request_data")
        }),
        ("Статус", {
            "fields": ("resolved", "resolved_at", "resolved_by", "notes")
        }),
    )

# Register your models here.
