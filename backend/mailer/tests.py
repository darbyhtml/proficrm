from __future__ import annotations

import tempfile
import uuid
from unittest.mock import patch

from django.test import TestCase
from django.test.utils import override_settings
from django.urls import reverse
from django.utils import timezone
from django.core.files.uploadedfile import SimpleUploadedFile

from accounts.models import User
from mailer.forms import CampaignForm, EmailSignatureForm
from mailer.models import (
    Campaign,
    CampaignQueue,
    CampaignRecipient,
    GlobalMailAccount,
    SendLog,
    SmtpBzQuota,
    Unsubscribe,
    UnsubscribeToken,
)
from mailer.utils import sanitize_email_html, get_next_send_window_start, msk_day_bounds
from mailer.constants import DEFER_REASON_DAILY_LIMIT


@override_settings(SECURE_SSL_REDIRECT=False)
class MailerSafetyAndUnsubTests(TestCase):
    def test_sanitize_email_html_removes_scripts_and_js_protocol(self):
        raw = '<div onclick="alert(1)"><script>alert(2)</script><a href="javascript:alert(3)">x</a></div>'
        cleaned = sanitize_email_html(raw)
        self.assertNotIn("<script", cleaned.lower())
        self.assertNotIn("onclick", cleaned.lower())
        self.assertNotIn("javascript:", cleaned.lower())

    def test_sanitize_email_html_normalizes_img_tags(self):
        raw = '<div><img src="x" width="600"></div>'
        cleaned = sanitize_email_html(raw)
        # style should be injected for email-client friendliness
        self.assertIn("<img", cleaned.lower())
        self.assertIn("style=", cleaned.lower())
        self.assertIn("max-width:100%", cleaned.lower())
        self.assertIn("height:auto", cleaned.lower())

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

    def test_campaign_recipients_reset_resets_only_failed_by_default(self):
        camp = self._make_campaign()
        camp.status = Campaign.Status.SENDING
        camp.save(update_fields=["status", "updated_at"])
        r_sent = CampaignRecipient.objects.create(campaign=camp, email="a@example.com", status=CampaignRecipient.Status.SENT)
        r_failed = CampaignRecipient.objects.create(campaign=camp, email="b@example.com", status=CampaignRecipient.Status.FAILED, last_error="x")
        # Имитируем ситуацию, когда кампания могла быть в очереди.
        q = CampaignQueue.objects.create(campaign=camp, status=CampaignQueue.Status.PENDING, priority=0)

        resp = self.client.post(reverse("campaign_recipients_reset", kwargs={"campaign_id": camp.id}))
        self.assertEqual(resp.status_code, 302)
        r_sent.refresh_from_db()
        r_failed.refresh_from_db()
        self.assertEqual(r_sent.status, CampaignRecipient.Status.SENT)
        self.assertEqual(r_failed.status, CampaignRecipient.Status.PENDING)
        camp.refresh_from_db()
        self.assertEqual(camp.status, Campaign.Status.DRAFT)
        q.refresh_from_db()
        # «Вернуть в очередь» не должно автозапускать рассылку: очередь отменяется, старт делается вручную.
        self.assertEqual(q.status, CampaignQueue.Status.CANCELLED)
        self.assertIsNone(q.started_at)
        self.assertIsNotNone(q.completed_at)

    def test_campaign_recipients_reset_all_requires_admin(self):
        # Создаем кампанию менеджера и одного отправленного получателя
        camp = self._make_campaign()
        camp.status = Campaign.Status.SENDING
        camp.save(update_fields=["status", "updated_at"])
        r_sent = CampaignRecipient.objects.create(campaign=camp, email="a@example.com", status=CampaignRecipient.Status.SENT)

        # Менеджер не может сбросить SENT в PENDING через scope=all
        resp = self.client.post(
            reverse("campaign_recipients_reset", kwargs={"campaign_id": camp.id}),
            data={"scope": "all"},
        )
        self.assertEqual(resp.status_code, 302)
        r_sent.refresh_from_db()
        self.assertEqual(r_sent.status, CampaignRecipient.Status.SENT)

        # Админ может
        admin = User.objects.create_user(username="adm", password="pass", role=User.Role.ADMIN, email="adm@example.com")
        self.client.force_login(admin)
        resp = self.client.post(
            reverse("campaign_recipients_reset", kwargs={"campaign_id": camp.id}),
            data={"scope": "all"},
        )
        self.assertEqual(resp.status_code, 302)
        r_sent.refresh_from_db()
        self.assertEqual(r_sent.status, CampaignRecipient.Status.PENDING)


