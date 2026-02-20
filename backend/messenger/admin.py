from django.contrib import admin

from accounts.models import User
from .models import (
    AgentProfile,
    CannedResponse,
    Channel,
    Conversation,
    Inbox,
    Message,
    MessageAttachment,
    RoutingRule,
)


class AdminOnlyMixin:
    """
    Ограничивает доступ к ModelAdmin только администраторам CRM.

    Правило:
    - доступ разрешён, если пользователь is_superuser или role == ADMIN;
    - иначе любые операции (просмотр/изменение/создание/удаление) запрещены.
    """

    def _is_crm_admin(self, user) -> bool:
        if not user or not user.is_authenticated or not user.is_active:
            return False
        if getattr(user, "is_superuser", False):
            return True
        return getattr(user, "role", None) == User.Role.ADMIN

    def has_module_permission(self, request) -> bool:
        return self._is_crm_admin(request.user)

    def has_view_permission(self, request, obj=None) -> bool:
        return self._is_crm_admin(request.user)

    def has_add_permission(self, request) -> bool:
        return self._is_crm_admin(request.user)

    def has_change_permission(self, request, obj=None) -> bool:
        return self._is_crm_admin(request.user)

    def has_delete_permission(self, request, obj=None) -> bool:
        return self._is_crm_admin(request.user)


@admin.register(Inbox)
class InboxAdmin(AdminOnlyMixin, admin.ModelAdmin):
    list_display = ("name", "branch", "is_active", "created_at")
    list_filter = ("is_active", "branch")
    search_fields = ("name", "widget_token")
    readonly_fields = ("widget_token", "created_at")


@admin.register(Channel)
class ChannelAdmin(AdminOnlyMixin, admin.ModelAdmin):
    list_display = ("type", "inbox", "is_active")
    list_filter = ("type", "is_active")
    search_fields = ("inbox__name",)


@admin.register(Conversation)
class ConversationAdmin(AdminOnlyMixin, admin.ModelAdmin):
    list_display = ("id", "inbox", "branch", "contact", "status", "priority", "assignee", "last_activity_at")
    list_filter = ("status", "priority", "branch", "inbox")
    search_fields = ("id", "contact__name", "contact__email", "contact__phone")
    readonly_fields = ("branch", "created_at", "last_activity_at")


@admin.register(Message)
class MessageAdmin(AdminOnlyMixin, admin.ModelAdmin):
    list_display = ("id", "conversation", "direction", "created_at")
    list_filter = ("direction", "created_at")
    search_fields = ("body",)
    readonly_fields = ("created_at", "delivered_at")


@admin.register(MessageAttachment)
class MessageAttachmentAdmin(AdminOnlyMixin, admin.ModelAdmin):
    list_display = ("id", "message", "original_name", "content_type", "size", "created_at")
    list_filter = ("content_type",)
    search_fields = ("original_name",)
    readonly_fields = ("size", "created_at")


@admin.register(RoutingRule)
class RoutingRuleAdmin(AdminOnlyMixin, admin.ModelAdmin):
    list_display = ("name", "branch", "inbox", "priority", "is_fallback", "is_active")
    list_filter = ("branch", "inbox", "is_fallback", "is_active")
    filter_horizontal = ("regions",)


@admin.register(CannedResponse)
class CannedResponseAdmin(AdminOnlyMixin, admin.ModelAdmin):
    list_display = ("title", "branch", "created_by", "created_at")
    list_filter = ("branch",)
    search_fields = ("title", "body")
    readonly_fields = ("created_by", "created_at")


@admin.register(AgentProfile)
class AgentProfileAdmin(AdminOnlyMixin, admin.ModelAdmin):
    list_display = ("user", "display_name", "status", "updated_at")
    list_filter = ("status",)
    search_fields = ("user__username", "display_name")

