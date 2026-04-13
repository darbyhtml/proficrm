"""Тесты auto-assignment pipeline для live-chat."""

from django.core.cache import cache
from django.db.models.signals import post_save
from django.test import TestCase

from accounts.models import Branch, User
from accounts.models_region import BranchRegion
from messenger.assignment_services.auto_assign import auto_assign_conversation
from messenger.assignment_services.branch_load_balancer import BranchLoadBalancer
from messenger.assignment_services.region_router import MultiBranchRouter
from messenger.models import Inbox, Conversation, Contact
from messenger.signals import auto_assign_new_conversation


class ConversationClientRegionTests(TestCase):
    """Проверка новых полей client_region / client_region_source."""

    def setUp(self):
        # Отключаем auto_assign-сигнал — тесты проверяют простые поля,
        # а сигнал приводил бы к вызову роутера на каждом create.
        post_save.disconnect(auto_assign_new_conversation, sender=Conversation)
        self.addCleanup(
            post_save.connect, auto_assign_new_conversation, sender=Conversation
        )
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
        # Сигнал дергал бы router ещё раз на каждом Conversation.create,
        # ломая round-robin-счётчик. В этих юнит-тестах роутер вызывается вручную.
        post_save.disconnect(auto_assign_new_conversation, sender=Conversation)
        self.addCleanup(
            post_save.connect, auto_assign_new_conversation, sender=Conversation
        )
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


class BranchLoadBalancerTests(TestCase):
    """Выбор наименее загруженного онлайн-менеджера филиала."""

    def setUp(self):
        self.branch = Branch.objects.create(name="ЕКБ", code="ekb")
        self.inbox = Inbox.objects.create(name="LB Inbox", branch=self.branch)
        self.contact = Contact.objects.create(
            name="LB Test", email="lb@example.com"
        )

        # Свободный менеджер (0 открытых диалогов).
        self.op_free = User.objects.create_user(
            username="op_free",
            password="pass12345",
            role=User.Role.MANAGER,
            branch=self.branch,
            messenger_online=True,
        )
        # Загруженный менеджер (3 открытых диалога).
        self.op_loaded = User.objects.create_user(
            username="op_loaded",
            password="pass12345",
            role=User.Role.MANAGER,
            branch=self.branch,
            messenger_online=True,
        )
        # Менеджер из того же филиала — по умолчанию offline.
        self.op_offline = User.objects.create_user(
            username="op_offline",
            password="pass12345",
            role=User.Role.MANAGER,
            branch=self.branch,
            messenger_online=False,
        )

        for _ in range(3):
            Conversation.objects.create(
                inbox=self.inbox,
                contact=self.contact,
                assignee=self.op_loaded,
                status=Conversation.Status.OPEN,
            )

        self.balancer = BranchLoadBalancer()

    def test_picks_least_loaded_online_manager(self):
        picked = self.balancer.pick(self.branch)
        self.assertEqual(picked, self.op_free)

    def test_offline_manager_excluded(self):
        self.op_free.messenger_online = False
        self.op_free.save(update_fields=["messenger_online"])
        picked = self.balancer.pick(self.branch)
        self.assertEqual(picked, self.op_loaded)

    def test_returns_none_when_nobody_online(self):
        for op in (self.op_free, self.op_loaded, self.op_offline):
            op.messenger_online = False
            op.save(update_fields=["messenger_online"])
        self.assertIsNone(self.balancer.pick(self.branch))

    def test_non_manager_excluded(self):
        self.op_free.role = User.Role.BRANCH_DIRECTOR
        self.op_free.save(update_fields=["role"])
        picked = self.balancer.pick(self.branch)
        self.assertEqual(picked, self.op_loaded)


class AutoAssignIntegrationTests(TestCase):
    """Интеграционные тесты оркестратора auto_assign_conversation + post_save."""

    def setUp(self):
        cache.clear()

        # Два филиала: ekb — fallback, tmn — обслуживает Томскую область.
        self.ekb = Branch.objects.create(name="ЕКБ", code="ekb")
        self.tmn = Branch.objects.create(name="Тюмень", code="tmn")
        BranchRegion.objects.create(
            branch=self.tmn, region_name="Томская область"
        )

        # Менеджеры филиалов — оба online.
        self.op_ekb = User.objects.create_user(
            username="op_ekb",
            password="pass12345",
            role=User.Role.MANAGER,
            branch=self.ekb,
            messenger_online=True,
        )
        self.op_tmn = User.objects.create_user(
            username="op_tmn",
            password="pass12345",
            role=User.Role.MANAGER,
            branch=self.tmn,
            messenger_online=True,
        )

        # Inbox принадлежит филиалу ekb — при авто-назначении Conversation
        # должен «переехать» в филиал tmn через queryset.update() (обход
        # инварианта save, запрещающего менять branch).
        self.inbox = Inbox.objects.create(name="Auto Inbox", branch=self.ekb)
        self.contact = Contact.objects.create(
            name="Auto Test", email="auto@example.com"
        )

    def test_regional_conversation_assigned_to_branch_manager(self):
        """Регион матчится в tmn → ставится филиал и менеджер tmn."""
        conv = Conversation.objects.create(
            inbox=self.inbox,
            contact=self.contact,
            client_region="Томская область",
        )
        # post_save сигнал уже отработал на create — проверяем результат.
        conv.refresh_from_db()
        self.assertEqual(conv.branch, self.tmn)
        self.assertEqual(conv.assignee, self.op_tmn)

        # Явный повторный вызов оркестратора также корректен (идемпотентно).
        result = auto_assign_conversation(conv)
        self.assertTrue(result["assigned"])
        self.assertEqual(result["branch"], self.tmn)
        self.assertEqual(result["user"], self.op_tmn)

    def test_no_online_manager_leaves_pool(self):
        """Онлайн-менеджеров в филиале нет → branch ставится, assignee=None."""
        self.op_tmn.messenger_online = False
        self.op_tmn.save(update_fields=["messenger_online"])

        conv = Conversation.objects.create(
            inbox=self.inbox,
            contact=self.contact,
            client_region="Томская область",
        )
        conv.refresh_from_db()
        self.assertEqual(conv.branch, self.tmn)
        self.assertIsNone(conv.assignee)

        # Явный вызов — результат: assigned=False, branch=tmn, user=None.
        result = auto_assign_conversation(conv)
        self.assertFalse(result["assigned"])
        self.assertEqual(result["branch"], self.tmn)
        self.assertIsNone(result["user"])

    def test_signal_triggers_auto_assign_on_create(self):
        """Сигнал post_save должен сам дёргать оркестратор при create."""
        conv = Conversation.objects.create(
            inbox=self.inbox,
            contact=self.contact,
            client_region="Томская область",
        )
        # Никаких явных вызовов — всё должно произойти автоматически.
        conv.refresh_from_db()
        self.assertEqual(conv.assignee, self.op_tmn)
        self.assertEqual(conv.branch, self.tmn)
