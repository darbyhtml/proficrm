"""Тесты auto-assignment pipeline для live-chat."""

from django.core.cache import cache
from django.test import TestCase

from accounts.models import Branch
from accounts.models_region import BranchRegion
from messenger.assignment_services.region_router import MultiBranchRouter
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


class MultiBranchRouterTests(TestCase):
    """Маршрутизация диалога в филиал по client_region."""

    COMMON_POOL_REGIONS = [
        "Москва, Московская область",
        "Санкт-Петербург, Ленинградская область",
        "Новгородская область",
        "Псковская область",
    ]

    def setUp(self):
        cache.clear()

        # Филиалы (ekb — fallback, tmn — Тюмень, krd — Краснодар).
        self.ekb = Branch.objects.create(name="ЕКБ", code="ekb")
        self.tmn = Branch.objects.create(name="Тюмень", code="tmn")
        self.krd = Branch.objects.create(name="Краснодар", code="krd")

        # Закреплённые регионы (is_common_pool=False).
        BranchRegion.objects.create(
            branch=self.ekb, region_name="Свердловская область"
        )
        BranchRegion.objects.create(
            branch=self.tmn, region_name="Томская область"
        )
        BranchRegion.objects.create(
            branch=self.krd, region_name="Краснодарский край"
        )

        # Общий пул: все четыре региона обслуживают все три филиала.
        for branch in (self.ekb, self.tmn, self.krd):
            for region in self.COMMON_POOL_REGIONS:
                BranchRegion.objects.create(
                    branch=branch, region_name=region, is_common_pool=True
                )

        self.inbox = Inbox.objects.create(name="Router Inbox", branch=self.ekb)
        self.contact = Contact.objects.create(
            name="Router Test", email="router@example.com"
        )
        self.router = MultiBranchRouter()

    def _make_conv(self, region: str) -> Conversation:
        return Conversation.objects.create(
            inbox=self.inbox,
            contact=self.contact,
            client_region=region,
        )

    def test_region_maps_to_exact_branch(self):
        conv = self._make_conv("Томская область")
        self.assertEqual(self.router.route(conv), self.tmn)

    def test_unknown_region_falls_back_to_ekb(self):
        conv = self._make_conv("Нет такого региона")
        self.assertEqual(self.router.route(conv), self.ekb)

    def test_empty_region_falls_back_to_ekb(self):
        conv = self._make_conv("")
        self.assertEqual(self.router.route(conv), self.ekb)

    def test_common_pool_picks_round_robin_branch(self):
        cache.clear()
        pool_ids = sorted([self.ekb.id, self.tmn.id, self.krd.id])

        picks = []
        for _ in range(len(pool_ids) * 2):
            conv = self._make_conv("Москва, Московская область")
            picks.append(self.router.route(conv).id)

        # Все филиалы общего пула должны быть выбраны.
        self.assertEqual(set(picks), set(pool_ids))

        # Round-robin: последовательность должна идти по возрастанию id
        # и повторяться циклически.
        expected_cycle = pool_ids + pool_ids
        self.assertEqual(picks, expected_cycle)
