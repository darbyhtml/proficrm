from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("ui", "0010_amoapiconfig_encrypt_tokens"),
    ]

    operations = [
        migrations.AlterField(
            model_name="uiuserpreference",
            name="font_scale",
            field=models.DecimalField(
                decimal_places=3,
                default=Decimal("1.000"),
                max_digits=4,
                validators=[
                    MinValueValidator(Decimal("0.850")),
                    MaxValueValidator(Decimal("1.300")),
                ],
                verbose_name="Масштаб интерфейса",
            ),
        ),
    ]
