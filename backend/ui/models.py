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
