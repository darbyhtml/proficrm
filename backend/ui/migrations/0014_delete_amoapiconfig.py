"""Drop AmoApiConfig model — amoCRM cleanup 2026-04-21.

amoCRM subscription expired, integration code is dead.
Table dropped with data — configurational only (OAuth tokens + domain).
Historical amocrm_company_id / amocrm_contact_id fields on Company/Contact
remain intact (preserved per Path E decision — W9 accumulated deploy).

See: docs/decisions/2026-04-21-remove-amocrm.md
"""

from __future__ import annotations

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("ui", "0013_amoapi_client_secret_enc"),
    ]

    operations = [
        migrations.DeleteModel(name="AmoApiConfig"),
    ]
