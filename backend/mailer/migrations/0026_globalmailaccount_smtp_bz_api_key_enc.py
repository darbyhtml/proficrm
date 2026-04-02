# Generated manually on 2026-03-14
#
# Шифрует API-ключ smtp.bz (ранее хранился в открытом виде в smtp_bz_api_key).
# Добавляет новое поле smtp_bz_api_key_enc и переносит данные.

from django.db import migrations, models


def encrypt_existing_keys(apps, schema_editor):
    """Переносит открытый ключ в зашифрованный формат."""
    GlobalMailAccount = apps.get_model("mailer", "GlobalMailAccount")
    try:
        from mailer.crypto import encrypt_str
    except Exception:
        return  # MAILER_FERNET_KEY не задан — пропускаем (ключей нет или нет Fernet)

    for obj in GlobalMailAccount.objects.all():
        raw = (getattr(obj, "_old_api_key", "") or "").strip()
        if raw:
            try:
                obj.smtp_bz_api_key_enc = encrypt_str(raw)
                obj.save(update_fields=["smtp_bz_api_key_enc"])
            except Exception:
                pass  # если ключ шифрования не задан — оставляем пустым, не падаем


class Migration(migrations.Migration):

    dependencies = [
        ("mailer", "0025_campaignqueue_deferred_index"),
    ]

    operations = [
        migrations.AddField(
            model_name="globalmailaccount",
            name="smtp_bz_api_key_enc",
            field=models.TextField(
                verbose_name="API ключ smtp.bz (зашифрован)",
                blank=True,
                default="",
                help_text="API ключ для получения информации о тарифе и квоте (Fernet)",
            ),
        ),
    ]
