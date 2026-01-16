# Generated manually for ЭТАП 3

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('phonebridge', '0006_mobileappbuild_mobileappqrtoken'),
    ]

    operations = [
        migrations.AddField(
            model_name='callrequest',
            name='call_ended_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='Время окончания звонка'),
        ),
        migrations.AddField(
            model_name='callrequest',
            name='direction',
            field=models.CharField(blank=True, choices=[('outgoing', 'Исходящий'), ('incoming', 'Входящий'), ('missed', 'Пропущенный'), ('unknown', 'Неизвестно')], db_index=True, max_length=16, null=True, verbose_name='Направление звонка'),
        ),
        migrations.AddField(
            model_name='callrequest',
            name='resolve_method',
            field=models.CharField(blank=True, choices=[('observer', 'Определено через ContentObserver'), ('retry', 'Определено через повторные проверки'), ('unknown', 'Неизвестно')], db_index=True, max_length=16, null=True, verbose_name='Метод определения результата'),
        ),
        migrations.AddField(
            model_name='callrequest',
            name='attempts_count',
            field=models.IntegerField(blank=True, null=True, verbose_name='Количество попыток определения'),
        ),
        migrations.AddField(
            model_name='callrequest',
            name='action_source',
            field=models.CharField(blank=True, choices=[('crm_ui', 'Команда из CRM'), ('notification', 'Нажатие на уведомление'), ('history', 'Нажатие из истории звонков'), ('unknown', 'Неизвестно')], db_index=True, max_length=16, null=True, verbose_name='Источник действия пользователя'),
        ),
    ]
