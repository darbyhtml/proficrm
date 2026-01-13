import uuid

from django.conf import settings
from django.db import models


class TaskType(models.Model):
    name = models.CharField("Название", max_length=120, unique=True)
    icon = models.CharField("Иконка", max_length=32, blank=True, default="")  # логический код иконки (phone, mail, alert и т.п.)
    color = models.CharField("Цвет", max_length=32, blank=True, default="")  # CSS-класс/токен цвета бейджа

    def __str__(self) -> str:
        return self.name


class Task(models.Model):
    class Status(models.TextChoices):
        NEW = "new", "Новая"
        IN_PROGRESS = "in_progress", "В работе"
        DONE = "done", "Выполнена"
        CANCELLED = "cancelled", "Отменена"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="Создатель", null=True, on_delete=models.SET_NULL, related_name="created_tasks")
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="Ответственный", null=True, on_delete=models.SET_NULL, related_name="assigned_tasks")

    company = models.ForeignKey("companies.Company", verbose_name="Компания", null=True, blank=True, on_delete=models.SET_NULL, related_name="tasks")
    type = models.ForeignKey(TaskType, verbose_name="Тип", null=True, blank=True, on_delete=models.SET_NULL, related_name="tasks")

    title = models.CharField("Заголовок", max_length=255)
    description = models.TextField("Описание", blank=True, default="")

    status = models.CharField(max_length=16, choices=Status.choices, default=Status.NEW)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    due_at = models.DateTimeField("Дедлайн", null=True, blank=True, db_index=True)
    completed_at = models.DateTimeField("Завершено", null=True, blank=True)

    # Повторяющиеся задачи: храним RRULE (iCal) строкой; генерацию экземпляров можно добавить позже cron'ом.
    recurrence_rrule = models.CharField("Повтор (RRULE)", max_length=500, blank=True, default="")

    # Импорт/интеграции (amo и т.п.) — для дедупликации и трассировки источника
    external_source = models.CharField("Внешний источник", max_length=32, blank=True, default="", db_index=True)
    external_uid = models.CharField("Внешний UID", max_length=120, blank=True, default="", db_index=True)

    def __str__(self) -> str:
        return self.title

# Create your models here.
