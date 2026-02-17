"""
Экспорт данных контакта Messenger (GDPR): контакт → диалоги → сообщения → вложения.

Пример:
  python manage.py export_messenger_contact_data --contact-id <uuid>

По умолчанию печатает JSON в stdout.
"""

from __future__ import annotations

import json
from uuid import UUID

from django.core.management.base import BaseCommand, CommandError

from messenger.models import Contact, Conversation, Message


class Command(BaseCommand):
    help = "Экспортировать данные контакта messenger в JSON (GDPR)."

    def add_arguments(self, parser):
        parser.add_argument("--contact-id", required=True, help="UUID контакта messenger")
        parser.add_argument("--pretty", action="store_true", help="Форматировать JSON (indent=2)")

    def handle(self, *args, **options):
        contact_id_raw = options["contact_id"]
        try:
            contact_uuid = UUID(str(contact_id_raw))
        except Exception:
            raise CommandError("contact-id должен быть UUID")

        try:
            contact = Contact.objects.select_related("region_detected").get(id=contact_uuid)
        except Contact.DoesNotExist:
            raise CommandError("Контакт не найден")

        conversations = (
            Conversation.objects.select_related("inbox", "branch", "region", "assignee")
            .filter(contact=contact)
            .order_by("id")
        )

        data = {
            "contact": {
                "id": str(contact.id),
                "external_id": contact.external_id,
                "name": contact.name,
                "email": contact.email,
                "phone": contact.phone,
                "region_detected": contact.region_detected.name if contact.region_detected else None,
                "created_at": contact.created_at.isoformat() if contact.created_at else None,
            },
            "conversations": [],
        }

        for conv in conversations:
            conv_payload = {
                "id": conv.id,
                "inbox_id": conv.inbox_id,
                "inbox_name": getattr(conv.inbox, "name", None),
                "branch_id": conv.branch_id,
                "branch_name": getattr(conv.branch, "name", None) if conv.branch_id else None,
                "region_id": conv.region_id,
                "region_name": getattr(conv.region, "name", None) if conv.region_id else None,
                "status": conv.status,
                "priority": conv.priority,
                "assignee_id": conv.assignee_id,
                "assignee_username": getattr(conv.assignee, "username", None) if conv.assignee_id else None,
                "created_at": conv.created_at.isoformat() if conv.created_at else None,
                "last_message_at": conv.last_message_at.isoformat() if conv.last_message_at else None,
                "rating_score": conv.rating_score,
                "rating_comment": conv.rating_comment,
                "rated_at": conv.rated_at.isoformat() if conv.rated_at else None,
                "messages": [],
            }

            messages = (
                Message.objects.select_related("sender_user", "sender_contact")
                .prefetch_related("attachments")
                .filter(conversation=conv)
                .order_by("created_at", "id")
            )
            for msg in messages:
                conv_payload["messages"].append(
                    {
                        "id": msg.id,
                        "direction": msg.direction,
                        "body": msg.body,
                        "created_at": msg.created_at.isoformat() if msg.created_at else None,
                        "sender_user_id": msg.sender_user_id,
                        "sender_contact_id": str(msg.sender_contact_id) if msg.sender_contact_id else None,
                        "read_at": msg.read_at.isoformat() if msg.read_at else None,
                        "attachments": [
                            {
                                "id": att.id,
                                "original_name": att.original_name,
                                "content_type": att.content_type,
                                "size": att.size,
                                "file": getattr(att.file, "name", ""),
                            }
                            for att in msg.attachments.all()
                        ],
                    }
                )

            data["conversations"].append(conv_payload)

        pretty = bool(options.get("pretty"))
        self.stdout.write(json.dumps(data, ensure_ascii=False, indent=2 if pretty else None))

