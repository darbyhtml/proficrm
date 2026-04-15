"""
Уникальность (parent_recurring_task, due_at) для защиты от race condition
в generate_recurring_tasks. Условный constraint применяется только к
сгенерированным экземплярам (parent_recurring_task IS NOT NULL), чтобы
не мешать ручному созданию задач.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tasksapp", "0012_add_recurrence_fields"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="task",
            constraint=models.UniqueConstraint(
                fields=["parent_recurring_task", "due_at"],
                condition=models.Q(parent_recurring_task__isnull=False),
                name="uniq_task_recurrence_occurrence",
            ),
        ),
    ]
