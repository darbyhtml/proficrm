# Шифрование client_secret в AmoApiConfig (Fernet, ключ MAILER_FERNET_KEY).
# Стратегия:
#   1. Добавляем поле client_secret_enc (TextField)
#   2. Data-миграция: шифруем существующее plaintext-значение, очищаем plaintext
#   Поле client_secret (CharField) остаётся для обратной совместимости.

from django.db import migrations, models


def encrypt_existing_secret(apps, schema_editor):
    """Шифруем существующий plaintext client_secret в client_secret_enc."""
    try:
        from core.crypto import encrypt_str
    except Exception:
        # Ключ не задан (dev-среда) — пропускаем
        return

    AmoApiConfig = apps.get_model("ui", "AmoApiConfig")
    for obj in AmoApiConfig.objects.all():
        plain = obj.client_secret or ""
        if plain:
            try:
                obj.client_secret_enc = encrypt_str(plain)
                obj.client_secret = ""  # очищаем plaintext
                obj.save(update_fields=["client_secret_enc", "client_secret"])
            except Exception:
                pass


def decrypt_back(apps, schema_editor):
    """Обратная миграция: расшифровываем обратно в plaintext."""
    try:
        from core.crypto import decrypt_str
    except Exception:
        return

    AmoApiConfig = apps.get_model("ui", "AmoApiConfig")
    for obj in AmoApiConfig.objects.all():
        enc = obj.client_secret_enc or ""
        if enc:
            try:
                obj.client_secret = decrypt_str(enc)
                obj.client_secret_enc = ""
                obj.save(update_fields=["client_secret", "client_secret_enc"])
            except Exception:
                pass


class Migration(migrations.Migration):

    dependencies = [
        ("ui", "0012_uiuserpreference_per_page"),
    ]

    operations = [
        # Шаг 1: добавляем зашифрованное поле
        migrations.AddField(
            model_name="amoapiconfig",
            name="client_secret_enc",
            field=models.TextField(
                blank=True,
                default="",
                verbose_name="Client Secret (зашифрован)",
            ),
        ),
        # Шаг 2: шифруем существующее значение, очищаем plaintext
        migrations.RunPython(encrypt_existing_secret, decrypt_back),
    ]
