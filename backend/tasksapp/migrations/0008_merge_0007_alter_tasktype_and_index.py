# Merge migration to resolve conflict between two 0007 migrations

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('tasksapp', '0007_alter_tasktype_color_alter_tasktype_icon'),
        ('tasksapp', '0007_task_company_due_status_index'),
    ]

    operations = [
        # Empty migration - just merges the two branches
    ]