@override_settings(SECURE_SSL_REDIRECT=False)
class MailerCampaignDetailTemplateTest(TestCase):
    def test_campaign_detail_renders(self):
        user = User.objects.create_user(username="m2", password="pass", role=User.Role.MANAGER, email="m2@example.com")
        self.client.force_login(user)
        camp = Campaign.objects.create(
            created_by=user,
            name="Camp",
            subject="Subj",
            body_html="<p>hi</p>",
            body_text="hi",
            sender_name="CRM",
            status=Campaign.Status.DRAFT,
        )
        resp = self.client.get(reverse("campaign_detail", kwargs={"campaign_id": camp.id}))
        self.assertEqual(resp.status_code, 200)


@override_settings(SECURE_SSL_REDIRECT=False)
class MailerAttachmentRecoveryTests(TestCase):
    def test_attachment_case_mismatch_is_recovered(self):
        from mailer.tasks import _get_campaign_attachment_bytes

        user = User.objects.create_user(username="m3", password="pass", role=User.Role.MANAGER, email="m3@example.com")
        with tempfile.TemporaryDirectory() as tmpdir:
            with override_settings(MEDIA_ROOT=tmpdir):
                camp = Campaign.objects.create(
                    created_by=user,
                    name="Camp",
                    subject="Subj",
                    body_html="<p>hi</p>",
                    body_text="hi",
                    sender_name="CRM",
                    status=Campaign.Status.DRAFT,
                )
                up = SimpleUploadedFile("ГДШ_Север_Бланк_заявки_GMMyLxa.docx", b"abc", content_type="application/octet-stream")
                camp.attachment.save(up.name, up)

                # Имитируем проблему: в БД имя файла отличается регистром (Linux чувствителен к регистру)
                bad_name = camp.attachment.name.replace("Бланк", "бланк")
                Campaign.objects.filter(id=camp.id).update(attachment=bad_name)
                camp.refresh_from_db()

                content, name, err = _get_campaign_attachment_bytes(camp)
                self.assertIsNone(err)
                self.assertEqual(content, b"abc")
                self.assertTrue(name)  # имя реального файла
                camp.refresh_from_db()
                # После восстановления путь должен указывать на реально существующий файл
                self.assertNotEqual(camp.attachment.name, bad_name)

    def test_attachment_missing_returns_error(self):
        from mailer.tasks import _get_campaign_attachment_bytes

        user = User.objects.create_user(username="m4", password="pass", role=User.Role.MANAGER, email="m4@example.com")
        with tempfile.TemporaryDirectory() as tmpdir:
            with override_settings(MEDIA_ROOT=tmpdir):
                camp = Campaign.objects.create(
                    created_by=user,
                    name="Camp",
                    subject="Subj",
                    body_html="<p>hi</p>",
                    body_text="hi",
                    sender_name="CRM",
                    status=Campaign.Status.DRAFT,
                )
                # Создаем запись на "вложение", но файл не кладём
                Campaign.objects.filter(id=camp.id).update(attachment="campaign_attachments/2026/01/missing.docx")
                camp.refresh_from_db()
                content, name, err = _get_campaign_attachment_bytes(camp)
                self.assertIsNone(content)
                self.assertIsNone(name)
                self.assertTrue(err)


