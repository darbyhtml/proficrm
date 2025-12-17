from django.contrib import admin

from .models import UiGlobalConfig


@admin.register(UiGlobalConfig)
class UiGlobalConfigAdmin(admin.ModelAdmin):
    list_display = ("id", "updated_at")
