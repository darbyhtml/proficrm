# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tasksapp', '0008_remove_task_tasksapp_task_company_due_status_idx_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='task',
            name='is_urgent',
            field=models.BooleanField(db_index=True, default=False, verbose_name='Срочно'),
        ),
    ]
