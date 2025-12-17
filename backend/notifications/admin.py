from django.contrib import admin

from .models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("created_at", "user", "kind", "title", "is_read")
    list_filter = ("kind", "is_read")
    search_fields = ("title", "body", "user__username", "user__email")

# Register your models here.
