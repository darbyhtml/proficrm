from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0007_rename_accounts_m_token_h_abc123_idx_accounts_ma_token_h_1115dc_idx_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="avatar",
            field=models.ImageField(
                blank=True,
                null=True,
                upload_to="users/avatars/",
                verbose_name="Фото профиля",
            ),
        ),
    ]
