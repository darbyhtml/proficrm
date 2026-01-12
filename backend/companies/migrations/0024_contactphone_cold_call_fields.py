# Generated manually

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('companies', '0023_normalize_existing_phone_numbers'),
        ('phonebridge', '0002_callrequest_is_cold_call'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='contactphone',
            name='is_cold_call',
            field=models.BooleanField(db_index=True, default=False, verbose_name='Холодный звонок'),
        ),
        migrations.AddField(
            model_name='contactphone',
            name='cold_marked_at',
            field=models.DateTimeField(blank=True, db_index=True, null=True, verbose_name='Холодный: когда отметили'),
        ),
        migrations.AddField(
            model_name='contactphone',
            name='cold_marked_by',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='contact_phone_cold_marks',
                to=settings.AUTH_USER_MODEL,
                verbose_name='Холодный: кто отметил'
            ),
        ),
        migrations.AddField(
            model_name='contactphone',
            name='cold_marked_call',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='+',
                to='phonebridge.callrequest',
                verbose_name='Холодный: звонок'
            ),
        ),
    ]
