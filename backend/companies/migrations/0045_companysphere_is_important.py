from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("companies", "0044_update_normalized_inns_help_text"),
    ]

    operations = [
        migrations.AddField(
            model_name="companysphere",
            name="is_important",
            field=models.BooleanField(
                default=False,
                help_text="Если включено, рядом с этой сферой в интерфейсе показывается оранжевая иконка с вопросительным знаком.",
                verbose_name="Важно",
            ),
        ),
    ]

