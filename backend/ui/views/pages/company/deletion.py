"""Company delete workflow (W1.2 refactor).

Extracted из `backend/ui/views/company_detail.py` в W1.2. Zero behavior change.

Endpoints:
- `company_delete_request_create` — POST /companies/<uuid>/delete-request/
- `company_delete_request_cancel` — POST /companies/<uuid>/delete-request/<id>/cancel/
- `company_delete_request_approve` — POST /companies/<uuid>/delete-request/<id>/approve/
- `company_delete_direct` — POST /companies/<uuid>/delete/
"""

from __future__ import annotations

import logging

from ui.views._base import (
    ActivityEvent,
    Company,
    CompanyDeletionRequest,
    HttpRequest,
    HttpResponse,
    Notification,
    User,
    _can_delete_company,
    _company_branch_id,
    _notify_branch_leads,
    get_object_or_404,
    log_event,
    login_required,
    messages,
    notify,
    policy_required,
    redirect,
    require_can_view_company,
    timezone,
)

logger = logging.getLogger(__name__)


@login_required
@policy_required(resource_type="action", resource="ui:companies:delete_request:create")
@require_can_view_company
def company_delete_request_create(request: HttpRequest, company_id) -> HttpResponse:
    if request.method != "POST":
        return redirect("company_detail", company_id=company_id)
    user: User = request.user
    company = get_object_or_404(
        Company.objects.select_related("responsible", "branch"), id=company_id
    )
    if not (user.role == User.Role.MANAGER and company.responsible_id == user.id):
        messages.error(request, "Запрос на удаление может отправить только ответственный менеджер.")
        return redirect("company_detail", company_id=company.id)
    existing = CompanyDeletionRequest.objects.filter(
        company=company, status=CompanyDeletionRequest.Status.PENDING
    ).first()
    if existing:
        messages.info(request, "Запрос на удаление уже отправлен и ожидает решения.")
        return redirect("company_detail", company_id=company.id)
    note = (request.POST.get("note") or "").strip()
    req = CompanyDeletionRequest.objects.create(
        company=company,
        company_id_snapshot=company.id,
        company_name_snapshot=company.name or "",
        requested_by=user,
        requested_by_branch=user.branch,
        note=note,
        status=CompanyDeletionRequest.Status.PENDING,
    )
    branch_id = _company_branch_id(company)
    sent = _notify_branch_leads(
        branch_id=branch_id,
        title="Запрос на удаление компании",
        body=f"{company.name}: {(note[:180] + '…') if len(note) > 180 else note or 'без комментария'}",
        url=f"/companies/{company.id}/",
        exclude_user_id=user.id,
    )
    # Дополнительно создаём Notification с payload для UI
    from notifications.models import Notification
    from notifications.service import notify as notify_service

    branch_leads = User.objects.filter(
        is_active=True,
        branch_id=branch_id,
        role__in=[User.Role.SALES_HEAD, User.Role.BRANCH_DIRECTOR],
    ).exclude(id=user.id)
    for lead in branch_leads:
        notify_service(
            user=lead,
            kind=Notification.Kind.COMPANY,
            title="Запрос на удаление компании",
            body=f"{company.name}: {(note[:180] + '…') if len(note) > 180 else note or 'без комментария'}",
            url=f"/companies/{company.id}/",
            payload={
                "company_id": str(company.id),
                "request_id": req.id,
                "requested_by_id": user.id,
                "requested_by_name": f"{user.last_name} {user.first_name}".strip()
                or user.get_username(),
                "reason": note[:500] if note else "",
            },
        )
    messages.success(
        request, f"Запрос отправлен на рассмотрение. Уведомлено руководителей: {sent}."
    )
    log_event(
        actor=user,
        verb=ActivityEvent.Verb.CREATE,
        entity_type="company_delete_request",
        entity_id=str(req.id),
        company_id=company.id,
        message="Запрос на удаление компании",
        meta={"note": note[:500], "notified": sent},
    )
    return redirect("company_detail", company_id=company.id)


