from django.contrib import admin

from .models import ActivityEvent


@admin.register(ActivityEvent)
class ActivityEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "actor", "verb", "entity_type", "entity_id", "company_id", "message")
    list_filter = ("verb", "entity_type")
    search_fields = ("message", "entity_id", "actor__username")

# Register your models here.
