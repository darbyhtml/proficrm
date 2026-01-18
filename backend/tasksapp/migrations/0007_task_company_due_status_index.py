# Generated manually for performance optimization
# Добавляет индекс для оптимизации запросов has_overdue в списке компаний

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tasksapp', '0006_default_status_tasktypes'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='task',
            index=models.Index(
                fields=['company', 'due_at', 'status'],
                name='tasksapp_task_company_due_status_idx',
            ),
        ),
    ]
