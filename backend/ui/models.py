from __future__ import annotations

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
                "company_list_columns": ["name", "address", "overdue", "inn", "status", "spheres", "responsible", "branch", "updated_at"]
            },
        )
        # если пусто (после ручных правок), вернём дефолт
        if not obj.company_list_columns:
            obj.company_list_columns = ["name", "address", "overdue", "inn", "status", "spheres", "responsible", "branch", "updated_at"]
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
