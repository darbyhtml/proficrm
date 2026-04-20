from __future__ import annotations

import logging

from django.db import transaction
from django.db.models.signals import post_delete, post_save, pre_delete
from django.dispatch import receiver

logger = logging.getLogger(__name__)

from .models import (
    Company,
    CompanyDeletionRequest,
    CompanyEmail,
    CompanyNote,
    CompanyPhone,
    Contact,
    ContactEmail,
    ContactPhone,
)

# Task больше не импортируем — _task_changed signal удалён 2026-04-20.


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
        logger.exception(
            "Не удалось удалить вложение CompanyNote id=%s", getattr(instance, "id", None)
        )


def _rebuild_index_for_company(company_id):
    """
    Непосредственно перестроить индекс по компании.
    Вызывать только ИЗВНЕ транзакции (или через on_commit), чтобы не ловить
    race-состояния при удалении компании в той же транзакции.
    """
    try:
        from companies.search_index import rebuild_company_search_index

        rebuild_company_search_index(company_id)
    except Exception:
        # Индекс — вспомогательная штука: не ломаем бизнес-сохранение из-за проблем индекса
        logger.exception("rebuild_company_search_index failed for company_id=%s", company_id)


def _schedule_rebuild_index_for_company(company_id):
    """
    Безопасно поставить перестроение индекса после текущей транзакции.
    Если транзакции нет, on_commit выполнит колбэк сразу.

    DEDUPLICATION (2026-04-20): сохраняем запланированные company_id в
    request-level set'е на connection'е. Если в одной транзакции пришло
    N сигналов от связанных объектов (AmoCRM import: 1 Company + 10 Phone
    + 5 Email + 20 Contact × 2 phones = ~31 сигнал), on_commit будет
    зарегистрирован **один раз на company_id**, а не 31.
    Это снижает нагрузку FTS rebuild в ~20-30× при bulk-операциях.

    Technically: храним set на атрибуте `_rebuild_pending_company_ids`
    у текущего connection. Django не даёт атомарной dedup для on_commit
    (`transaction.on_commit` просто append'ает callback в список), поэтому
    проверяем наличие ID в set'е и регистрируем on_commit только в первый раз.
    После commit'а колбэк сам очищает себя из set'а.
    """
    if not company_id:
        return

    try:
        conn = transaction.get_connection()
        pending = getattr(conn, "_rebuild_pending_company_ids", None)
        if pending is None:
            pending = set()
            conn._rebuild_pending_company_ids = pending

        if company_id in pending:
            # Уже запланирован в этой транзакции — пропускаем.
            return
        pending.add(company_id)

        def _commit_callback(cid=company_id, _conn=conn):
            try:
                _rebuild_index_for_company(cid)
            finally:
                _pending = getattr(_conn, "_rebuild_pending_company_ids", None)
                if _pending is not None:
                    _pending.discard(cid)

        transaction.on_commit(_commit_callback)
    except Exception:
        # На всякий случай fallback, если нет менеджера транзакций.
        logger.exception(
            "transaction.on_commit недоступен, выполняем rebuild сразу (company_id=%s)", company_id
        )
        _rebuild_index_for_company(company_id)


@receiver(pre_delete, sender=Company)
def _auto_cancel_deletion_requests(sender, instance: Company, **kwargs):
    """
    При удалении компании все PENDING-заявки на удаление автоматически отменяются,
    чтобы не оставалось zombie-записей со status=PENDING и company=NULL.
    """
    CompanyDeletionRequest.objects.filter(
        company=instance,
        status=CompanyDeletionRequest.Status.PENDING,
    ).update(status=CompanyDeletionRequest.Status.CANCELLED)


@receiver(post_save, sender=Company)
def _company_saved_rebuild_search_index(sender, instance: Company, **kwargs):
    _schedule_rebuild_index_for_company(instance.id)


@receiver(post_save, sender=CompanyEmail)
@receiver(post_delete, sender=CompanyEmail)
def _company_email_changed(sender, instance: CompanyEmail, **kwargs):
    _schedule_rebuild_index_for_company(instance.company_id)


@receiver(post_save, sender=CompanyPhone)
@receiver(post_delete, sender=CompanyPhone)
def _company_phone_changed(sender, instance: CompanyPhone, **kwargs):
    _schedule_rebuild_index_for_company(instance.company_id)


@receiver(post_save, sender=Contact)
@receiver(post_delete, sender=Contact)
def _contact_changed(sender, instance: Contact, **kwargs):
    if instance.company_id:
        _schedule_rebuild_index_for_company(instance.company_id)


@receiver(post_save, sender=ContactEmail)
@receiver(post_delete, sender=ContactEmail)
def _contact_email_changed(sender, instance: ContactEmail, **kwargs):
    try:
        company_id = instance.contact.company_id
    except Exception:
        logger.exception(
            "Не удалось получить company_id из ContactEmail id=%s", getattr(instance, "id", None)
        )
        company_id = None
    if company_id:
        _schedule_rebuild_index_for_company(company_id)


@receiver(post_save, sender=ContactPhone)
@receiver(post_delete, sender=ContactPhone)
def _contact_phone_changed(sender, instance: ContactPhone, **kwargs):
    try:
        company_id = instance.contact.company_id
    except Exception:
        logger.exception(
            "Не удалось получить company_id из ContactPhone id=%s", getattr(instance, "id", None)
        )
        company_id = None
    if company_id:
        _schedule_rebuild_index_for_company(company_id)


@receiver(post_save, sender=CompanyNote)
@receiver(post_delete, sender=CompanyNote)
def _company_note_changed(sender, instance: CompanyNote, **kwargs):
    _schedule_rebuild_index_for_company(instance.company_id)


# Task signal УДАЛЁН 2026-04-20: данные Task (title, status, due_at) НЕ входят
# в FTS-индекс CompanySearchIndex (см. rebuild_company_search_index в
# companies/search_index.py — индексирует только поля Company + Contact + Note).
# Каждый save/delete Task создавал бесполезный rebuild → сотни в день.
# См. docs/runbooks/04-god-nodes-n1-analysis.md.
