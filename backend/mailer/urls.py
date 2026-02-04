from django.urls import path

from . import views

urlpatterns = [
    path("mail/settings/", views.mail_settings, name="mail_settings"),
    path("mail/admin/", views.mail_admin, name="mail_admin"),
    path("mail/signature/", views.mail_signature, name="mail_signature"),
    path("mail/progress/poll/", views.mail_progress_poll, name="mail_progress_poll"),
    path("mail/quota/poll/", views.mail_quota_poll, name="mail_quota_poll"),
    path("mail/unsubscribes/list/", views.mail_unsubscribes_list, name="mail_unsubscribes_list"),
    path("mail/unsubscribes/delete/", views.mail_unsubscribes_delete, name="mail_unsubscribes_delete"),
    path("mail/unsubscribes/clear/", views.mail_unsubscribes_clear, name="mail_unsubscribes_clear"),
    path("mail/campaigns/", views.campaigns, name="campaigns"),
    path("mail/campaigns/new/", views.campaign_create, name="campaign_create"),
    path("mail/campaigns/pick/", views.campaign_pick, name="campaign_pick"),
    path("mail/campaigns/add-email/", views.campaign_add_email, name="campaign_add_email"),
    path("mail/campaigns/<uuid:campaign_id>/", views.campaign_detail, name="campaign_detail"),
    path("mail/campaigns/<uuid:campaign_id>/progress/poll/", views.campaign_progress_poll, name="campaign_progress_poll"),
    path("mail/campaigns/<uuid:campaign_id>/edit/", views.campaign_edit, name="campaign_edit"),
    path("mail/campaigns/<uuid:campaign_id>/attachment/download/", views.campaign_attachment_download, name="campaign_attachment_download"),
    path("mail/campaigns/<uuid:campaign_id>/attachment/delete/", views.campaign_attachment_delete, name="campaign_attachment_delete"),
    path("mail/campaigns/<uuid:campaign_id>/delete/", views.campaign_delete, name="campaign_delete"),
    path("mail/campaigns/<uuid:campaign_id>/generate/", views.campaign_generate_recipients, name="campaign_generate_recipients"),
    path("mail/campaigns/<uuid:campaign_id>/recipients/reset/", views.campaign_recipients_reset, name="campaign_recipients_reset"),
    path("mail/campaigns/<uuid:campaign_id>/clear/", views.campaign_clear, name="campaign_clear"),
    path("mail/campaigns/<uuid:campaign_id>/recipients/add/", views.campaign_recipient_add, name="campaign_recipient_add"),
    path("mail/campaigns/<uuid:campaign_id>/recipients/bulk-delete/", views.campaign_recipients_bulk_delete, name="campaign_recipients_bulk_delete"),
    path("mail/campaigns/<uuid:campaign_id>/recipients/<uuid:recipient_id>/delete/", views.campaign_recipient_delete, name="campaign_recipient_delete"),
    path("mail/campaigns/<uuid:campaign_id>/test-send/", views.campaign_test_send, name="campaign_test_send"),
    path("mail/campaigns/<uuid:campaign_id>/send-step/", views.campaign_send_step, name="campaign_send_step"),
    path("mail/campaigns/<uuid:campaign_id>/start/", views.campaign_start, name="campaign_start"),
    path("mail/campaigns/<uuid:campaign_id>/pause/", views.campaign_pause, name="campaign_pause"),
    path("mail/campaigns/<uuid:campaign_id>/resume/", views.campaign_resume, name="campaign_resume"),
    path("unsubscribe/<str:token>/", views.unsubscribe, name="unsubscribe"),
]


