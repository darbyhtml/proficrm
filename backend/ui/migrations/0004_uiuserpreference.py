from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("ui", "0003_amoapiconfig_long_lived_token"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="UiUserPreference",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("font_scale", models.DecimalField(
                    decimal_places=2,
                    default=Decimal("1.00"),
                    max_digits=4,
                    validators=[MinValueValidator(Decimal("0.90")), MaxValueValidator(Decimal("1.15"))],
                    verbose_name="Масштаб шрифта",
                )),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Обновлено")),
                ("user", models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="ui_preferences",
                    to=settings.AUTH_USER_MODEL,
                    verbose_name="Пользователь",
                )),
            ],
            options={
                "verbose_name": "Настройки интерфейса (пользователь)",
                "verbose_name_plural": "Настройки интерфейса (пользователь)",
            },
        ),
    ]

