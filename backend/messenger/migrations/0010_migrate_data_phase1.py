# Миграция данных для Фазы 1: заполнение новых полей из существующих данных

from django.db import migrations, models


def migrate_last_activity_at(apps, schema_editor):
    """Заполнить last_activity_at из created_at для Conversation."""
    Conversation = apps.get_model('messenger', 'Conversation')
    # Обновляем только те записи, где last_activity_at пусто
    Conversation.objects.filter(
        last_activity_at__isnull=True
    ).update(
        last_activity_at=models.F('created_at')
    )


def migrate_waiting_since(apps, schema_editor):
    """Установить waiting_since для открытых диалогов без назначения."""
    Conversation = apps.get_model('messenger', 'Conversation')
    # Устанавливаем waiting_since для открытых/ожидающих диалогов без назначения
    Conversation.objects.filter(
        status__in=['open', 'pending'],
        assignee_id__isnull=True,
        waiting_since__isnull=True
    ).update(
        waiting_since=models.F('created_at')
    )
    # Для назначенных диалогов используем assignee_assigned_at или created_at
    Conversation.objects.filter(
        assignee_id__isnull=False,
        waiting_since__isnull=True
    ).update(
        waiting_since=models.F('assignee_assigned_at')
    )
    # Если assignee_assigned_at тоже пусто, используем created_at
    Conversation.objects.filter(
        waiting_since__isnull=True
    ).update(
        waiting_since=models.F('created_at')
    )


def migrate_contact_last_activity(apps, schema_editor):
    """Заполнить last_activity_at для Contact из последнего сообщения."""
    Contact = apps.get_model('messenger', 'Contact')
    Message = apps.get_model('messenger', 'Message')
    
    # Для каждого контакта находим последнее входящее сообщение
    for contact in Contact.objects.filter(last_activity_at__isnull=True):
        last_message = Message.objects.filter(
            sender_contact=contact,
            direction='in'
        ).order_by('-created_at').first()
        
        if last_message:
            Contact.objects.filter(pk=contact.pk).update(
                last_activity_at=last_message.created_at
            )
        else:
            # Если нет сообщений, используем created_at контакта
            Contact.objects.filter(pk=contact.pk).update(
                last_activity_at=models.F('created_at')
            )


def migrate_first_reply(apps, schema_editor):
    """Установить first_reply_created_at для диалогов с ответами."""
    Conversation = apps.get_model('messenger', 'Conversation')
    Message = apps.get_model('messenger', 'Message')
    
    # Для каждого диалога находим первое исходящее сообщение от пользователя
    for conversation in Conversation.objects.filter(first_reply_created_at__isnull=True):
        first_reply = Message.objects.filter(
            conversation=conversation,
            direction='out',
            sender_user__isnull=False
        ).order_by('created_at', 'id').first()
        
        if first_reply:
            Conversation.objects.filter(pk=conversation.pk).update(
                first_reply_created_at=first_reply.created_at
            )


class Migration(migrations.Migration):

    dependencies = [
        ('messenger', '0009_add_critical_fields_phase1'),
    ]

    operations = [
        migrations.RunPython(
            code=migrate_last_activity_at,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.RunPython(
            code=migrate_waiting_since,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.RunPython(
            code=migrate_contact_last_activity,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.RunPython(
            code=migrate_first_reply,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
