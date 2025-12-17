from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("mailer", "0005_alter_globalmailaccount_rate_per_day_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="globalmailaccount",
            name="from_email",
            field=models.EmailField(blank=True, default="no-reply@groupprofi.ru", max_length=254, verbose_name="Email отправителя (From)"),
        ),
    ]


