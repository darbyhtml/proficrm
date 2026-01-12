# Generated manually to resolve migration conflict

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('companies', '0024_alter_company_lead_state_and_more'),
        ('companies', '0025_contactphone_cold_call_fields'),
    ]

    operations = [
        # Empty merge migration - just resolves the conflict
    ]
