from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("mailer", "0004_global_mail_account_smtpbz_defaults"),
    ]

    operations = [
        migrations.AlterField(
            model_name="globalmailaccount",
            name="smtp_host",
            field=models.CharField(default="connect.smtp.bz", max_length=255, verbose_name="SMTP host"),
        ),
        migrations.AlterField(
            model_name="globalmailaccount",
            name="rate_per_minute",
            field=models.PositiveIntegerField(default=1, verbose_name="Лимит писем в минуту"),
        ),
        migrations.AlterField(
            model_name="globalmailaccount",
            name="rate_per_day",
            field=models.PositiveIntegerField(default=15000, verbose_name="Лимит писем в день"),
        ),
    ]


