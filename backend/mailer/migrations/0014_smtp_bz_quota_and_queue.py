# Generated manually

from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ('mailer', '0013_campaign_paused_status'),
    ]

    operations = [
        # Добавляем поле API ключа в GlobalMailAccount
        migrations.AddField(
            model_name='globalmailaccount',
            name='smtp_bz_api_key',
            field=models.CharField(blank=True, default='', help_text='API ключ для получения информации о тарифе и квоте', max_length=255, verbose_name='API ключ smtp.bz'),
        ),
        
        # Создаем модель SmtpBzQuota
        migrations.CreateModel(
            name='SmtpBzQuota',
            fields=[
                ('id', models.IntegerField(default=1, editable=False, primary_key=True, serialize=False)),
                ('tariff_name', models.CharField(blank=True, default='', max_length=50, verbose_name='Название тарифа')),
                ('tariff_renewal_date', models.DateField(blank=True, null=True, verbose_name='Дата продления тарифа')),
                ('emails_available', models.PositiveIntegerField(default=0, verbose_name='Доступно писем')),
                ('emails_limit', models.PositiveIntegerField(default=0, verbose_name='Лимит писем')),
                ('sent_per_hour', models.PositiveIntegerField(default=0, verbose_name='Отправлено за час')),
                ('max_per_hour', models.PositiveIntegerField(default=100, verbose_name='Максимум в час')),
                ('last_synced_at', models.DateTimeField(blank=True, null=True, verbose_name='Последняя синхронизация')),
                ('sync_error', models.TextField(blank=True, default='', verbose_name='Ошибка синхронизации')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Создано')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Обновлено')),
            ],
            options={
                'verbose_name': 'Квота smtp.bz',
                'verbose_name_plural': 'Квота smtp.bz',
            },
        ),
        
        # Создаем модель CampaignQueue
        migrations.CreateModel(
            name='CampaignQueue',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('status', models.CharField(choices=[('pending', 'В очереди'), ('processing', 'Обрабатывается'), ('completed', 'Завершена'), ('cancelled', 'Отменена')], default='pending', max_length=16, verbose_name='Статус')),
                ('priority', models.IntegerField(default=0, help_text='Чем выше число, тем выше приоритет', verbose_name='Приоритет')),
                ('queued_at', models.DateTimeField(auto_now_add=True, verbose_name='Поставлено в очередь')),
                ('started_at', models.DateTimeField(blank=True, null=True, verbose_name='Начало обработки')),
                ('completed_at', models.DateTimeField(blank=True, null=True, verbose_name='Завершено')),
                ('campaign', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='queue_entry', to='mailer.campaign', verbose_name='Кампания')),
            ],
            options={
                'ordering': ['-priority', 'queued_at'],
            },
        ),
        migrations.AddIndex(
            model_name='campaignqueue',
            index=models.Index(fields=['status', 'priority', 'queued_at'], name='mailer_camp_status_priority_idx'),
        ),
    ]
