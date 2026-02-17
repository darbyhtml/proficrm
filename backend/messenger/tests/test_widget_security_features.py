from __future__ import annotations

from django.conf import settings
from django.core.cache import cache
from django.test import TestCase, Client
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import Branch
from messenger.models import Inbox, Contact, Conversation


class WidgetSecurityFeatureTests(TestCase):
    def setUp(self):
        self.original_messenger_enabled = getattr(settings, "MESSENGER_ENABLED", False)
        settings.MESSENGER_ENABLED = True

        self.branch = Branch.objects.create(code="b1", name="B1")
        self.inbox = Inbox.objects.create(
            name="Inbox",
            branch=self.branch,
            widget_token="token_1",
            is_active=True,
            settings={},
        )
        self.contact = Contact.objects.create(external_id="v1", name="V")
        self.conversation = Conversation.objects.create(
            inbox=self.inbox,
            contact=self.contact,
            branch=self.branch,
            status=Conversation.Status.OPEN,
        )

    def tearDown(self):
        settings.MESSENGER_ENABLED = self.original_messenger_enabled

    def test_domain_allowlist_blocks_foreign_origin(self):
        self.inbox.settings = {"security": {"allowed_domains": ["allowed.com"]}}
        self.inbox.save(update_fields=["settings"])

        client = APIClient()
        r = client.post(
            "/api/widget/bootstrap/",
            {"widget_token": "token_1", "contact_external_id": "v2"},
            format="json",
            HTTP_ORIGIN="https://bad.com",
        )
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)

    def test_captcha_required_then_passed_allows_send(self):
        ip = "1.2.3.4"
        cache.set(f"messenger:captcha:ip:{ip}", 999, timeout=600)

        client = APIClient()
        boot = client.post(
            "/api/widget/bootstrap/",
            {"widget_token": "token_1", "contact_external_id": "v1"},
            format="json",
            REMOTE_ADDR=ip,
        )
        self.assertEqual(boot.status_code, status.HTTP_200_OK)
        self.assertTrue(boot.data.get("captcha_required"))
        token = boot.data.get("captcha_token")
        self.assertTrue(token)

        expected = cache.get(f"messenger:captcha:token:{token}")
        self.assertTrue(expected)

        session = boot.data["widget_session_token"]

        # send without captcha → 400
        r1 = client.post(
            "/api/widget/send/",
            {"widget_token": "token_1", "widget_session_token": session, "body": "hi"},
            format="json",
            REMOTE_ADDR=ip,
        )
        self.assertEqual(r1.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(r1.data.get("captcha_required"))

        # send with captcha → 201
        r2 = client.post(
            "/api/widget/send/",
            {
                "widget_token": "token_1",
                "widget_session_token": session,
                "body": "hi",
                "captcha_token": token,
                "captcha_answer": expected,
            },
            format="json",
            REMOTE_ADDR=ip,
        )
        self.assertEqual(r2.status_code, status.HTTP_201_CREATED)

        # subsequent send without captcha should pass for same session
        r3 = client.post(
            "/api/widget/send/",
            {"widget_token": "token_1", "widget_session_token": session, "body": "ok"},
            format="json",
            REMOTE_ADDR=ip,
        )
        self.assertEqual(r3.status_code, status.HTTP_201_CREATED)

    def test_stream_endpoint_returns_sse(self):
        client = APIClient()
        boot = client.post(
            "/api/widget/bootstrap/",
            {"widget_token": "token_1", "contact_external_id": "v1"},
            format="json",
        )
        session = boot.data["widget_session_token"]

        c = Client()
        resp = c.get(
            "/api/widget/stream/",
            {"widget_token": "token_1", "widget_session_token": session},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/event-stream", resp.get("Content-Type", ""))
        first = next(resp.streaming_content)
        if isinstance(first, bytes):
            first_text = first.decode("utf-8", errors="ignore")
        else:
            first_text = str(first)
        self.assertIn("event: ready", first_text)

