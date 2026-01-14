from django.urls import path

from . import views

urlpatterns = [
    path("mail/settings/", views.mail_settings, name="mail_settings"),
    path("mail/signature/", views.mail_signature, name="mail_signature"),
    path("mail/progress/poll/", views.mail_progress_poll, name="mail_progress_poll"),
    path("mail/campaigns/", views.campaigns, name="campaigns"),
    path("mail/campaigns/new/", views.campaign_create, name="campaign_create"),
    path("mail/campaigns/pick/", views.campaign_pick, name="campaign_pick"),
    path("mail/campaigns/add-email/", views.campaign_add_email, name="campaign_add_email"),
    path("mail/campaigns/<uuid:campaign_id>/", views.campaign_detail, name="campaign_detail"),
    path("mail/campaigns/<uuid:campaign_id>/edit/", views.campaign_edit, name="campaign_edit"),
    path("mail/campaigns/<uuid:campaign_id>/delete/", views.campaign_delete, name="campaign_delete"),
    path("mail/campaigns/<uuid:campaign_id>/generate/", views.campaign_generate_recipients, name="campaign_generate_recipients"),
    path("mail/campaigns/<uuid:campaign_id>/recipients/reset/", views.campaign_recipients_reset, name="campaign_recipients_reset"),
    path("mail/campaigns/<uuid:campaign_id>/clear/", views.campaign_clear, name="campaign_clear"),
    path("mail/campaigns/<uuid:campaign_id>/recipients/add/", views.campaign_recipient_add, name="campaign_recipient_add"),
    path("mail/campaigns/<uuid:campaign_id>/recipients/bulk-delete/", views.campaign_recipients_bulk_delete, name="campaign_recipients_bulk_delete"),
    path("mail/campaigns/<uuid:campaign_id>/recipients/<uuid:recipient_id>/delete/", views.campaign_recipient_delete, name="campaign_recipient_delete"),
    path("mail/campaigns/<uuid:campaign_id>/test-send/", views.campaign_test_send, name="campaign_test_send"),
    path("mail/campaigns/<uuid:campaign_id>/send-step/", views.campaign_send_step, name="campaign_send_step"),
    path("unsubscribe/<str:token>/", views.unsubscribe, name="unsubscribe"),
]


