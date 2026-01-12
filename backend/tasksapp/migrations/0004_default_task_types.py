from django.db import migrations


def create_default_task_types(apps, schema_editor):
    TaskType = apps.get_model("tasksapp", "TaskType")
    defaults = [
        "Перезвонить клиенту",
        "Отправить КП",
        "Проверить оплату",
        "Назначить встречу",
        "Подготовить документы",
    ]
    for name in defaults:
        TaskType.objects.get_or_create(name=name)


def delete_default_task_types(apps, schema_editor):
    TaskType = apps.get_model("tasksapp", "TaskType")
    defaults = [
        "Перезвонить клиенту",
        "Отправить КП",
        "Проверить оплату",
        "Назначить встречу",
        "Подготовить документы",
    ]
    TaskType.objects.filter(name__in=defaults).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("tasksapp", "0003_task_external_source_and_uid"),
    ]

    operations = [
        migrations.RunPython(create_default_task_types, delete_default_task_types),
    ]

