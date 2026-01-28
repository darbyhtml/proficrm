from __future__ import annotations

from django.db.models.signals import post_delete, post_save, pre_delete
from django.dispatch import receiver

from .models import (
    Company,
    CompanyEmail,
    CompanyNote,
    CompanyPhone,
    Contact,
    ContactEmail,
    ContactPhone,
)
from tasksapp.models import Task


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


def _rebuild_index_for_company(company_id):
    try:
        from companies.search_index import rebuild_company_search_index
        rebuild_company_search_index(company_id)
    except Exception:
        # Индекс — вспомогательная штука: не ломаем бизнес-сохранение из-за проблем индекса
        pass


@receiver(post_save, sender=Company)
def _company_saved_rebuild_search_index(sender, instance: Company, **kwargs):
    _rebuild_index_for_company(instance.id)


@receiver(post_save, sender=CompanyEmail)
@receiver(post_delete, sender=CompanyEmail)
def _company_email_changed(sender, instance: CompanyEmail, **kwargs):
    _rebuild_index_for_company(instance.company_id)


@receiver(post_save, sender=CompanyPhone)
@receiver(post_delete, sender=CompanyPhone)
def _company_phone_changed(sender, instance: CompanyPhone, **kwargs):
    _rebuild_index_for_company(instance.company_id)


@receiver(post_save, sender=Contact)
@receiver(post_delete, sender=Contact)
def _contact_changed(sender, instance: Contact, **kwargs):
    if instance.company_id:
        _rebuild_index_for_company(instance.company_id)


@receiver(post_save, sender=ContactEmail)
@receiver(post_delete, sender=ContactEmail)
def _contact_email_changed(sender, instance: ContactEmail, **kwargs):
    try:
        company_id = instance.contact.company_id
    except Exception:
        company_id = None
    if company_id:
        _rebuild_index_for_company(company_id)


@receiver(post_save, sender=ContactPhone)
@receiver(post_delete, sender=ContactPhone)
def _contact_phone_changed(sender, instance: ContactPhone, **kwargs):
    try:
        company_id = instance.contact.company_id
    except Exception:
        company_id = None
    if company_id:
        _rebuild_index_for_company(company_id)


@receiver(post_save, sender=CompanyNote)
@receiver(post_delete, sender=CompanyNote)
def _company_note_changed(sender, instance: CompanyNote, **kwargs):
    _rebuild_index_for_company(instance.company_id)


@receiver(post_save, sender=Task)
@receiver(post_delete, sender=Task)
def _task_changed(sender, instance: Task, **kwargs):
    if instance.company_id:
        _rebuild_index_for_company(instance.company_id)


