"""
Signals для инвалидации кэша dashboard при изменении задач и компаний.
"""

from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.core.cache import cache
from django.utils import timezone

from tasksapp.models import Task
from companies.models import Company


def invalidate_dashboard_cache(user_id: int):
    """
    Инвалидирует кэш dashboard для пользователя.
    Удаляет все ключи вида dashboard_{user_id}_*
    """
    if not user_id:
        return
    
    # Для Redis можно использовать delete_pattern, но для LocMemCache нужно удалять по ключам
    # Используем простой подход: удаляем ключи для сегодняшней и вчерашней даты
    today = timezone.localdate(timezone.now())
    yesterday = today - timezone.timedelta(days=1)
    
    cache_keys = [
        f"dashboard_{user_id}_{today.isoformat()}",
        f"dashboard_{user_id}_{yesterday.isoformat()}",
    ]
    
    for key in cache_keys:
        cache.delete(key)


@receiver(post_save, sender=Task)
@receiver(post_delete, sender=Task)
def invalidate_dashboard_on_task_change(sender, instance, **kwargs):
    """Инвалидирует кэш dashboard при изменении задачи."""
    if instance.assigned_to_id:
        invalidate_dashboard_cache(instance.assigned_to_id)


@receiver(post_save, sender=Company)
@receiver(post_delete, sender=Company)
def invalidate_dashboard_on_company_change(sender, instance, **kwargs):
    """Инвалидирует кэш dashboard при изменении компании (для договоров)."""
    if instance.responsible_id:
        invalidate_dashboard_cache(instance.responsible_id)
