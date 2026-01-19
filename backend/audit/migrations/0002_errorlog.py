# Generated manually

from django.conf import settings
import uuid
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('audit', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='ErrorLog',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('level', models.CharField(choices=[('error', 'Ошибка'), ('warning', 'Предупреждение'), ('critical', 'Критическая'), ('exception', 'Исключение')], db_index=True, default='error', max_length=16, verbose_name='Уровень')),
                ('message', models.TextField(blank=True, default='', verbose_name='Сообщение')),
                ('exception_type', models.CharField(blank=True, db_index=True, default='', max_length=255, verbose_name='Тип исключения')),
                ('traceback', models.TextField(blank=True, default='', verbose_name='Трассировка')),
                ('path', models.CharField(blank=True, db_index=True, default='', max_length=500, verbose_name='Путь')),
                ('method', models.CharField(blank=True, db_index=True, default='', max_length=10, verbose_name='Метод')),
                ('user_agent', models.CharField(blank=True, default='', max_length=500, verbose_name='User-Agent')),
                ('ip_address', models.GenericIPAddressField(blank=True, db_index=True, null=True, verbose_name='IP адрес')),
                ('request_data', models.JSONField(blank=True, default=dict, verbose_name='Данные запроса')),
                ('resolved', models.BooleanField(db_index=True, default=False, verbose_name='Исправлено')),
                ('resolved_at', models.DateTimeField(blank=True, null=True, verbose_name='Когда исправлено')),
                ('notes', models.TextField(blank=True, default='', verbose_name='Заметки')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True, verbose_name='Когда произошло')),
                ('resolved_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='resolved_errors', to=settings.AUTH_USER_MODEL, verbose_name='Исправил')),
                ('user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='error_logs', to=settings.AUTH_USER_MODEL, verbose_name='Пользователь')),
            ],
            options={
                'verbose_name': 'Ошибка',
                'verbose_name_plural': 'Ошибки',
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='errorlog',
            index=models.Index(fields=['-created_at', 'resolved'], name='audit_error_created_idx'),
        ),
        migrations.AddIndex(
            model_name='errorlog',
            index=models.Index(fields=['level', 'resolved'], name='audit_error_level_res_idx'),
        ),
        migrations.AddIndex(
            model_name='errorlog',
            index=models.Index(fields=['path', 'resolved'], name='audit_error_path_res_idx'),
        ),
    ]
