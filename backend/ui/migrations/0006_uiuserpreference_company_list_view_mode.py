from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("ui", "0005_amoconfig_region_field"),
    ]

    operations = [
        migrations.AddField(
            model_name="uiuserpreference",
            name="company_list_view_mode",
            field=models.CharField(
                choices=[("classic", "Обычный"), ("compact", "Компактный")],
                default="classic",
                help_text="Режим отображения списка компаний: обычный (таблица) или компактный (карточки)",
                max_length=20,
                verbose_name="Режим просмотра списка компаний",
            ),
        ),
    ]
