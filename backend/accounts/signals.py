"""
Сигналы приложения accounts.

Содержит:
- sync_is_staff_with_role: автоматическая синхронизация флага is_staff с User.role.
- invalidate_sessions_on_deactivate: инвалидация всех сессий уволенного юзера.
"""
import logging

from django.contrib.sessions.models import Session
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone

from .models import User

logger = logging.getLogger(__name__)


@receiver(post_save, sender=User)
def sync_is_staff_with_role(sender, instance: User, created: bool, **kwargs) -> None:
    """
    Привести is_staff в соответствие с role: True только если role == ADMIN
    (либо пользователь is_superuser, что тоже означает ADMIN-равнозначный доступ).

    Сохраняется только при реальном расхождении, чтобы не плодить бесконечные
    рекурсивные post_save при первой инициализации.
    """
    desired_is_staff = bool(instance.is_superuser or instance.role == User.Role.ADMIN)
    if instance.is_staff != desired_is_staff:
        # update_fields предотвращает рекурсию через тот же post_save
        # (при update_fields сигнал всё равно срабатывает, но мы проверяем
        # условие "!=" до save — на втором проходе оно будет False).
        User.objects.filter(pk=instance.pk).update(is_staff=desired_is_staff)
        # Обновляем in-memory инстанс, чтобы вызывающий код видел актуальное значение
        instance.is_staff = desired_is_staff


@receiver(pre_save, sender=User)
def _capture_previous_is_active(sender, instance: User, **kwargs) -> None:
    """Запоминаем прежнее значение is_active перед сохранением."""
    if not instance.pk:
        instance._previous_is_active = True  # type: ignore[attr-defined]
        return
    try:
        prev = User.objects.filter(pk=instance.pk).values_list("is_active", flat=True).first()
        instance._previous_is_active = bool(prev) if prev is not None else True  # type: ignore[attr-defined]
    except Exception:
        instance._previous_is_active = True  # type: ignore[attr-defined]


@receiver(post_save, sender=User)
def invalidate_sessions_on_deactivate(sender, instance: User, created: bool, **kwargs) -> None:
    """Инвалидирует все сессии при deactivate пользователя.

    SECURITY: без этого уволенный сотрудник продолжает работать до
    истечения сессии. Если у админа была включена view-as этого user —
    session тоже очищается (view_as_user_id не будет найти активного юзера).
    """
    if created:
        return
    # Триггер: is_active был True, стал False
    prev_active = getattr(instance, "_previous_is_active", True)
    if prev_active and not instance.is_active:
        _delete_user_sessions(instance.pk)


def _delete_user_sessions(user_id) -> None:
    """Удалить все активные Django-сессии данного пользователя.

    Стоимость: O(N) по активным сессиям — мы обязаны прочитать каждую,
    чтобы извлечь `_auth_user_id`. При 1000+ активных сессий операция
    может стать заметной; для CRM с 20-50 юзерами — мгновенно.
    """
    try:
        deleted = 0
        now = timezone.now()
        qs = Session.objects.filter(expire_date__gt=now).iterator(chunk_size=200)
        for session in qs:
            data = session.get_decoded()
            sid = data.get("_auth_user_id")
            if sid and str(sid) == str(user_id):
                session.delete()
                deleted += 1
        if deleted:
            logger.info("Invalidated %d sessions for deactivated user_id=%s", deleted, user_id)
    except Exception:
        logger.exception("Failed to invalidate sessions for user_id=%s", user_id)
