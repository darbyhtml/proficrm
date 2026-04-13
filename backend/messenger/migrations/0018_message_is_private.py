# Generated manually for Task 5: Live-chat Backend Foundation.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("messenger", "0017_conversation_transfer"),
    ]

    operations = [
        migrations.AddField(
            model_name="message",
            name="is_private",
            field=models.BooleanField(
                db_index=True,
                default=False,
                verbose_name="Приватная заметка (видна только сотрудникам)",
            ),
        ),
        migrations.AddIndex(
            model_name="message",
            index=models.Index(
                fields=["conversation", "is_private", "created_at"],
                name="msg_conv_private_idx",
            ),
        ),
    ]
