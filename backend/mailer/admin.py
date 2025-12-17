from django.contrib import admin

from .models import Campaign, CampaignRecipient, MailAccount, GlobalMailAccount, SendLog, Unsubscribe, UnsubscribeToken


@admin.register(MailAccount)
class MailAccountAdmin(admin.ModelAdmin):
    list_display = ("user", "from_email", "smtp_host", "smtp_port", "is_enabled", "updated_at")
    search_fields = ("user__username", "from_email", "smtp_username")
    list_filter = ("is_enabled",)


@admin.register(GlobalMailAccount)
class GlobalMailAccountAdmin(admin.ModelAdmin):
    list_display = ("smtp_host", "smtp_port", "smtp_username", "is_enabled", "updated_at")
    list_filter = ("is_enabled", "use_starttls")


@admin.register(Unsubscribe)
class UnsubscribeAdmin(admin.ModelAdmin):
    list_display = ("email", "created_at")
    search_fields = ("email",)


@admin.register(UnsubscribeToken)
class UnsubscribeTokenAdmin(admin.ModelAdmin):
    list_display = ("email", "token", "created_at")
    search_fields = ("email", "token")


@admin.register(Campaign)
class CampaignAdmin(admin.ModelAdmin):
    list_display = ("name", "created_by", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("name", "subject", "created_by__username")


@admin.register(CampaignRecipient)
class CampaignRecipientAdmin(admin.ModelAdmin):
    list_display = ("campaign", "email", "status", "updated_at")
    list_filter = ("status",)
    search_fields = ("email", "campaign__name")


@admin.register(SendLog)
class SendLogAdmin(admin.ModelAdmin):
    list_display = ("campaign", "recipient", "status", "created_at")
    list_filter = ("status", "provider")
    search_fields = ("campaign__name", "recipient__email", "error")

# Register your models here.
