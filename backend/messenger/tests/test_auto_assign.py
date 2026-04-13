"""Тесты auto-assignment pipeline для live-chat."""

from django.test import TestCase

from accounts.models import Branch
from messenger.models import Inbox, Conversation, Contact


class ConversationClientRegionTests(TestCase):
    """Проверка новых полей client_region / client_region_source."""

    def setUp(self):
        self.branch = Branch.objects.create(name="Test Branch", code="test")
        self.inbox = Inbox.objects.create(name="Test Inbox", branch=self.branch)
        self.contact = Contact.objects.create(
            name="Test", email="test@example.com"
        )

    def test_conversation_stores_client_region_and_source(self):
        conv = Conversation.objects.create(
            inbox=self.inbox,
            contact=self.contact,
            client_region="Свердловская область",
            client_region_source=Conversation.RegionSource.GEOIP,
        )
        self.assertEqual(conv.client_region, "Свердловская область")
        self.assertEqual(conv.client_region_source, "geoip")

    def test_conversation_region_defaults_empty(self):
        conv = Conversation.objects.create(inbox=self.inbox, contact=self.contact)
        self.assertEqual(conv.client_region, "")
        self.assertEqual(conv.client_region_source, "")
