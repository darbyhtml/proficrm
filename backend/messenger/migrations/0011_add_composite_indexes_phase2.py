# Миграция для Фазы 2: Составные индексы для производительности (по образцу Chatwoot)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('messenger', '0010_migrate_data_phase1'),
    ]

    operations = [
        # Критически важный индекс для списка диалогов (по образцу Chatwoot)
        # Используется в ConversationViewSet для фильтрации по inbox, status, assignee
        migrations.AddIndex(
            model_name='conversation',
            index=models.Index(
                fields=['inbox', 'status', 'assignee'],
                name='msg_conv_inbox_st_assign_idx',
            ),
        ),
        
        # Индекс для сортировки по статусу и приоритету (по образцу Chatwoot)
        # Используется для фильтрации и сортировки диалогов
        migrations.AddIndex(
            model_name='conversation',
            index=models.Index(
                fields=['status', 'priority'],
                name='msg_conv_status_priority_idx',
            ),
        ),
        
        # Индекс для фильтрации по branch и статусу (улучшение существующего)
        # Добавляем assignee для более точной фильтрации
        migrations.AddIndex(
            model_name='conversation',
            index=models.Index(
                fields=['branch', 'status', 'assignee'],
                name='msg_conv_branch_st_assign_idx',
            ),
        ),
        
        # Индекс для поиска диалогов по контакту и inbox (для виджета)
        migrations.AddIndex(
            model_name='conversation',
            index=models.Index(
                fields=['contact', 'inbox', 'status'],
                name='msg_conv_cont_inbox_st_idx',
            ),
        ),
        
        # Индекс для сообщений по контакту-отправителю (для last_activity_at контакта)
        migrations.AddIndex(
            model_name='message',
            index=models.Index(
                fields=['sender_contact', 'direction', 'created_at'],
                name='msg_msg_cont_dir_crt_idx',
            ),
        ),
        
        # Индекс для сообщений по пользователю-отправителю (для статистики)
        migrations.AddIndex(
            model_name='message',
            index=models.Index(
                fields=['sender_user', 'direction', 'created_at'],
                name='msg_msg_user_dir_crt_idx',
            ),
        ),
    ]
