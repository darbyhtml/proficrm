# Generated manually

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('notifications', '0002_company_contract_reminders'),
    ]

    operations = [
        migrations.AddField(
            model_name='notification',
            name='payload',
            field=models.JSONField(blank=True, default=dict, null=True, verbose_name='Дополнительные данные'),
        ),
    ]
