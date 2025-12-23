from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0003_alter_user_role"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="email_signature_html",
            field=models.TextField(blank=True, default="", verbose_name="Подпись в письме (HTML)"),
        ),
    ]


