# Generated 2026-03-15 — encrypt AmoApiConfig OAuth tokens with Fernet (same key as mailer).
# Strategy:
#   1. Add *_enc fields (keep old plaintext fields temporarily)
#   2. Data-migrate: encrypt existing values into *_enc fields (skips if MAILER_FERNET_KEY not set)
#   3. Remove old plaintext fields

from django.db import migrations, models


def encrypt_existing_tokens(apps, schema_editor):
    """Copy plaintext tokens into encrypted columns if MAILER_FERNET_KEY is available."""
    try:
        from mailer.crypto import encrypt_str
    except Exception:
        # If crypto not available (e.g. key missing in dev), skip silently.
        return

    AmoApiConfig = apps.get_model("ui", "AmoApiConfig")
    for obj in AmoApiConfig.objects.all():
        changed = False
        for plain_field, enc_field in [
            ("access_token", "access_token_enc"),
            ("refresh_token", "refresh_token_enc"),
            ("long_lived_token", "long_lived_token_enc"),
        ]:
            plain_value = getattr(obj, plain_field, "") or ""
            if plain_value:
                try:
                    setattr(obj, enc_field, encrypt_str(plain_value))
                    changed = True
                except Exception:
                    pass
        if changed:
            obj.save(update_fields=["access_token_enc", "refresh_token_enc", "long_lived_token_enc"])


class Migration(migrations.Migration):

    dependencies = [
        ('ui', '0009_uiuserpreference_task_prefs'),
    ]

    operations = [
        # Step 1: Add encrypted columns (keep old plaintext columns for now)
        migrations.AddField(
            model_name='amoapiconfig',
            name='access_token_enc',
            field=models.TextField(blank=True, default='', verbose_name='Access token (зашифрован)'),
        ),
        migrations.AddField(
            model_name='amoapiconfig',
            name='refresh_token_enc',
            field=models.TextField(blank=True, default='', verbose_name='Refresh token (зашифрован)'),
        ),
        migrations.AddField(
            model_name='amoapiconfig',
            name='long_lived_token_enc',
            field=models.TextField(blank=True, default='', verbose_name='Долгосрочный токен (зашифрован)'),
        ),
        # Step 2: Data migration — encrypt existing tokens
        migrations.RunPython(encrypt_existing_tokens, migrations.RunPython.noop),
        # Step 3: Remove old plaintext columns
        migrations.RemoveField(
            model_name='amoapiconfig',
            name='access_token',
        ),
        migrations.RemoveField(
            model_name='amoapiconfig',
            name='refresh_token',
        ),
        migrations.RemoveField(
            model_name='amoapiconfig',
            name='long_lived_token',
        ),
    ]
