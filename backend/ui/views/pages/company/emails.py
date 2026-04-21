"""Company email updates (W1.2 refactor).

Extracted из `backend/ui/views/company_detail.py` в W1.2. Zero behavior change.

Endpoints:
- `company_main_email_update` — POST /companies/<uuid>/main-email-update/
- `company_email_value_update` — POST /company-emails/<id>/value-update/
"""

from __future__ import annotations

import logging

from ui.views._base import (
    ActivityEvent,
    Company,
    CompanyEmail,
    HttpRequest,
    HttpResponse,
    JsonResponse,
    User,
    _can_edit_company,
    get_object_or_404,
    log_event,
    login_required,
    messages,
    policy_required,
    redirect,
    require_can_view_company,
)

logger = logging.getLogger(__name__)


@login_required
@policy_required(resource_type="action", resource="ui:companies:update")
@require_can_view_company
def company_main_email_update(request: HttpRequest, company_id) -> HttpResponse:
    """Обновление основного email компании (AJAX)"""
    if request.method != "POST":
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"success": False, "error": "Метод не разрешен."}, status=405)
        return redirect("company_detail", company_id=company_id)

    user: User = request.user
    company = get_object_or_404(
        Company.objects.select_related("responsible", "branch"), id=company_id
    )
    if not _can_edit_company(user, company):
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse(
                {"success": False, "error": "Нет прав на редактирование этой компании."}, status=403
            )
        messages.error(request, "Нет прав на редактирование этой компании.")
        return redirect("company_detail", company_id=company.id)

    # Phase 2 extract: company_emails сервис.
    from companies.services import check_email_duplicate, validate_email_value

    email, err = validate_email_value(
        request.POST.get("email") or "",
        allow_empty=True,  # Основной email можно очистить
    )
    if err:
        return JsonResponse({"success": False, "error": err}, status=400)
    dup_err = check_email_duplicate(
        company=company,
        email=email,
        check_main=False,  # Для основного проверяем только доп.
    )
    if dup_err:
        return JsonResponse({"success": False, "error": dup_err}, status=400)

    company.email = email
    company.save(update_fields=["email", "updated_at"])

    log_event(
        actor=user,
        verb=ActivityEvent.Verb.UPDATE,
        entity_type="company",
        entity_id=company.id,
        company_id=company.id,
        message="Инлайн: обновлен основной email",
    )
    return JsonResponse({"success": True, "email": email})


@login_required
@policy_required(resource_type="action", resource="ui:companies:update")
def company_email_value_update(request: HttpRequest, company_email_id) -> HttpResponse:
    """Обновление значения дополнительного email компании (AJAX)"""
    if request.method != "POST":
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"success": False, "error": "Метод не разрешен."}, status=405)
        return redirect("dashboard")

    user: User = request.user
    company_email = get_object_or_404(
        CompanyEmail.objects.select_related("company"), id=company_email_id
    )
    company = company_email.company
    if not _can_edit_company(user, company):
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse(
                {"success": False, "error": "Нет прав на редактирование этой компании."}, status=403
            )
        messages.error(request, "Нет прав на редактирование этой компании.")
        return redirect("company_detail", company_id=company.id)

    # Phase 2 extract: company_emails сервис.
    from companies.services import check_email_duplicate, validate_email_value

    email, err = validate_email_value(request.POST.get("email") or "")
    if err:
        return JsonResponse({"success": False, "error": err}, status=400)
    dup_err = check_email_duplicate(
        company=company,
        email=email,
        exclude_email_id=company_email.id,
    )
    if dup_err:
        return JsonResponse({"success": False, "error": dup_err}, status=400)

    company_email.value = email
    company_email.save(update_fields=["value"])

    log_event(
        actor=user,
        verb=ActivityEvent.Verb.UPDATE,
        entity_type="company_email",
        entity_id=str(company_email.id),
        company_id=company.id,
        message="Инлайн: обновлен дополнительный email",
    )

    return JsonResponse({"success": True, "email": email})
