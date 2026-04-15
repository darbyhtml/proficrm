"""
Справочник регионов, закреплённых за подразделениями.

Источник: «Положение о распределении входящих запросов на оказание
образовательных услуг, поступающих от клиентов с территорий РФ»
(Группа компаний ПРОФИ, 2025-2026).
"""

from django.db import models

from accounts.models import Branch


class BranchRegion(models.Model):
    """Регион, закреплённый за подразделением."""

    branch = models.ForeignKey(
        Branch,
        on_delete=models.CASCADE,
        related_name="regions",
        verbose_name="Подразделение",
    )
    region_name = models.CharField(
        "Регион",
        max_length=128,
        db_index=True,
    )
    is_common_pool = models.BooleanField(
        "Общий пул",
        default=False,
        help_text=(
            "Москва/МО, СПб/ЛО, Новгородская, Псковская — "
            "обслуживаются равномерно всеми подразделениями"
        ),
    )
    ordering = models.PositiveSmallIntegerField("Порядок", default=0)

    class Meta:
        verbose_name = "Регион подразделения"
        verbose_name_plural = "Регионы подразделений"
        unique_together = [("branch", "region_name")]
        indexes = [
            models.Index(fields=["region_name", "branch"]),
            models.Index(fields=["is_common_pool"]),
        ]
        ordering = ["branch", "ordering"]

    def __str__(self):
        return f"{self.branch.name} — {self.region_name}"
