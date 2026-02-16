"""
Бизнес-логика messenger.

На Этапе 1 оставляем только заготовки, чтобы структурировать кодовую базу.
Полная реализация (routing, assign_conversation, обработка входящих сообщений)
будет добавлена на этапах 4–5.
"""

from __future__ import annotations

from typing import Optional

from django.utils import timezone

from accounts.models import User
from .models import Conversation, Message, Contact


def create_or_get_contact(
    *,
    external_id: str | None = None,
    email: str | None = None,
    phone: str | None = None,
    name: str | None = None,
) -> Contact:
    """
    Базовая реализация create_or_get_contact.

    В v1 держим логику простой: ищем по external_id, затем по email/phone.
    """
    qs = Contact.objects.all()
    if external_id:
        contact = qs.filter(external_id=external_id).first()
        if contact:
            return contact
    if email:
        contact = qs.filter(email=email).first()
        if contact:
            return contact
    if phone:
        contact = qs.filter(phone=phone).first()
        if contact:
            return contact

    contact = Contact.objects.create(
        external_id=external_id or "",
        email=email or "",
        phone=phone or "",
        name=name or "",
    )
    return contact


def record_message(
    *,
    conversation: Conversation,
    direction: str,
    body: str,
    sender_user: Optional[User] = None,
    sender_contact: Optional[Contact] = None,
) -> Message:
    """
    Создаёт сообщение в диалоге и обновляет last_message_at.
    """
    msg = Message.objects.create(
        conversation=conversation,
        direction=direction,
        body=body,
        sender_user=sender_user,
        sender_contact=sender_contact,
    )
    Conversation.objects.filter(pk=conversation.pk).update(last_message_at=timezone.now())
    return msg


def assign_conversation(conversation: Conversation) -> Conversation:
    """
    Заглушка для логики автоприсвоения диалогов.

    Полная реализация (round-robin по операторам филиала, учёт online-статуса)
    будет добавлена на последующих этапах. На Этапе 1 ничего не делает.
    """
    return conversation

