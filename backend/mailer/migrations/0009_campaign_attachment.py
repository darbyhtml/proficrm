# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('mailer', '0008_campaign_sender_name'),
    ]

    operations = [
        migrations.AddField(
            model_name='campaign',
            name='attachment',
            field=models.FileField(blank=True, help_text='Файл, который будет прикреплен ко всем письмам кампании', null=True, upload_to='campaign_attachments/%Y/%m/', verbose_name='Вложение'),
        ),
    ]
