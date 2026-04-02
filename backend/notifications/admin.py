from django.contrib import admin

from .models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("created_at", "user", "kind", "title", "is_read")
    list_filter = ("kind", "is_read")
    search_fields = ("title", "body", "user__username", "user__email")


from .models import CrmAnnouncement, CrmAnnouncementRead

@admin.register(CrmAnnouncement)
class CrmAnnouncementAdmin(admin.ModelAdmin):
    list_display = ("created_at", "announcement_type", "title", "created_by", "is_active", "read_count")
    list_filter = ("announcement_type", "is_active")
    search_fields = ("title", "body")
    readonly_fields = ("created_at", "created_by")

    def read_count(self, obj):
        return obj.reads.count()
    read_count.short_description = "Прочитали"
