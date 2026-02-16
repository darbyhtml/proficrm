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
    update_if_exists: bool = True,
) -> Contact:
    """
    Создаёт или получает Contact, при необходимости обновляя поля.

    Args:
        external_id: Внешний идентификатор (visitor_id)
        email: Email контакта
        phone: Телефон контакта
        name: Имя контакта
        update_if_exists: Если True, обновляет поля существующего контакта новыми значениями (не перетирает на None)

    Returns:
        Contact instance
    """
    qs = Contact.objects.all()
    contact = None

    # Ищем по external_id в первую очередь
    if external_id:
        contact = qs.filter(external_id=external_id).first()
        if contact:
            if update_if_exists:
                # Обновляем поля только если переданы новые значения (не перетираем на None)
                update_fields = []
                if name and name != contact.name:
                    contact.name = name
                    update_fields.append("name")
                if email and email != contact.email:
                    contact.email = email
                    update_fields.append("email")
                if phone and phone != contact.phone:
                    contact.phone = phone
                    update_fields.append("phone")
                if update_fields:
                    contact.save(update_fields=update_fields)
            return contact

    # Если не нашли по external_id, ищем по email
    if email:
        contact = qs.filter(email=email).first()
        if contact:
            if update_if_exists:
                # Обновляем external_id и другие поля, если переданы
                update_fields = []
                if external_id and external_id != contact.external_id:
                    contact.external_id = external_id
                    update_fields.append("external_id")
                if name and name != contact.name:
                    contact.name = name
                    update_fields.append("name")
                if phone and phone != contact.phone:
                    contact.phone = phone
                    update_fields.append("phone")
                if update_fields:
                    contact.save(update_fields=update_fields)
            return contact

    # Если не нашли по email, ищем по phone
    if phone:
        contact = qs.filter(phone=phone).first()
        if contact:
            if update_if_exists:
                update_fields = []
                if external_id and external_id != contact.external_id:
                    contact.external_id = external_id
                    update_fields.append("external_id")
                if name and name != contact.name:
                    contact.name = name
                    update_fields.append("name")
                if email and email != contact.email:
                    contact.email = email
                    update_fields.append("email")
                if update_fields:
                    contact.save(update_fields=update_fields)
            return contact

    # Контакт не найден - создаём новый
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

