# Generated manually for Magic Link Authentication

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0004_user_email_signature_html'),
    ]

    operations = [
        migrations.CreateModel(
            name='MagicLinkToken',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('token_hash', models.CharField(db_index=True, max_length=64, unique=True, verbose_name='Хэш токена')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True, verbose_name='Создан')),
                ('expires_at', models.DateTimeField(db_index=True, verbose_name='Истекает')),
                ('used_at', models.DateTimeField(blank=True, db_index=True, null=True, verbose_name='Использован')),
                ('ip_address', models.GenericIPAddressField(blank=True, null=True, verbose_name='IP адрес при использовании')),
                ('user_agent', models.CharField(blank=True, default='', max_length=255, verbose_name='User-Agent при использовании')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='created_magic_links', to=settings.AUTH_USER_MODEL, verbose_name='Создан администратором')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='magic_link_tokens', to=settings.AUTH_USER_MODEL, verbose_name='Пользователь')),
            ],
            options={
                'verbose_name': 'Токен входа',
                'verbose_name_plural': 'Токены входа',
            },
        ),
        migrations.AddIndex(
            model_name='magiclinktoken',
            index=models.Index(fields=['token_hash'], name='accounts_m_token_h_abc123_idx'),
        ),
        migrations.AddIndex(
            model_name='magiclinktoken',
            index=models.Index(fields=['user', 'expires_at', 'used_at'], name='accounts_m_user_id_xyz789_idx'),
        ),
    ]
