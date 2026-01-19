from django.db import migrations


class Migration(migrations.Migration):
    """
    Merge conflicting migrations:
    - 0002_magic_link_token
    - 0005_magic_link_token

    На серверах могли появиться оба файла/ветки (из-за ручных/локальных миграций).
    Эта миграция просто объединяет граф, операций нет.
    """

    dependencies = [
        ("accounts", "0002_magic_link_token"),
        ("accounts", "0005_magic_link_token"),
    ]

    operations = []

