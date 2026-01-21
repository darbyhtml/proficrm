"""
ВАЖНО:
В репозитории исторически оказались две миграции, создающие одну и ту же таблицу
MagicLinkToken: 0002_magic_link_token и 0005_magic_link_token (дубликат).

В проде и в тестовой БД это приводило к ошибке:
  psycopg.errors.DuplicateTable: relation "accounts_magiclinktoken" already exists

Эта миграция оставлена как no-op для совместимости (ничего не делает).
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0004_user_email_signature_html"),
    ]

    operations = []
