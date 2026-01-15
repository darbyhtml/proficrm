# Generated manually for MobileAppBuild and MobileAppQrToken models

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone
from datetime import timedelta
import uuid


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('phonebridge', '0005_phonedevice_encryption_enabled'),
    ]

    operations = [
        migrations.CreateModel(
            name='MobileAppBuild',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('env', models.CharField(db_index=True, default='production', help_text='Только production', max_length=16)),
                ('version_name', models.CharField(max_length=32, verbose_name='Версия (name)')),
                ('version_code', models.IntegerField(verbose_name='Версия (code)')),
                ('file', models.FileField(upload_to='mobile_apps/', verbose_name='APK файл')),
                ('sha256', models.CharField(blank=True, max_length=64, verbose_name='SHA256 хеш')),
                ('uploaded_at', models.DateTimeField(auto_now_add=True, verbose_name='Дата загрузки')),
                ('is_active', models.BooleanField(db_index=True, default=True, help_text='Показывать в списке', verbose_name='Активна')),
                ('uploaded_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='uploaded_app_builds', to=settings.AUTH_USER_MODEL, verbose_name='Загрузил')),
            ],
            options={
                'ordering': ['-uploaded_at'],
            },
        ),
        migrations.CreateModel(
            name='MobileAppQrToken',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('token', models.CharField(db_index=True, max_length=128, unique=True, verbose_name='Токен')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Создан')),
                ('expires_at', models.DateTimeField(verbose_name='Истекает')),
                ('used_at', models.DateTimeField(blank=True, null=True, verbose_name='Использован')),
                ('ip_address', models.GenericIPAddressField(blank=True, null=True, verbose_name='IP адрес')),
                ('user_agent', models.CharField(blank=True, default='', max_length=255, verbose_name='User-Agent')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='qr_tokens', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='mobileappbuild',
            index=models.Index(fields=['env', 'is_active', '-uploaded_at'], name='phonebridge_env_is_ac_upload_idx'),
        ),
        migrations.AddIndex(
            model_name='mobileappqrtoken',
            index=models.Index(fields=['token'], name='phonebridge_token_idx'),
        ),
        migrations.AddIndex(
            model_name='mobileappqrtoken',
            index=models.Index(fields=['user', '-created_at'], name='phonebridge_user_creat_idx'),
        ),
        migrations.AddIndex(
            model_name='mobileappqrtoken',
            index=models.Index(fields=['expires_at'], name='phonebridge_expires_idx'),
        ),
    ]
