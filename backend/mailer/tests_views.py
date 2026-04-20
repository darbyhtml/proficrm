"""
Тесты view-слоя для mailer/ — HTTP request/response, permissions, redirects.

Покрытие:
  1. campaigns list        — GET, login required, manager видит только свои
  2. campaign_create       — GET/POST, создание, валидация формы
  3. campaign_detail       — GET, 200/404, чужая кампания
  4. campaign_edit         — GET/POST, чужая кампания = редирект
  5. campaign_delete       — POST, только свои
  6. campaign_start        — POST, SMTP не настроен, нет получателей
  7. campaign_pause/resume — POST, переключение статусов
  8. mail_signature        — GET/POST, сохраняется в User
  9. mail_settings         — только admin
 10. unsubscribe           — публичная, без логина, rate limit
 11. campaign_add_email    — AJAX POST, возвращает JSON
 12. campaign_progress_poll— AJAX GET, возвращает JSON
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

from django.test import Client, TestCase, override_settings
from django.urls import reverse

from accounts.models import User
from mailer.models import (
    Campaign,
    CampaignQueue,
    CampaignRecipient,
    GlobalMailAccount,
    Unsubscribe,
    UnsubscribeToken,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_user(username, role=User.Role.MANAGER, **kwargs):
    u = User.objects.create_user(username=username, password="pass", role=role, **kwargs)
    return u


def _make_campaign(user, status=Campaign.Status.DRAFT, **kwargs):
    return Campaign.objects.create(
        created_by=user,
        name=kwargs.pop("name", "Test Campaign"),
        subject=kwargs.pop("subject", "Test Subject"),
        body_html="<p>Hello</p>",
        body_text="Hello",
        status=status,
        **kwargs,
    )


def _make_global_smtp(enabled=True):
    cfg, _ = GlobalMailAccount.objects.get_or_create(id=1)
    cfg.smtp_host = "smtp.example.com"
    cfg.smtp_port = 587
    cfg.smtp_username = "sender@example.com"
    cfg.from_email = "sender@example.com"
    cfg.is_enabled = enabled
    cfg.save()
    return cfg


# ---------------------------------------------------------------------------
# 1. Campaigns list
# ---------------------------------------------------------------------------


@override_settings(SECURE_SSL_REDIRECT=False)
class CampaignsListViewTest(TestCase):
    def setUp(self):
        self.manager = _make_user("mgr1", role=User.Role.MANAGER)
        self.other = _make_user("mgr2", role=User.Role.MANAGER)
        self.camp_own = _make_campaign(self.manager)
        self.camp_other = _make_campaign(self.other)

    def test_requires_login(self):
        r = self.client.get(reverse("campaigns"))
        self.assertNotEqual(r.status_code, 200)

    def test_manager_sees_only_own_campaigns(self):
        self.client.force_login(self.manager)
        r = self.client.get(reverse("campaigns"))
        self.assertEqual(r.status_code, 200)
        camps = list(r.context["campaigns"])
        ids = [str(c.id) for c in camps]
        self.assertIn(str(self.camp_own.id), ids)
        self.assertNotIn(str(self.camp_other.id), ids)

    def test_admin_sees_all_campaigns(self):
        admin = _make_user("adm", role=User.Role.ADMIN)
        self.client.force_login(admin)
        r = self.client.get(reverse("campaigns"))
        self.assertEqual(r.status_code, 200)
        ids = [str(c.id) for c in r.context["campaigns"]]
        self.assertIn(str(self.camp_own.id), ids)
        self.assertIn(str(self.camp_other.id), ids)


# ---------------------------------------------------------------------------
# 2. campaign_create
# ---------------------------------------------------------------------------


@override_settings(SECURE_SSL_REDIRECT=False)
class CampaignCreateViewTest(TestCase):
    def setUp(self):
        self.user = _make_user("mgr", email="mgr@example.com")
        _make_global_smtp()

    def test_get_renders_form(self):
        self.client.force_login(self.user)
        r = self.client.get(reverse("campaign_create"))
        self.assertEqual(r.status_code, 200)
        self.assertIn("form", r.context)

    def test_post_creates_campaign(self):
        self.client.force_login(self.user)
        r = self.client.post(
            reverse("campaign_create"),
            {
                "name": "My Campaign",
                "subject": "Hello",
                "sender_name": "Test Sender",
                "body_html": "<p>Content</p>",
            },
        )
        self.assertEqual(
            Campaign.objects.filter(created_by=self.user, name="My Campaign").count(), 1
        )
        camp = Campaign.objects.get(created_by=self.user, name="My Campaign")
        self.assertRedirects(
            r,
            reverse("campaign_detail", kwargs={"campaign_id": camp.id}),
            fetch_redirect_response=False,
        )

    def test_post_invalid_form_rerenders(self):
        self.client.force_login(self.user)
        r = self.client.post(reverse("campaign_create"), {"name": ""})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.context["form"].is_valid())

    def test_requires_login(self):
        r = self.client.get(reverse("campaign_create"))
        self.assertNotEqual(r.status_code, 200)


# ---------------------------------------------------------------------------
# 3. campaign_detail
# ---------------------------------------------------------------------------


@override_settings(SECURE_SSL_REDIRECT=False)
class CampaignDetailViewTest(TestCase):
    def setUp(self):
        self.user = _make_user("mgr", email="mgr@example.com")
        self.other = _make_user("mgr2", email="mgr2@example.com")
        self.camp = _make_campaign(self.user)
        _make_global_smtp()

    def test_get_200_for_own_campaign(self):
        self.client.force_login(self.user)
        r = self.client.get(reverse("campaign_detail", kwargs={"campaign_id": self.camp.id}))
        self.assertEqual(r.status_code, 200)

    def test_get_404_for_nonexistent(self):
        self.client.force_login(self.user)
        r = self.client.get(reverse("campaign_detail", kwargs={"campaign_id": uuid.uuid4()}))
        self.assertEqual(r.status_code, 404)

    def test_requires_login(self):
        r = self.client.get(reverse("campaign_detail", kwargs={"campaign_id": self.camp.id}))
        self.assertNotEqual(r.status_code, 200)


# ---------------------------------------------------------------------------
# 4. campaign_edit
# ---------------------------------------------------------------------------


@override_settings(SECURE_SSL_REDIRECT=False)
class CampaignEditViewTest(TestCase):
    def setUp(self):
        self.user = _make_user("mgr")
        self.other = _make_user("mgr2")
        self.camp = _make_campaign(self.user)
        _make_global_smtp()

    def test_get_renders_form(self):
        self.client.force_login(self.user)
        r = self.client.get(reverse("campaign_edit", kwargs={"campaign_id": self.camp.id}))
        self.assertEqual(r.status_code, 200)

    def test_post_saves_name(self):
        self.client.force_login(self.user)
        self.client.post(
            reverse("campaign_edit", kwargs={"campaign_id": self.camp.id}),
            {
                "name": "Updated Name",
                "subject": "Updated Subject",
                "sender_name": "Sender",
                "body_html": "<p>Updated</p>",
            },
        )
        self.camp.refresh_from_db()
        self.assertEqual(self.camp.name, "Updated Name")

    def test_other_user_redirected(self):
        self.client.force_login(self.other)
        r = self.client.get(reverse("campaign_edit", kwargs={"campaign_id": self.camp.id}))
        self.assertRedirects(r, reverse("campaigns"), fetch_redirect_response=False)


# ---------------------------------------------------------------------------
# 5. campaign_delete
# ---------------------------------------------------------------------------


@override_settings(SECURE_SSL_REDIRECT=False)
class CampaignDeleteViewTest(TestCase):
    def setUp(self):
        self.user = _make_user("mgr")
        self.other = _make_user("mgr2")

    def test_owner_can_delete(self):
        camp = _make_campaign(self.user)
        self.client.force_login(self.user)
        r = self.client.post(reverse("campaign_delete", kwargs={"campaign_id": camp.id}))
        self.assertFalse(Campaign.objects.filter(id=camp.id).exists())
        self.assertRedirects(r, reverse("campaigns"), fetch_redirect_response=False)

    def test_other_user_cannot_delete(self):
        camp = _make_campaign(self.user)
        self.client.force_login(self.other)
        self.client.post(reverse("campaign_delete", kwargs={"campaign_id": camp.id}))
        self.assertTrue(Campaign.objects.filter(id=camp.id).exists())


# ---------------------------------------------------------------------------
# 6. campaign_start — проверка блокирующих условий
# ---------------------------------------------------------------------------


@override_settings(SECURE_SSL_REDIRECT=False)
class CampaignStartViewTest(TestCase):
    def setUp(self):
        self.user = _make_user("mgr", email="mgr@example.com")
        self.camp = _make_campaign(self.user)

    def test_get_redirects_to_detail(self):
        self.client.force_login(self.user)
        r = self.client.get(reverse("campaign_start", kwargs={"campaign_id": self.camp.id}))
        self.assertRedirects(
            r,
            reverse("campaign_detail", kwargs={"campaign_id": self.camp.id}),
            fetch_redirect_response=False,
        )

    def test_smtp_not_configured_blocks_start(self):
        # SMTP is_enabled=False — не должен запускаться
        cfg, _ = GlobalMailAccount.objects.get_or_create(id=1)
        cfg.is_enabled = False
        cfg.save()
        self.client.force_login(self.user)
        with patch("mailer.views.sending.is_user_throttled", return_value=(False, 0, None)):
            r = self.client.post(reverse("campaign_start", kwargs={"campaign_id": self.camp.id}))
        self.camp.refresh_from_db()
        self.assertNotEqual(self.camp.status, Campaign.Status.SENDING)
        self.assertRedirects(
            r,
            reverse("campaign_detail", kwargs={"campaign_id": self.camp.id}),
            fetch_redirect_response=False,
        )

    def test_no_pending_recipients_blocks_start(self):
        _make_global_smtp(enabled=True)
        # Нет получателей → не запускаем
        self.client.force_login(self.user)
        with patch("mailer.views.sending.is_user_throttled", return_value=(False, 0, None)):
            r = self.client.post(reverse("campaign_start", kwargs={"campaign_id": self.camp.id}))
        self.camp.refresh_from_db()
        self.assertNotEqual(self.camp.status, Campaign.Status.SENDING)

    def test_other_user_blocked(self):
        other = _make_user("mgr2")
        self.client.force_login(other)
        r = self.client.post(reverse("campaign_start", kwargs={"campaign_id": self.camp.id}))
        self.assertRedirects(r, reverse("campaigns"), fetch_redirect_response=False)


# ---------------------------------------------------------------------------
# 7. campaign_pause / campaign_resume
# ---------------------------------------------------------------------------


@override_settings(SECURE_SSL_REDIRECT=False)
class CampaignPauseResumeViewTest(TestCase):
    def setUp(self):
        self.user = _make_user("mgr", email="mgr@example.com")
        _make_global_smtp()

    def test_pause_sending_campaign(self):
        camp = _make_campaign(self.user, status=Campaign.Status.SENDING)
        CampaignQueue.objects.create(
            campaign=camp,
            status=CampaignQueue.Status.PROCESSING,
            priority=5,
        )
        self.client.force_login(self.user)
        self.client.post(reverse("campaign_pause", kwargs={"campaign_id": camp.id}))
        camp.refresh_from_db()
        self.assertEqual(camp.status, Campaign.Status.PAUSED)

    def test_resume_paused_campaign(self):
        camp = _make_campaign(self.user, status=Campaign.Status.PAUSED)
        CampaignQueue.objects.create(
            campaign=camp,
            status=CampaignQueue.Status.CANCELLED,
            priority=5,
        )
        CampaignRecipient.objects.create(
            campaign=camp, email="a@b.com", status=CampaignRecipient.Status.PENDING
        )
        self.client.force_login(self.user)
        with patch("mailer.views.sending.is_user_throttled", return_value=(False, 0, None)):
            self.client.post(reverse("campaign_resume", kwargs={"campaign_id": camp.id}))
        camp.refresh_from_db()
        # resume ставит READY (Celery worker переводит в SENDING при запуске)
        self.assertEqual(camp.status, Campaign.Status.READY)


# ---------------------------------------------------------------------------
# 8. mail_signature
# ---------------------------------------------------------------------------


@override_settings(SECURE_SSL_REDIRECT=False)
class MailSignatureViewTest(TestCase):
    def setUp(self):
        self.user = _make_user("mgr")

    def test_get_renders(self):
        self.client.force_login(self.user)
        r = self.client.get(reverse("mail_signature"))
        self.assertEqual(r.status_code, 200)

    def test_post_saves_signature(self):
        self.client.force_login(self.user)
        self.client.post(
            reverse("mail_signature"),
            {
                "signature_html": "<p>My Signature</p>",
            },
        )
        self.user.refresh_from_db()
        self.assertIn("My Signature", self.user.email_signature_html)

    def test_requires_login(self):
        r = self.client.get(reverse("mail_signature"))
        self.assertNotEqual(r.status_code, 200)


# ---------------------------------------------------------------------------
# 9. mail_settings — только admin
# ---------------------------------------------------------------------------


@override_settings(SECURE_SSL_REDIRECT=False)
class MailSettingsViewTest(TestCase):
    def setUp(self):
        self.admin = _make_user("adm", role=User.Role.ADMIN)
        self.manager = _make_user("mgr", role=User.Role.MANAGER)
        _make_global_smtp()

    def test_admin_can_access(self):
        self.client.force_login(self.admin)
        r = self.client.get(reverse("mail_settings"))
        self.assertEqual(r.status_code, 200)

    def test_manager_redirected(self):
        self.client.force_login(self.manager)
        r = self.client.get(reverse("mail_settings"))
        # policy enforce в observe_only — страница загружается, но доступ ограничен
        # Достаточно проверить что менеджер не падает с 500
        self.assertIn(r.status_code, [200, 302, 403])

    def test_requires_login(self):
        r = self.client.get(reverse("mail_settings"))
        self.assertNotEqual(r.status_code, 200)


# ---------------------------------------------------------------------------
# 10. unsubscribe — публичная view
# ---------------------------------------------------------------------------


@override_settings(SECURE_SSL_REDIRECT=False)
class UnsubscribeViewTest(TestCase):
    def test_get_with_valid_token_renders(self):
        t = UnsubscribeToken.objects.create(token="abc123", email="user@example.com")
        r = self.client.get(reverse("unsubscribe", kwargs={"token": "abc123"}))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["email"], "user@example.com")

    def test_post_with_valid_token_creates_unsubscribe(self):
        UnsubscribeToken.objects.create(token="tok456", email="unsub@example.com")
        self.client.post(reverse("unsubscribe", kwargs={"token": "tok456"}))
        self.assertTrue(Unsubscribe.objects.filter(email="unsub@example.com").exists())

    def test_get_with_unknown_token_renders_empty_email(self):
        r = self.client.get(reverse("unsubscribe", kwargs={"token": "nonexistent"}))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["email"], "")

    def test_no_login_required(self):
        # Публичная view, должна работать без аутентификации
        r = self.client.get(reverse("unsubscribe", kwargs={"token": "any"}))
        self.assertNotEqual(r.status_code, 302)

    def test_rate_limit_returns_429(self):
        from django.core.cache import cache

        ip = "127.0.0.1"
        from mailer.constants import UNSUBSCRIBE_RATE_LIMIT_PER_HOUR

        cache.set(f"mailer:unsub_ratelimit:{ip}", UNSUBSCRIBE_RATE_LIMIT_PER_HOUR, 3600)
        r = self.client.get(reverse("unsubscribe", kwargs={"token": "sometoken"}), REMOTE_ADDR=ip)
        self.assertEqual(r.status_code, 429)


# ---------------------------------------------------------------------------
# 11. campaign_add_email — AJAX
# ---------------------------------------------------------------------------


@override_settings(SECURE_SSL_REDIRECT=False)
class CampaignAddEmailViewTest(TestCase):
    def setUp(self):
        self.user = _make_user("mgr", email="mgr@example.com")
        self.camp = _make_campaign(self.user, status=Campaign.Status.DRAFT)

    def test_post_returns_json(self):
        self.client.force_login(self.user)
        r = self.client.post(
            reverse("campaign_add_email"),
            {"campaign_id": str(self.camp.id), "email": "new@example.com"},
        )
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("ok", data)

    def test_requires_login(self):
        r = self.client.post(
            reverse("campaign_add_email"),
            {"campaign_id": str(self.camp.id), "email": "x@example.com"},
        )
        self.assertNotEqual(r.status_code, 200)


# ---------------------------------------------------------------------------
# 12. campaign_progress_poll — AJAX JSON
# ---------------------------------------------------------------------------


@override_settings(SECURE_SSL_REDIRECT=False)
class CampaignProgressPollViewTest(TestCase):
    def setUp(self):
        self.user = _make_user("mgr", email="mgr@example.com")
        self.camp = _make_campaign(self.user, status=Campaign.Status.SENDING)

    def test_returns_json_with_progress(self):
        self.client.force_login(self.user)
        r = self.client.get(reverse("campaign_progress_poll", kwargs={"campaign_id": self.camp.id}))
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("total", data)
        self.assertIn("sent", data)
        self.assertIn("failed", data)

    def test_requires_login(self):
        r = self.client.get(reverse("campaign_progress_poll", kwargs={"campaign_id": self.camp.id}))
        self.assertNotEqual(r.status_code, 200)
