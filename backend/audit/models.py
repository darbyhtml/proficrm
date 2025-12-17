import uuid

from django.conf import settings
from django.db import models


class ActivityEvent(models.Model):
    """
    Универсальный журнал действий.
    entity_type: company/contact/task/note/user/branch/...
    entity_id: UUID/int в строковом виде (для простоты и унификации)
    """

    class Verb(models.TextChoices):
        CREATE = "create", "Создал"
        UPDATE = "update", "Изменил"
        DELETE = "delete", "Удалил"
        STATUS = "status", "Сменил статус"
        COMMENT = "comment", "Комментарий"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    actor = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="Кто", null=True, on_delete=models.SET_NULL, related_name="activity_events")

    verb = models.CharField("Действие", max_length=16, choices=Verb.choices)
    entity_type = models.CharField("Сущность", max_length=32, db_index=True)
    entity_id = models.CharField("ID сущности", max_length=64, db_index=True)

    # Для удобства фильтрации по компании
    company_id = models.UUIDField("ID компании", null=True, blank=True, db_index=True)

    message = models.CharField("Описание", max_length=255, blank=True, default="")
    meta = models.JSONField("Данные", default=dict, blank=True)

    created_at = models.DateTimeField("Когда", auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.created_at} {self.entity_type}:{self.entity_id} {self.verb}"

# Create your models here.
