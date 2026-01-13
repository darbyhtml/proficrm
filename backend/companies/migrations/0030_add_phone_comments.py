# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('companies', '0029_companyphone_cold_call_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='company',
            name='phone_comment',
            field=models.CharField(blank=True, default='', help_text='Комментарий к основному номеру телефона', max_length=255, verbose_name='Комментарий к основному телефону'),
        ),
        migrations.AddField(
            model_name='companyphone',
            name='comment',
            field=models.CharField(blank=True, default='', help_text='Комментарий к номеру телефона', max_length=255, verbose_name='Комментарий'),
        ),
        migrations.AddField(
            model_name='contactphone',
            name='comment',
            field=models.CharField(blank=True, default='', help_text='Комментарий к номеру телефона', max_length=255, verbose_name='Комментарий'),
        ),
    ]
