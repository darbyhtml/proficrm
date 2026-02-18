"""
Анонимизация контакта Messenger (GDPR).

По умолчанию очищает персональные поля Contact.
Опционально:
- редактирует входящие сообщения контакта (body -> "[redacted]");
- удаляет вложения (и файлы) у сообщений контакта.

Пример:
  python manage.py anonymize_messenger_contact --contact-id <uuid> --redact-messages --delete-attachments
"""

from __future__ import annotations

import hashlib
from uuid import UUID

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from messenger.models import Contact, Conversation, Message, MessageAttachment


class Command(BaseCommand):
    help = "Анонимизировать контакт messenger (GDPR): очистить PII и опционально редактировать сообщения/вложения."

    def add_arguments(self, parser):
        parser.add_argument("--contact-id", required=True, help="UUID контакта messenger")
        parser.add_argument("--dry-run", action="store_true", help="Показать что будет сделано, без изменений.")
        parser.add_argument("--redact-messages", action="store_true", help="Заменить body у IN сообщений контакта на '[redacted]'.")
        parser.add_argument("--delete-attachments", action="store_true", help="Удалить вложения (и файлы) у IN сообщений контакта.")

    def handle(self, *args, **options):
        contact_id_raw = options["contact_id"]
        try:
            contact_uuid = UUID(str(contact_id_raw))
        except Exception:
            raise CommandError("contact-id должен быть UUID")

        try:
            contact = Contact.objects.get(id=contact_uuid)
        except Contact.DoesNotExist:
            raise CommandError("Контакт не найден")

        dry_run = bool(options.get("dry_run"))
        redact_messages = bool(options.get("redact_messages"))
        delete_attachments = bool(options.get("delete_attachments"))

        # Стабильный псевдо-ID для external_id (чтобы не ломать уникальность/интеграции полностью)
        seed = f"{contact.id}:{contact.external_id}:{contact.email}:{contact.phone}"
        pseudo = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
        new_external_id = f"anon:{pseudo}"

        conv_ids = list(Conversation.objects.filter(contact=contact).values_list("id", flat=True))
        in_msg_qs = Message.objects.filter(conversation_id__in=conv_ids, direction=Message.Direction.IN, sender_contact=contact)

        self.stdout.write(f"Контакт: {contact.id}")
        self.stdout.write(f"Диалогов: {len(conv_ids)}")
        self.stdout.write(f"IN сообщений контакта: {in_msg_qs.count()}")

        if delete_attachments:
            att_qs = MessageAttachment.objects.filter(message__in=in_msg_qs)
            self.stdout.write(f"Вложений к IN сообщениям: {att_qs.count()}")

        if dry_run:
            self.stdout.write(self.style.WARNING("dry-run: изменений не сделано."))
            return

        with transaction.atomic():
            # 1) Анонимизируем Contact
            contact.external_id = new_external_id
            contact.name = ""
            contact.email = ""
            contact.phone = ""
            contact.save(update_fields=["external_id", "name", "email", "phone"])

            # 2) Опционально редактируем сообщения
            if redact_messages:
                in_msg_qs.update(body="[redacted]")

            # 3) Опционально удаляем вложения (и файлы)
            if delete_attachments:
                atts = list(MessageAttachment.objects.filter(message__in=in_msg_qs).select_related("message"))
                for att in atts:
                    try:
                        # удаляем файл из storage
                        if att.file:
                            att.file.delete(save=False)
                    except Exception:
                        pass
                MessageAttachment.objects.filter(id__in=[a.id for a in atts]).delete()

        self.stdout.write(self.style.SUCCESS("Анонимизация выполнена."))

