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
    is_urgent = models.BooleanField("Срочно", default=False, db_index=True)

    # Повторяющиеся задачи: строка в формате iCalendar RRULE (RFC 5545).
    # Пустая строка = задача не повторяется.
    #
    # Примеры:
    #   FREQ=DAILY                          — каждый день
    #   FREQ=WEEKLY;BYDAY=MO,WE,FR          — пн/ср/пт каждую неделю
    #   FREQ=MONTHLY;BYMONTHDAY=1           — первого числа каждого месяца
    #   FREQ=WEEKLY;INTERVAL=2;BYDAY=MO     — каждый второй понедельник
    #   FREQ=DAILY;UNTIL=20261231T235959Z   — каждый день до 31.12.2026
    #   FREQ=WEEKLY;COUNT=10                — 10 раз, раз в неделю
    #
    # Реализация: поле только хранит правило. Генерация экземпляров задач
    # по расписанию — отдельная Celery-задача (не реализована, TODO).
    recurrence_rrule = models.CharField("Повтор (RRULE)", max_length=500, blank=True, default="")

    # Импорт/интеграции (amo и т.п.) — для дедупликации и трассировки источника
    external_source = models.CharField("Внешний источник", max_length=32, blank=True, default="", db_index=True)
    external_uid = models.CharField("Внешний UID", max_length=120, blank=True, default="", db_index=True)

    def __str__(self) -> str:
        return self.title


class TaskComment(models.Model):
    """Комментарий к задаче — внутренняя переписка/заметки по ходу работы."""

    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="comments")
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Автор",
        null=True,
        on_delete=models.SET_NULL,
        related_name="task_comments",
    )
    text = models.TextField("Текст")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"Комментарий к задаче {self.task_id}"


class TaskEvent(models.Model):
    """История изменений задачи: смена статуса, переназначение, перенос дедлайна."""

    class Kind(models.TextChoices):
        CREATED = "created", "Создана"
        STATUS_CHANGED = "status_changed", "Статус изменён"
        ASSIGNED = "assigned", "Переназначена"
        DEADLINE_CHANGED = "deadline_changed", "Дедлайн изменён"

    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="events")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Кто изменил",
        null=True,
        on_delete=models.SET_NULL,
        related_name="task_events",
    )
    kind = models.CharField("Тип события", max_length=32, choices=Kind.choices)
    old_value = models.CharField("Старое значение", max_length=255, blank=True, default="")
    new_value = models.CharField("Новое значение", max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} — задача {self.task_id}"
