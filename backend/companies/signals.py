from __future__ import annotations

from django.db.models.signals import pre_delete
from django.dispatch import receiver

from .models import CompanyNote


@receiver(pre_delete, sender=CompanyNote)
def _delete_company_note_attachment(sender, instance: CompanyNote, **kwargs):
    """
    При каскадном удалении компании заметки удаляются bulk-ом,
    но файлы из FileField не удаляются автоматически.
    """
    try:
        if instance.attachment:
            instance.attachment.delete(save=False)
    except Exception:
        pass


