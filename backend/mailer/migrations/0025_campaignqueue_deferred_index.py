# Generated manually on 2026-03-14

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('mailer', '0024_campaignrecipient_last_error_500'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='campaignqueue',
            index=models.Index(fields=['status', 'deferred_until', 'priority', 'queued_at'], name='mailer_camp_status_deferred_idx'),
        ),
    ]
