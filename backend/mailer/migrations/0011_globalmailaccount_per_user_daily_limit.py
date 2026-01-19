from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("mailer", "0010_email_cooldown"),
    ]

    operations = [
        migrations.AddField(
            model_name="globalmailaccount",
            name="per_user_daily_limit",
            field=models.PositiveIntegerField(
                verbose_name="Лимит писем в день на менеджера",
                default=100,
            ),
        ),
    ]

