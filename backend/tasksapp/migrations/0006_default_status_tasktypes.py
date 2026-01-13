from django.db import migrations


def create_default_status_types(apps, schema_editor):
    TaskType = apps.get_model("tasksapp", "TaskType")

    defaults = [
        # name, icon, color
        ("Связаться", "phone", "badge-blue"),
        ("Отправить прайс", "document", "badge-amber"),
        ("Конкурс прошел?", "question", "badge-purple"),
        ("Обучение актуально?", "education", "badge-teal"),
        ("Отправить заявку", "send", "badge-green"),
    ]

    for name, icon, color in defaults:
        obj, created = TaskType.objects.get_or_create(name=name, defaults={"icon": icon, "color": color})
        if not created:
            # Обновляем только пустые поля, чтобы не ломать ручные настройки
            changed = False
            if not getattr(obj, "icon", None):
                obj.icon = icon
                changed = True
            if not getattr(obj, "color", None):
                obj.color = color
                changed = True
            if changed:
                obj.save(update_fields=["icon", "color"])


def noop_reverse(apps, schema_editor):
    # Не удаляем статусы при откате, чтобы не потерять данные
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("tasksapp", "0005_tasktype_icon_color"),
    ]

    operations = [
        migrations.RunPython(create_default_status_types, noop_reverse),
    ]

