# Хешированное хранение QR-токенов
import hashlib
from django.db import migrations, models


def forwards(apps, schema_editor):
    """Вычисляем token_hash для всех существующих токенов."""
    QrToken = apps.get_model('phonebridge', 'MobileAppQrToken')
    for qt in QrToken.objects.filter(token_hash=''):
        qt.token_hash = hashlib.sha256(qt.token.encode()).hexdigest()
        qt.save(update_fields=['token_hash'])


class Migration(migrations.Migration):

    dependencies = [
        ('phonebridge', '0009_alter_mobileappbuild_file'),
    ]

    operations = [
        # Шаг 1: добавляем поле token_hash (без unique, чтобы data migration прошла)
        migrations.AddField(
            model_name='mobileappqrtoken',
            name='token_hash',
            field=models.CharField(
                blank=True, default='',
                max_length=64, verbose_name='Хеш токена',
            ),
            preserve_default=False,
        ),
        # Шаг 2: заполняем хеши для существующих записей
        migrations.RunPython(forwards, migrations.RunPython.noop),
        # Шаг 3: делаем поле unique (unique создаёт index автоматически)
        migrations.AlterField(
            model_name='mobileappqrtoken',
            name='token_hash',
            field=models.CharField(
                blank=True, db_index=True, max_length=64,
                unique=True, verbose_name='Хеш токена',
            ),
        ),
    ]
