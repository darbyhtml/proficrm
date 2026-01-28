from __future__ import annotations

import json
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from notifications.service import notify
from notifications.models import Notification
from .models import Task
from accounts.models import User


@receiver(post_save, sender=Task)
def notify_task_assigned(sender, instance: Task, created: bool, **kwargs):
    """
    Отправляет уведомление исполнителю при назначении задачи.
    Срабатывает только при создании новой задачи с assigned_to.
    """
    # Отправляем уведомление только при создании новой задачи
    if not created:
        return
    
    # Если задача не назначена никому, уведомление не нужно
    if not instance.assigned_to:
        return
    
    # Не отправляем уведомление, если задача назначена самому создателю
    if instance.created_by_id and instance.assigned_to_id == instance.created_by_id:
        return
    
    # Формируем payload с данными задачи
    payload = {
        "task_id": str(instance.id),
        "company_id": str(instance.company_id) if instance.company_id else None,
        "created_by_id": instance.created_by_id if instance.created_by_id else None,
        "due_at": instance.due_at.isoformat() if instance.due_at else None,
        "is_urgent": instance.is_urgent,
    }
    
    # Определяем роль создателя для отображения иконки
    creator_role = None
    if instance.created_by:
        if instance.created_by.role == User.Role.SALES_HEAD:
            creator_role = "sales_head"  # РОП
        elif instance.created_by.role == User.Role.BRANCH_DIRECTOR:
            creator_role = "branch_director"  # Директор филиала
        elif instance.created_by.role == User.Role.ADMIN:
            creator_role = "admin"
    
    payload["creator_role"] = creator_role
    
    # Формируем текст уведомления
    title = "Вам назначена задача"
    body_parts = []
    
    if instance.company:
        body_parts.append(f"Компания: {instance.company.name}")
    
    if instance.created_by:
        creator_name = f"{instance.created_by.last_name} {instance.created_by.first_name}".strip() or instance.created_by.get_username()
        body_parts.append(f"Поставил: {creator_name}")
    
    if instance.is_urgent:
        body_parts.append("СРОЧНО")
    
    body = " · ".join(body_parts) if body_parts else instance.title
    
    # URL для перехода к задаче
    url = f"/tasks/{instance.id}/"
    
    # Создаём уведомление с payload
    notify(
        user=instance.assigned_to,
        kind=Notification.Kind.TASK,
        title=title,
        body=body,
        url=url,
        payload=payload,
    )
