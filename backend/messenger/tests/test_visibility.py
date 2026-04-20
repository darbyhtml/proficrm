"""Тесты ролевой видимости диалогов: messenger.selectors.get_visible_conversations."""

from django.db.models.signals import post_save
from django.test import TestCase

from accounts.models import Branch, User
from messenger.models import Contact, Conversation, Inbox
from messenger.selectors import get_visible_conversations
from messenger.signals import auto_assign_new_conversation


class VisibilityTests(TestCase):
    """Проверка get_visible_conversations по ролям."""

    def setUp(self):
        # Отключаем сигнал auto_assign: иначе pool-диалоги (assignee=None)
        # были бы автоматически назначены менеджерам и тесты потеряли бы смысл.
        post_save.disconnect(auto_assign_new_conversation, sender=Conversation)
        self.addCleanup(post_save.connect, auto_assign_new_conversation, sender=Conversation)

        # Два филиала.
        self.ekb = Branch.objects.create(name="ЕКБ", code="ekb")
        self.tmn = Branch.objects.create(name="Тюмень", code="tmn")

        # Менеджеры филиала ekb.
        self.mgr_ekb_1 = User.objects.create_user(
            username="mgr_ekb_1",
            password="pass12345",
            role=User.Role.MANAGER,
            branch=self.ekb,
        )
        self.mgr_ekb_2 = User.objects.create_user(
            username="mgr_ekb_2",
            password="pass12345",
            role=User.Role.MANAGER,
            branch=self.ekb,
        )
        # Менеджер филиала tmn.
        self.mgr_tmn = User.objects.create_user(
            username="mgr_tmn",
            password="pass12345",
            role=User.Role.MANAGER,
            branch=self.tmn,
        )
        # Директор филиала ekb.
        self.director_ekb = User.objects.create_user(
            username="director_ekb",
            password="pass12345",
            role=User.Role.BRANCH_DIRECTOR,
            branch=self.ekb,
        )
        # РОП (SALES_HEAD) филиала ekb.
        self.rop_ekb = User.objects.create_user(
            username="rop_ekb",
            password="pass12345",
            role=User.Role.SALES_HEAD,
            branch=self.ekb,
        )
        # Суперпользователь (админ).
        self.admin = User.objects.create_superuser(
            username="admin",
            password="pass12345",
            email="admin@example.com",
        )

        # По одному Inbox на филиал.
        self.inbox_ekb = Inbox.objects.create(name="EKB Inbox", branch=self.ekb)
        self.inbox_tmn = Inbox.objects.create(name="TMN Inbox", branch=self.tmn)

        self.contact = Contact.objects.create(name="Visib", email="v@example.com")

        # 5 диалогов.
        self.conv_m1 = Conversation.objects.create(
            inbox=self.inbox_ekb,
            contact=self.contact,
            assignee=self.mgr_ekb_1,
        )
        self.conv_m2 = Conversation.objects.create(
            inbox=self.inbox_ekb,
            contact=self.contact,
            assignee=self.mgr_ekb_2,
        )
        self.conv_ekb_pool = Conversation.objects.create(
            inbox=self.inbox_ekb,
            contact=self.contact,
            assignee=None,
        )
        self.conv_tmn_assigned = Conversation.objects.create(
            inbox=self.inbox_tmn,
            contact=self.contact,
            assignee=self.mgr_tmn,
        )
        self.conv_tmn_pool = Conversation.objects.create(
            inbox=self.inbox_tmn,
            contact=self.contact,
            assignee=None,
        )

    def test_manager_sees_own_plus_own_branch_pool(self):
        visible = set(get_visible_conversations(self.mgr_ekb_1))
        # Свой назначенный + общий пул ekb. Чужие ekb-диалоги и tmn не видит.
        self.assertEqual(
            visible,
            {self.conv_m1, self.conv_ekb_pool},
        )
        self.assertNotIn(self.conv_m2, visible)
        self.assertNotIn(self.conv_tmn_assigned, visible)
        self.assertNotIn(self.conv_tmn_pool, visible)

    def test_director_sees_whole_branch(self):
        visible = set(get_visible_conversations(self.director_ekb))
        # Все три диалога филиала ekb (оба назначенных + пул). Tmn — нет.
        self.assertEqual(
            visible,
            {self.conv_m1, self.conv_m2, self.conv_ekb_pool},
        )
        self.assertNotIn(self.conv_tmn_assigned, visible)
        self.assertNotIn(self.conv_tmn_pool, visible)

    def test_rop_sees_whole_branch_same_as_director(self):
        visible_rop = set(get_visible_conversations(self.rop_ekb))
        visible_dir = set(get_visible_conversations(self.director_ekb))
        self.assertEqual(visible_rop, visible_dir)
        self.assertEqual(
            visible_rop,
            {self.conv_m1, self.conv_m2, self.conv_ekb_pool},
        )

    def test_admin_sees_everything(self):
        visible = set(get_visible_conversations(self.admin))
        self.assertEqual(
            visible,
            {
                self.conv_m1,
                self.conv_m2,
                self.conv_ekb_pool,
                self.conv_tmn_assigned,
                self.conv_tmn_pool,
            },
        )
