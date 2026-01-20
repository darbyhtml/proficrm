# Generated manually

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('mailer', '0014_smtp_bz_quota_and_queue'),
    ]

    operations = [
        migrations.CreateModel(
            name='UserDailyLimitStatus',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('last_limit_reached_date', models.DateField(blank=True, help_text='Дата, когда пользователь в последний раз достиг дневного лимита', null=True, verbose_name='Дата последнего достижения лимита')),
                ('last_notified_date', models.DateField(blank=True, help_text='Дата, когда было отправлено последнее уведомление об обновлении лимита', null=True, verbose_name='Дата последнего уведомления')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Обновлено')),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='daily_limit_status', to=settings.AUTH_USER_MODEL, verbose_name='Пользователь')),
            ],
            options={
                'verbose_name': 'Статус дневного лимита пользователя',
                'verbose_name_plural': 'Статусы дневных лимитов пользователей',
            },
        ),
        migrations.AddIndex(
            model_name='userdailylimitstatus',
            index=models.Index(fields=['user', 'last_limit_reached_date'], name='mailer_user_user_id_2a3b4c_idx'),
        ),
    ]
