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
        ("branch", "Подразделение"),
        ("region", "Область"),
        ("updated_at", "Обновлено"),
    ]

    company_list_columns = models.JSONField("Колонки списка компаний", default=list, blank=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "Настройки интерфейса"
        verbose_name_plural = "Настройки интерфейса"

    @classmethod
    def load(cls) -> UiGlobalConfig:
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


# AmoApiConfig model removed 2026-04-21 (amoCRM subscription expired).
# Data preserved on prod until W9 accumulated deploy per Path E.
# Staging: tables dropped via Operation 3 dump+drop.
# See docs/decisions/2026-04-21-remove-amocrm.md.



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
        "Масштаб интерфейса",
        max_digits=4,
        decimal_places=3,
        default=Decimal("1.000"),
        validators=[
            # Расширенный диапазон для v2-пресетов: 87.5% / 100% / 112.5% / 125%.
            # В v2 применяется через CSS zoom и масштабирует весь интерфейс пропорционально.
            MinValueValidator(Decimal("0.850")),
            MaxValueValidator(Decimal("1.300")),
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
    tasks_per_page = models.PositiveSmallIntegerField(
        "Строк на странице (задачи)",
        default=25,
        choices=[(25, "25"), (50, "50"), (100, "100"), (200, "200")],
    )
    companies_per_page = models.PositiveSmallIntegerField(
        "Строк на странице (компании)",
        default=25,
        choices=[(25, "25"), (50, "50"), (100, "100"), (200, "200")],
    )
    default_task_tab = models.CharField(
        "Вкладка задач по умолчанию",
        max_length=20,
        default="all",
        choices=[
            ("all", "Все"),
            ("mine", "Мои"),
            ("overdue", "Просроченные"),
            ("today", "Сегодня"),
        ],
    )
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "Настройки интерфейса (пользователь)"
        verbose_name_plural = "Настройки интерфейса (пользователь)"

    @classmethod
    def load_for_user(cls, user) -> UiUserPreference:
        obj, _ = cls.objects.get_or_create(user=user, defaults={"font_scale": Decimal("1.00")})
        return obj

    def font_scale_float(self) -> float:
        try:
            return float(self.font_scale or Decimal("1.00"))
        except Exception:
            return 1.0
