from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("ui", "0007_remove_uiuserpreference_company_list_view_mode"),
    ]

    operations = [
        migrations.AddField(
            model_name="uiuserpreference",
            name="company_detail_view_mode",
            field=models.CharField(
                choices=[("classic", "Классический"), ("modern", "Современный")],
                default="classic",
                help_text="Режим отображения карточки компании: классический (старый layout) или современный (новый layout)",
                max_length=20,
                verbose_name="Режим просмотра карточки компании",
            ),
        ),
    ]
