from django.db import migrations


class Migration(migrations.Migration):
    """
    Merge conflicting 0011 migrations:
    - 0011_globalmailaccount_per_user_daily_limit
    - 0011_rename_mailer_emai_created_7b9a3d_idx_mailer_emai_created_817c96_idx_and_more
    """

    dependencies = [
        ("mailer", "0011_globalmailaccount_per_user_daily_limit"),
        ("mailer", "0011_rename_mailer_emai_created_7b9a3d_idx_mailer_emai_created_817c96_idx_and_more"),
    ]

    operations = [
    ]

