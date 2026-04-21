"""Company access / edit / delete / notifications / cache helpers.

Extracted из backend/ui/views/_base.py в W1.1 refactor.
Zero behavior change.
"""

from __future__ import annotations

from django.core.cache import cache
from django.db.models import Count, Exists, IntegerField, OuterRef, Subquery
from django.db.models.functions import Coalesce
from django.utils import timezone

from accounts.models import User
from companies.models import Company, Contact
from companies.permissions import can_edit_company as can_edit_company_perm
from companies.permissions import editable_company_qs as editable_company_qs_perm
from notifications.models import Notification
from notifications.service import notify
from tasksapp.models import Task


def _dup_reasons(*, c: Company, inn: str, kpp: str, name: str, address: str) -> list[str]:
    reasons: list[str] = []
    if inn and (c.inn or "").strip() == inn:
        reasons.append("ИНН")
    if kpp and (c.kpp or "").strip() == kpp:
        reasons.append("КПП")
    if name:
        n = name.lower()
        if n in (c.name or "").lower() or n in (c.legal_name or "").lower():
            reasons.append("Название")
    if address:
        a = address.lower()
        if a in (c.address or "").lower():
            reasons.append("Адрес")
    return reasons


def _can_edit_company(user: User, company: Company) -> bool:
    return can_edit_company_perm(user, company)


def _editable_company_qs(user: User):
    return editable_company_qs_perm(user)


def _company_branch_id(company: Company):
    if getattr(company, "branch_id", None):
        return company.branch_id
    resp = getattr(company, "responsible", None)
    return getattr(resp, "branch_id", None)


def _can_delete_company(user: User, company: Company) -> bool:
    if not user or not user.is_authenticated or not user.is_active:
        return False
    if user.is_superuser or user.role in (User.Role.ADMIN, User.Role.GROUP_MANAGER):
        return True
    if user.role in (User.Role.SALES_HEAD, User.Role.BRANCH_DIRECTOR) and user.branch_id:
        return bool(_company_branch_id(company) == user.branch_id)
    return False


def _notify_branch_leads(*, branch_id, title: str, body: str, url: str, exclude_user_id=None):
    if not branch_id:
        return 0
    qs = User.objects.filter(
        is_active=True,
        branch_id=branch_id,
        role__in=[User.Role.SALES_HEAD, User.Role.BRANCH_DIRECTOR],
    )
    if exclude_user_id:
        qs = qs.exclude(id=exclude_user_id)
    sent = 0
    for u in qs.iterator():
        notify(user=u, kind=Notification.Kind.COMPANY, title=title, body=body, url=url)
        sent += 1
    return sent


def _detach_client_branches(*, head_company: Company) -> list[Company]:
    """
    Если удаляется "головная организация" клиента, её дочерние карточки должны стать самостоятельными:
    head_company=NULL.
    Возвращает список "бывших филиалов" (до 200 для сообщений/логов).
    """
    children_qs = (
        Company.objects.filter(head_company_id=head_company.id)
        .select_related("responsible", "branch")
        .order_by("name")
    )
    children = list(children_qs[:200])
    if children:
        now_ts = timezone.now()
        Company.objects.filter(head_company_id=head_company.id).update(
            head_company=None, updated_at=now_ts
        )
    return children


def _notify_head_deleted_with_branches(
    *, actor: User, head_company: Company, detached: list[Company]
):
    """
    Уведомление о том, что удалили головную компанию клиента, и её филиалы стали самостоятельными.
    По ТЗ уведомляем руководителей (РОП/директор) соответствующего внутреннего филиала.
    """
    if not detached:
        return 0
    branch_id = _company_branch_id(head_company)
    body = f"{head_company.name}: удалена головная организация. Филиалов стало головными: {len(detached)}."
    # В body добавим первые несколько названий (чтобы было понятно о чём речь)
    sample = ", ".join([c.name for c in detached[:5] if c.name])
    if sample:
        body = body + f" Примеры: {sample}."
    return _notify_branch_leads(
        branch_id=branch_id,
        title="Удалена головная организация (филиалы стали самостоятельными)",
        body=body,
        url="/companies/",
        exclude_user_id=actor.id,
    )


def _invalidate_company_count_cache():
    """
    Инвалидирует кэш общего количества компаний.
    Удаляет все ключи с префиксом 'companies_total_count_*'.
    """
    # Для Redis можно использовать delete_pattern, но для LocMemCache нужно удалять по ключам
    # Используем простой подход: удаляем ключи для всех возможных комбинаций user/view_as
    # В реальности лучше использовать Redis с delete_pattern или версионирование ключей

    # Удаляем старый глобальный ключ (для обратной совместимости)
    cache.delete("companies_total_count")

    # Если используется Redis, можно использовать delete_pattern
    # Для LocMemCache это не работает, поэтому очищаем весь кэш при массовых операциях
    # или используем версионирование ключей


def _companies_with_overdue_flag(*, now):
    """
    Базовый QS компаний с вычисляемым флагом просроченных задач `has_overdue`
    и наличием активных задач `has_any_active_task`.
    Используется в списке/экспорте/массовых операциях, чтобы фильтры работали одинаково.
    """
    overdue_tasks = (
        Task.objects.filter(company_id=OuterRef("pk"), due_at__lt=now)
        .exclude(status__in=[Task.Status.DONE, Task.Status.CANCELLED])
        .values("id")
    )
    active_tasks = (
        Task.objects.filter(company_id=OuterRef("pk"))
        .exclude(status__in=[Task.Status.DONE, Task.Status.CANCELLED])
        .values("id")
    )
    cold_contacts = Contact.objects.filter(company_id=OuterRef("pk"), is_cold_call=True).values(
        "id"
    )
    # Скалярная подзапрос-аннотация количества активных задач (не ломает JOIN'ы)
    active_tasks_count_sq = (
        Task.objects.filter(company_id=OuterRef("pk"))
        .exclude(status__in=[Task.Status.DONE, Task.Status.CANCELLED])
        .values("company_id")
        .annotate(_c=Count("id"))
        .values("_c")
    )
    return Company.objects.all().annotate(
        has_overdue=Exists(overdue_tasks),
        has_any_active_task=Exists(active_tasks),
        has_cold_call_contact=Exists(cold_contacts),
        active_tasks_count=Coalesce(
            Subquery(active_tasks_count_sq, output_field=IntegerField()),
            0,
        ),
    )
