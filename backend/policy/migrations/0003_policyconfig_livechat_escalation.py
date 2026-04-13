from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("policy", "0002_rename_policy_indexes"),
    ]

    operations = [
        migrations.AddField(
            model_name="policyconfig",
            name="livechat_escalation",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text=(
                    "Ключи: warn_min, urgent_min, rop_alert_min, pool_return_min. "
                    "Пустые значения → дефолты 3/10/20/40."
                ),
                verbose_name="Пороги эскалации live-chat (минуты)",
            ),
        ),
    ]
