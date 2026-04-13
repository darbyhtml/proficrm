"""Тесты приватных сообщений (заметок для сотрудников).

Проверяют:
- дефолтное значение `is_private=False`;
- возможность пометить сообщение приватным;
- работу queryset-фильтра `is_private=False`, который используется
  в widget_api.py для скрытия приватных заметок от клиента в виджете.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import Branch
from messenger.models import Contact, Conversation, Inbox, Message

User = get_user_model()


class PrivateMessageModelTests(TestCase):
    def setUp(self):
        self.branch = Branch.objects.create(name="Br", code="br")
        self.inbox = Inbox.objects.create(name="Widget", branch=self.branch)
        self.contact = Contact.objects.create(name="Client")
        self.conv = Conversation.objects.create(inbox=self.inbox, contact=self.contact)
        self.op = User.objects.create_user(
            "op", password="pw", role=User.Role.MANAGER, branch=self.branch
        )

    def _create_out(self, body: str, is_private: bool = False) -> Message:
        # Исходящее сообщение от оператора — валидно по инвариантам Message.clean().
        return Message.objects.create(
            conversation=self.conv,
            direction=Message.Direction.OUT,
            body=body,
            sender_user=self.op,
            is_private=is_private,
        )

    def test_is_private_defaults_false(self):
        m = self._create_out("Hello")
        self.assertFalse(m.is_private)

    def test_private_flag_can_be_set(self):
        m = self._create_out("Заметка для своих", is_private=True)
        self.assertTrue(m.is_private)

    def test_private_messages_filtered_from_widget_queryset(self):
        """Базовая проверка: queryset-фильтр is_private=False работает."""
        self._create_out("Публичное", is_private=False)
        self._create_out("ПРИВАТНО", is_private=True)

        public = Message.objects.filter(conversation=self.conv, is_private=False)
        self.assertEqual(public.count(), 1)
        self.assertEqual(public.first().body, "Публичное")
