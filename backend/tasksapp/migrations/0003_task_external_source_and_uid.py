from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tasksapp", "0002_task_updated_at_alter_task_assigned_to_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="task",
            name="external_source",
            field=models.CharField(blank=True, db_index=True, default="", max_length=32, verbose_name="Внешний источник"),
        ),
        migrations.AddField(
            model_name="task",
            name="external_uid",
            field=models.CharField(blank=True, db_index=True, default="", max_length=120, verbose_name="Внешний UID"),
        ),
    ]


