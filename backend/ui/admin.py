from django.contrib import admin

from .models import AmoApiConfig, UiGlobalConfig


@admin.register(UiGlobalConfig)
class UiGlobalConfigAdmin(admin.ModelAdmin):
    list_display = ("id", "updated_at")
    readonly_fields = ("id",)


@admin.register(AmoApiConfig)
class AmoApiConfigAdmin(admin.ModelAdmin):
    list_display = ("domain", "is_connected", "updated_at")
    readonly_fields = ("id", "updated_at")
    
    def is_connected(self, obj):
        return obj.is_connected()
    is_connected.boolean = True
    is_connected.short_description = "Подключено"
