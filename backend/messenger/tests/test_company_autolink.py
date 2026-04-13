from django.test import TestCase
from accounts.models import Branch
from messenger.models import Conversation, Contact, Inbox


class ConversationCompanyFieldTests(TestCase):
    def setUp(self):
        self.branch = Branch.objects.create(name="ЕКБ", code="ekb")
        self.inbox = Inbox.objects.create(
            name="S", branch=self.branch, widget_token="tok_ctx", settings={}
        )
        self.contact = Contact.objects.create(
            external_id="ctx_c", name="C", email="c@e.com"
        )
        self.conv = Conversation.objects.create(
            inbox=self.inbox, contact=self.contact, branch=self.branch
        )

    def test_company_defaults_none(self):
        self.assertIsNone(self.conv.company)
