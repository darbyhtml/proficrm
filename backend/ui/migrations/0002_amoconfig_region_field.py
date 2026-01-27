from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("ui", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="amoapiconfig",
            name="region_custom_field_id",
            field=models.IntegerField(
                "ID кастомного поля региона (amoCRM)",
                null=True,
                blank=True,
                help_text="Необязательно. Если задано — при импорте компаний из amoCRM будем пытаться заполнить регион по этому полю.",
            ),
        ),
    ]

