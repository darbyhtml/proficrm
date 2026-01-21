from __future__ import annotations

import uuid

from django.test import TestCase
from django.test.utils import override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from mailer.forms import CampaignForm, EmailSignatureForm
from mailer.models import Campaign, CampaignQueue, CampaignRecipient, Unsubscribe, UnsubscribeToken
from mailer.utils import sanitize_email_html


@override_settings(SECURE_SSL_REDIRECT=False)
class MailerSafetyAndUnsubTests(TestCase):
    def test_sanitize_email_html_removes_scripts_and_js_protocol(self):
        raw = '<div onclick="alert(1)"><script>alert(2)</script><a href="javascript:alert(3)">x</a></div>'
        cleaned = sanitize_email_html(raw)
        self.assertNotIn("<script", cleaned.lower())
        self.assertNotIn("onclick", cleaned.lower())
        self.assertNotIn("javascript:", cleaned.lower())

    def test_email_signature_form_sanitizes_html(self):
        form = EmailSignatureForm(data={"signature_html": "<script>alert(1)</script><b>ok</b>"})
        self.assertTrue(form.is_valid())
        self.assertIn("<b>ok</b>", form.cleaned_data["signature_html"])
        self.assertNotIn("<script", form.cleaned_data["signature_html"].lower())

    def test_campaign_form_sanitizes_body_html(self):
        form = CampaignForm(data={"name": "n", "subject": "s", "sender_name": "x", "body_html": "<script>1</script><p>ok</p>"})
        self.assertTrue(form.is_valid())
        self.assertIn("<p>ok</p>", form.cleaned_data["body_html"])
        self.assertNotIn("<script", form.cleaned_data["body_html"].lower())

    def test_unsubscribe_view_get_and_post(self):
        email = "test@example.com"
        t = UnsubscribeToken.objects.create(email=email, token="tok123")

        # GET
        resp = self.client.get(reverse("unsubscribe", kwargs={"token": t.token}))
        self.assertEqual(resp.status_code, 200)
        u = Unsubscribe.objects.get(email=email)
        self.assertEqual(u.source, "token")
        self.assertIn(u.reason, ("user", "unsubscribe", ""))  # reason может быть "user" для GET
        self.assertIsNotNone(u.last_seen_at)

        # POST (one-click)
        resp = self.client.post(reverse("unsubscribe", kwargs={"token": t.token}), data={"List-Unsubscribe": "One-Click"})
        self.assertEqual(resp.status_code, 200)
        u.refresh_from_db()
        self.assertEqual(u.source, "token")
        self.assertEqual(u.reason, "unsubscribe")
        self.assertIsNotNone(u.last_seen_at)


@override_settings(SECURE_SSL_REDIRECT=False)
class MailerQueueConsistencyTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="m", password="pass", role=User.Role.MANAGER, email="m@example.com")
        self.client.force_login(self.user)

    def _make_campaign(self) -> Campaign:
        return Campaign.objects.create(
            created_by=self.user,
            name="Camp",
            subject="Subj",
            body_html="<p>hi</p>",
            body_text="hi",
            sender_name="CRM",
            status=Campaign.Status.DRAFT,
        )

    def test_campaign_clear_cancels_queue_entry(self):
        camp = self._make_campaign()
        CampaignRecipient.objects.create(campaign=camp, email="a@example.com", status=CampaignRecipient.Status.PENDING)
        q = CampaignQueue.objects.create(campaign=camp, status=CampaignQueue.Status.PENDING, priority=0)

        resp = self.client.post(reverse("campaign_clear", kwargs={"campaign_id": camp.id}))
        self.assertEqual(resp.status_code, 302)
        q.refresh_from_db()
        self.assertEqual(q.status, CampaignQueue.Status.CANCELLED)
        self.assertIsNotNone(q.completed_at)

    def test_campaign_recipients_reset_puts_in_queue(self):
        camp = self._make_campaign()
        camp.status = Campaign.Status.SENDING
        camp.save(update_fields=["status", "updated_at"])
        r = CampaignRecipient.objects.create(campaign=camp, email="a@example.com", status=CampaignRecipient.Status.SENT)
        # Имитируем ситуацию, когда кампания могла быть в очереди.
        q = CampaignQueue.objects.create(campaign=camp, status=CampaignQueue.Status.PENDING, priority=0)

        resp = self.client.post(reverse("campaign_recipients_reset", kwargs={"campaign_id": camp.id}))
        self.assertEqual(resp.status_code, 302)
        r.refresh_from_db()
        self.assertEqual(r.status, CampaignRecipient.Status.PENDING)
        camp.refresh_from_db()
        self.assertEqual(camp.status, Campaign.Status.DRAFT)
        q.refresh_from_db()
        # «Вернуть в очередь» не должно автозапускать рассылку: очередь отменяется, старт делается вручную.
        self.assertEqual(q.status, CampaignQueue.Status.CANCELLED)
        self.assertIsNone(q.started_at)
        self.assertIsNotNone(q.completed_at)
