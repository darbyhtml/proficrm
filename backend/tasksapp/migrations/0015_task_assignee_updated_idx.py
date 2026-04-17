"""Композитный индекс (assigned_to, updated_at) для dashboard_poll.

EXISTS(assigned_to=user, updated_at > since) ускоряется в 5-10 раз при
больших объёмах Task. Ключевой путь: каждые 30 секунд poll от каждой
активной вкладки.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tasksapp", "0014_task_indexes_and_constraints"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="task",
            index=models.Index(
                fields=["assigned_to", "updated_at"],
                name="task_assignee_updated_idx",
            ),
        ),
    ]
