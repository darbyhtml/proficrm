from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class UiGlobalConfig(models.Model):
    """
    Глобальные настройки UI (одна запись на проект).
    """

    COMPANY_LIST_COLUMNS = [
        ("name", "Компания"),
        ("address", "Адрес (под названием)"),
        ("overdue", "Просрочки"),
        ("inn", "ИНН"),
        ("status", "Статус"),
        ("spheres", "Сферы"),
        ("responsible", "Ответственный"),
        ("branch", "Филиал"),
        ("region", "Область"),
        ("updated_at", "Обновлено"),
    ]

    company_list_columns = models.JSONField("Колонки списка компаний", default=list, blank=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "Настройки интерфейса"
        verbose_name_plural = "Настройки интерфейса"

    @classmethod
    def load(cls) -> "UiGlobalConfig":
        """
        Храним одну запись с pk=1.
        """
        obj, _ = cls.objects.get_or_create(
            pk=1,
            defaults={
                "company_list_columns": [
                    "name",
                    "address",
                    "overdue",
                    "inn",
                    "status",
                    "spheres",
                    "responsible",
                    "branch",
                    "region",
                    "updated_at",
                ]
            },
        )
        # если пусто (после ручных правок), вернём дефолт
        if not obj.company_list_columns:
            obj.company_list_columns = [
                "name",
                "address",
                "overdue",
                "inn",
                "status",
                "spheres",
                "responsible",
                "branch",
                "region",
                "updated_at",
            ]
            obj.save(update_fields=["company_list_columns", "updated_at"])
        return obj

    def __str__(self) -> str:
        return "UI config"


class AmoApiConfig(models.Model):
    """
    Настройки подключения к amoCRM API (одноразово для миграции).
    Храним одну запись с pk=1.
    """

    domain = models.CharField("Домен amoCRM", max_length=255, blank=True, default="")  # kmrprofi.amocrm.ru
    client_id = models.CharField("OAuth Client ID", max_length=255, blank=True, default="")
    client_secret = models.CharField("OAuth Client Secret", max_length=255, blank=True, default="")
    redirect_uri = models.CharField("Redirect URI", max_length=500, blank=True, default="")

    access_token = models.TextField("Access token", blank=True, default="")
    refresh_token = models.TextField("Refresh token", blank=True, default="")
    long_lived_token = models.TextField("Долгосрочный токен (если используете)", blank=True, default="")
    token_type = models.CharField("Token type", max_length=32, blank=True, default="Bearer")
    expires_at = models.DateTimeField("Token expires at", null=True, blank=True)

    last_error = models.TextField("Последняя ошибка", blank=True, default="")
    region_custom_field_id = models.IntegerField(
        "ID кастомного поля региона (amoCRM)",
        null=True,
        blank=True,
        help_text="Необязательно. Если задано — при импорте компаний из amoCRM будем пытаться заполнить регион по этому полю.",
    )
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "Интеграция amoCRM"
        verbose_name_plural = "Интеграция amoCRM"

    @classmethod
    def load(cls) -> "AmoApiConfig":
        obj, _ = cls.objects.get_or_create(pk=1, defaults={"domain": "kmrprofi.amocrm.ru"})
        return obj

    def is_connected(self) -> bool:
        # Либо OAuth (access+refresh), либо долгосрочный токен
        if self.long_lived_token and self.domain:
            return True
        return bool(self.access_token and self.refresh_token and self.domain and self.client_id and self.client_secret)


class UiUserPreference(models.Model):
    """
    Персональные настройки интерфейса (одна запись на пользователя).
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="ui_preferences",
        verbose_name="Пользователь",
    )
    font_scale = models.DecimalField(
        "Масштаб шрифта",
        max_digits=4,
        decimal_places=2,
        default=Decimal("1.00"),
        validators=[
            # Держим диапазон аккуратным, чтобы не "ехала" верстка на сетках/таблицах.
            MinValueValidator(Decimal("0.90")),
            MaxValueValidator(Decimal("1.15")),
        ],
    )
    company_detail_view_mode = models.CharField(
        "Режим просмотра карточки компании",
        max_length=20,
        default="classic",
        choices=[
            ("classic", "Классический"),
            ("modern", "Современный"),
        ],
        help_text="Режим отображения карточки компании: классический (старый layout) или современный (новый layout)",
    )
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "Настройки интерфейса (пользователь)"
        verbose_name_plural = "Настройки интерфейса (пользователь)"

    @classmethod
    def load_for_user(cls, user) -> "UiUserPreference":
        obj, _ = cls.objects.get_or_create(user=user, defaults={"font_scale": Decimal("1.00")})
        return obj

    def font_scale_float(self) -> float:
        try:
            return float(self.font_scale or Decimal("1.00"))
        except Exception:
            return 1.0
