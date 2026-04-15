"""
Сигналы приложения accounts.

Содержит:
- sync_is_staff_with_role: автоматическая синхронизация флага is_staff с User.role.
  Django-admin проверяет is_staff для доступа в административный интерфейс.
  В проекте используется собственная роль ADMIN (User.Role.ADMIN), поэтому
  флаг is_staff должен всегда отражать факт "пользователь — администратор CRM".

  Ранее is_staff синхронизировался вручную в ui/forms.py (CompanyCreateForm и др.),
  что приводило к риску рассинхронизации при создании пользователей через другие
  пути (django-admin, импорт, bulk operations). Единый post_save-сигнал закрывает
  эту дыру раз и навсегда.
"""
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import User


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
