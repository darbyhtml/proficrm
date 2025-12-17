from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("mailer", "0007_global_mail_account_from_email_seed"),
    ]

    operations = [
        migrations.AddField(
            model_name="campaign",
            name="sender_name",
            field=models.CharField(blank=True, default="", max_length=120, verbose_name="Имя отправителя"),
        ),
    ]