@login_required
@policy_required(resource_type="action", resource="ui:companies:delete_request:cancel")
@require_can_view_company
def company_delete_request_cancel(request: HttpRequest, company_id, req_id: int) -> HttpResponse:
    if request.method != "POST":
        return redirect("company_detail", company_id=company_id)
    user: User = request.user
    company = get_object_or_404(
        Company.objects.select_related("responsible", "branch"), id=company_id
    )
    if not _can_delete_company(user, company):
        messages.error(request, "Нет прав на обработку запросов удаления по этой компании.")
        return redirect("company_detail", company_id=company.id)
    req = get_object_or_404(
        CompanyDeletionRequest.objects.select_related("requested_by"),
        id=req_id,
        company_id_snapshot=company.id,
    )
    if req.status != CompanyDeletionRequest.Status.PENDING:
        messages.info(request, "Запрос уже обработан.")
        return redirect("company_detail", company_id=company.id)
    decision_note = (request.POST.get("decision_note") or "").strip()
    if not decision_note:
        messages.error(request, "Укажите причину отмены запроса.")
        return redirect("company_detail", company_id=company.id)
    req.status = CompanyDeletionRequest.Status.CANCELLED
    req.decided_by = user
    req.decision_note = decision_note
    req.decided_at = timezone.now()
    req.save(update_fields=["status", "decided_by", "decision_note", "decided_at"])
    if req.requested_by_id:
        notify(
            user=req.requested_by,
            kind=Notification.Kind.COMPANY,
            title="Запрос на удаление отклонён",
            body=f"{company.name}: {decision_note}",
            url=f"/companies/{company.id}/",
            payload={
                "company_id": str(company.id),
                "request_id": req.id,
                "decided_by_id": user.id,
                "decided_by_name": f"{user.last_name} {user.first_name}".strip()
                or user.get_username(),
                "decision": "cancelled",
            },
        )
    messages.success(request, "Запрос отклонён. Менеджер уведомлён.")
    log_event(
        actor=user,
        verb=ActivityEvent.Verb.UPDATE,
        entity_type="company_delete_request",
        entity_id=str(req.id),
        company_id=company.id,
        message="Отклонён запрос на удаление компании",
        meta={"decision_note": decision_note[:500]},
    )
    return redirect("company_detail", company_id=company.id)


@login_required
@policy_required(resource_type="action", resource="ui:companies:delete_request:approve")
@require_can_view_company
def company_delete_request_approve(request: HttpRequest, company_id, req_id: int) -> HttpResponse:
    if request.method != "POST":
        return redirect("company_detail", company_id=company_id)
    user: User = request.user
    company = get_object_or_404(
        Company.objects.select_related("responsible", "branch"), id=company_id
    )
    # Сохраняем ID компании отдельно — после company.delete() pk на инстансе станет None.
    company_pk = company.id
    if not _can_delete_company(user, company):
        messages.error(request, "Нет прав на удаление этой компании.")
        return redirect("company_detail", company_id=company.id)
    req = get_object_or_404(
        CompanyDeletionRequest.objects.select_related("requested_by"),
        id=req_id,
        company_id_snapshot=company.id,
    )
    if req.status != CompanyDeletionRequest.Status.PENDING:
        messages.info(request, "Запрос уже обработан.")
        return redirect("company_detail", company_id=company.id)
    req.status = CompanyDeletionRequest.Status.APPROVED
    req.decided_by = user
    req.decided_at = timezone.now()
    req.save(update_fields=["status", "decided_by", "decided_at"])

    # Уведомляем автора запроса перед удалением — это shipped-up-front: если
    # execute_company_deletion провалится, автор всё равно видит «approved»
    # в UI только после успешного удаления; здесь notify добавляет Notification,
    # которое прочитается на следующем опросе. Порядок сохраняем как был.
    if req.requested_by_id:
        notify(
            user=req.requested_by,
            kind=Notification.Kind.COMPANY,
            title="Запрос на удаление подтверждён",
            body=f"{company.name}: компания удалена",
            url="/companies/",
            payload={
                "company_id": str(company_pk),
                "request_id": req.id,
                "decided_by_id": user.id,
                "decided_by_name": f"{user.last_name} {user.first_name}".strip()
                or user.get_username(),
                "decision": "approved",
            },
        )

    # Phase 3 extract: единый workflow удаления.
    from companies.services import CompanyDeletionError, execute_company_deletion

    try:
        execute_company_deletion(
            company=company,
            actor=user,
            source="approve_request",
            extra_meta={"request_id": req.id},
        )
    except CompanyDeletionError as exc:
        messages.error(request, str(exc))
        return redirect("company_detail", company_id=company_pk)

    messages.success(request, "Компания удалена.")
    return redirect("company_list")


@login_required
@policy_required(resource_type="action", resource="ui:companies:delete")
@require_can_view_company
def company_delete_direct(request: HttpRequest, company_id) -> HttpResponse:
    if request.method != "POST":
        return redirect("company_detail", company_id=company_id)
    user: User = request.user
    company = get_object_or_404(
        Company.objects.select_related("responsible", "branch"), id=company_id
    )
    # Сохраняем исходный ID компании отдельно, т.к. после company.delete() pk на инстансе станет None,
    # а ошибка IntegrityError может возникнуть уже на COMMIT.
    company_pk = company.id
    if not _can_delete_company(user, company):
        messages.error(request, "Нет прав на удаление этой компании.")
        return redirect("company_detail", company_id=company.id)

    reason = (request.POST.get("reason") or "").strip()

    # Phase 3 extract: единый workflow удаления в companies.services.
    from companies.services import CompanyDeletionError, execute_company_deletion

    try:
        execute_company_deletion(
            company=company,
            actor=user,
            reason=reason,
            source="direct",
        )
    except CompanyDeletionError as exc:
        messages.error(request, str(exc))
        # Компания формально ещё существует (транзакция откатилась), но инстанс "битый".
        # Ведём пользователя в список компаний, чтобы избежать NoReverseMatch.
        return redirect("company_detail", company_id=company_pk)

    messages.success(request, "Компания удалена.")
    return redirect("company_list")