@override_settings(SECURE_SSL_REDIRECT=False)
class MailerDeferDailyLimitTests(TestCase):
    """Тесты DEFER вместо PAUSE при дневном лимите: кампания не ставится в PAUSED, очередь получает deferred_until."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="defer_u", password="pass", role=User.Role.MANAGER, email="defer@example.com"
        )
        GlobalMailAccount.objects.update_or_create(
            id=1,
            defaults={"is_enabled": True, "per_user_daily_limit": 100},
        )
        cfg = SmtpBzQuota.load()
        cfg.emails_available = 500
        cfg.max_per_hour = 100
        cfg.emails_limit = 500
        cfg.sync_error = ""
        cfg.save()

    def test_get_next_send_window_start_tomorrow(self):
        from zoneinfo import ZoneInfo
        from datetime import datetime
        # 20:00 МСК -> завтра 09:00
        msk = ZoneInfo("Europe/Moscow")
        evening = datetime(2026, 1, 15, 20, 0, 0, tzinfo=msk)
        next_run = get_next_send_window_start(now=evening, always_tomorrow=True)
        self.assertEqual(next_run.hour, 9)
        self.assertEqual(next_run.day, 16)

    def test_campaign_resume_when_daily_limit_exhausted(self):
        """Resume при исчерпанном лимите: не READY «сейчас», deferred_until на завтра, сообщение."""
        self.client.force_login(self.user)
        camp = Campaign.objects.create(
            created_by=self.user,
            name="Defer Camp",
            subject="S",
            body_html="<p>x</p>",
            body_text="x",
            sender_name="X",
            status=Campaign.Status.PAUSED,
        )
        for i in range(3):
            CampaignRecipient.objects.create(campaign=camp, email=f"r{i}@ex.com", status=CampaignRecipient.Status.PENDING)
        start, end, _ = msk_day_bounds(timezone.now())
        for i in range(100):
            SendLog.objects.create(
                campaign=camp,
                recipient=None,
                account=None,
                provider="smtp_global",
                status="sent",
                created_at=start,
            )
        resp = self.client.post(reverse("campaign_resume", kwargs={"campaign_id": camp.id}))
        self.assertEqual(resp.status_code, 302)
        camp.refresh_from_db()
        self.assertEqual(camp.status, Campaign.Status.READY)
        q = getattr(camp, "queue_entry", None)
        self.assertIsNotNone(q)
        self.assertIsNotNone(q.deferred_until)
        self.assertEqual((q.defer_reason or "").strip(), DEFER_REASON_DAILY_LIMIT)

    @patch("mailer.tasks.cache.add", return_value=True)
    @patch("mailer.tasks.cache.delete")
    def test_send_pending_emails_defers_on_daily_limit_not_paused(self, _del, _add):
        """При достижении дневного лимита: Campaign не PAUSED, CampaignQueue.deferred_until и defer_reason."""
        camp = Campaign.objects.create(
            created_by=self.user,
            name="Defer Camp 2",
            subject="S",
            body_html="<p>x</p>",
            body_text="x",
            sender_name="X",
            status=Campaign.Status.READY,
        )
        for i in range(196):
            CampaignRecipient.objects.create(campaign=camp, email=f"r{i}@ex.com", status=CampaignRecipient.Status.PENDING)
        CampaignQueue.objects.create(campaign=camp, status=CampaignQueue.Status.PENDING, priority=0)
        start, end, _ = msk_day_bounds(timezone.now())
        for i in range(100):
            SendLog.objects.create(
                campaign=camp,
                recipient=None,
                account=None,
                provider="smtp_global",
                status="sent",
                created_at=start,
            )
        from mailer.tasks import send_pending_emails
        with patch("mailer.tasks._is_working_hours", return_value=True):
            with patch("mailer.tasks.open_smtp_connection"):
                with patch("mailer.tasks.send_via_smtp"):
                    send_pending_emails.run(batch_size=50)
        camp.refresh_from_db()
        self.assertNotEqual(camp.status, Campaign.Status.PAUSED)
        self.assertIn(camp.status, (Campaign.Status.READY, Campaign.Status.SENDING))
        q = CampaignQueue.objects.get(campaign=camp)
        self.assertIsNotNone(q.deferred_until)
        self.assertEqual((q.defer_reason or "").strip(), DEFER_REASON_DAILY_LIMIT)
        self.assertEqual(q.status, CampaignQueue.Status.PENDING)
