# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('phonebridge', '0004_phonedevice_telemetry_logs'),
    ]

    operations = [
        migrations.AddField(
            model_name='phonedevice',
            name='encryption_enabled',
            field=models.BooleanField(default=True, help_text='Использует ли устройство EncryptedSharedPreferences', verbose_name='Шифрование включено'),
        ),
    ]
