from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("ui", "0006_uiuserpreference_company_list_view_mode"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="uiuserpreference",
            name="company_list_view_mode",
        ),
    ]
