from django.test import TestCase
from accounts.models import Branch
from messenger.models import Conversation, Contact, Inbox
from companies.models import Company


class ConversationCompanyFieldTests(TestCase):
    def setUp(self):
        self.branch = Branch.objects.create(name="ЕКБ", code="ekb")
        self.inbox = Inbox.objects.create(
            name="S", branch=self.branch, widget_token="tok_ctx", settings={}
        )
        self.contact = Contact.objects.create(external_id="ctx_c", name="C", email="c@e.com")
        self.conv = Conversation.objects.create(
            inbox=self.inbox, contact=self.contact, branch=self.branch
        )

    def test_company_defaults_none(self):
        self.assertIsNone(self.conv.company)


class AutolinkTests(TestCase):
    def setUp(self):
        self.branch = Branch.objects.create(name="ЕКБ", code="ekb2")
        self.inbox = Inbox.objects.create(
            name="S2", branch=self.branch, widget_token="tok_autolink", settings={}
        )
        # Компания с корпоративным email-доменом и телефоном.
        self.company = Company.objects.create(
            name="Газпром",
            email="info@gazprom.ru",
            phone="+79991234567",
        )

    def test_autolink_by_email_domain(self):
        contact = Contact.objects.create(
            external_id="al1", name="Екатерина", email="kate@gazprom.ru"
        )
        conv = Conversation.objects.create(inbox=self.inbox, contact=contact, branch=self.branch)
        conv.refresh_from_db()
        self.assertEqual(conv.company, self.company)

    def test_autolink_skips_when_multiple_matches(self):
        # Вторая компания с тем же доменом — должно стать неоднозначно.
        Company.objects.create(name="Газпром-Дубликат", email="hr@gazprom.ru")
        contact = Contact.objects.create(external_id="al2", name="K", email="kate@gazprom.ru")
        conv = Conversation.objects.create(inbox=self.inbox, contact=contact, branch=self.branch)
        conv.refresh_from_db()
        self.assertIsNone(conv.company)

    def test_autolink_skips_public_email_domains(self):
        contact = Contact.objects.create(external_id="al3", name="K", email="kate@gmail.com")
        conv = Conversation.objects.create(inbox=self.inbox, contact=contact, branch=self.branch)
        conv.refresh_from_db()
        self.assertIsNone(conv.company)

    def test_autolink_by_phone_when_email_missing(self):
        contact = Contact.objects.create(
            external_id="al4", name="K", email="", phone="+79991234567"
        )
        conv = Conversation.objects.create(inbox=self.inbox, contact=contact, branch=self.branch)
        conv.refresh_from_db()
        self.assertEqual(conv.company, self.company)

    def test_autolink_by_phone_when_public_email(self):
        # Публичный домен не должен мешать матчу по телефону.
        contact = Contact.objects.create(
            external_id="al5",
            name="K",
            email="kate@gmail.com",
            phone="89991234567",
        )
        conv = Conversation.objects.create(inbox=self.inbox, contact=contact, branch=self.branch)
        conv.refresh_from_db()
        self.assertEqual(conv.company, self.company)
