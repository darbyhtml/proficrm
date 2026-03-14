# Generated manually on 2026-03-14
#
# Удаляет поле smtp_bz_api_key (открытый текст) из GlobalMailAccount,
# заменённое на smtp_bz_api_key_enc (Fernet-шифрование) в миграции 0026.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("mailer", "0026_globalmailaccount_smtp_bz_api_key_enc"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="globalmailaccount",
            name="smtp_bz_api_key",
        ),
    ]
