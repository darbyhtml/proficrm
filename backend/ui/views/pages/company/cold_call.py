"""Cold-call toggles + resets (W1.2 refactor).

Extracted из `backend/ui/views/company_detail.py` в W1.2. Zero behavior change.

8 endpoints для 4 entity × 2 action (toggle/reset):
- `company_cold_call_toggle` — POST /companies/<uuid>/cold-call/toggle/
- `company_cold_call_reset` — POST /companies/<uuid>/cold-call/reset/
- `contact_cold_call_toggle` — POST /contacts/<uuid>/cold-call/toggle/
- `contact_cold_call_reset` — POST /contacts/<uuid>/cold-call/reset/
- `contact_phone_cold_call_toggle` — POST /contact-phones/<id>/cold-call/toggle/
- `contact_phone_cold_call_reset` — POST /contact-phones/<id>/cold-call/reset/
- `company_phone_cold_call_toggle` — POST /company-phones/<id>/cold-call/toggle/
- `company_phone_cold_call_reset` — POST /company-phones/<id>/cold-call/reset/

Size note (~650 LOC): все 8 функций структурно идентичны (entity permission check +
confirmation + already-marked check + ColdCallService delegation + AJAX/redirect response).
Дальнейшее разделение на `cold_call_company.py` + `cold_call_contact.py` было бы cosmetic —
оставлено одним модулем для когерентности паттерна.
"""

from __future__ import annotations

import logging

from ui.views._base import (
    ActivityEvent,
    Company,
    CompanyPhone,
    Contact,
    ContactPhone,
    HttpRequest,
    HttpResponse,
    JsonResponse,
    User,
    _can_edit_company,
    _cold_call_json,
    _is_ajax,
    get_object_or_404,
    log_event,
    login_required,
    messages,
    policy_required,
    redirect,
    require_admin,
    require_can_view_company,
)

logger = logging.getLogger(__name__)


@login_required
@require_can_view_company
def company_cold_call_toggle(request: HttpRequest, company_id) -> HttpResponse:
    """
    Отметить основной контакт компании как холодный звонок.
    Отметку можно поставить только один раз.
    """
    if request.method != "POST":
        return redirect("company_detail", company_id=company_id)

    user: User = request.user
    company = get_object_or_404(
        Company.objects.select_related("responsible", "branch", "primary_cold_marked_by"),
        id=company_id,
    )
    if not _can_edit_company(user, company):
        if _is_ajax(request):
            return JsonResponse(
                {"ok": False, "error": "Нет прав на изменение признака 'Холодный звонок'."},
                status=403,
            )
        messages.error(request, "Нет прав на изменение признака 'Холодный звонок'.")
        return redirect("company_detail", company_id=company.id)

    # Проверка подтверждения
    confirmed = request.POST.get("confirmed") == "1"
    if not confirmed:
        if _is_ajax(request):
            return JsonResponse(
                {"ok": False, "error": "Требуется подтверждение действия."}, status=400
            )
        messages.error(request, "Требуется подтверждение действия.")
        return redirect("company_detail", company_id=company.id)

    # Проверка: уже отмечен?
    if company.primary_contact_is_cold_call:
        if _is_ajax(request):
            return _cold_call_json(
                entity="company",
                entity_id=str(company.id),
                is_cold_call=True,
                marked_at=company.primary_cold_marked_at,
                marked_by=str(company.primary_cold_marked_by or ""),
                can_reset=bool(require_admin(user)),
                message="Основной контакт уже отмечен как холодный.",
            )
        messages.info(request, "Основной контакт уже отмечен как холодный.")
        return redirect("company_detail", company_id=company.id)

    from companies.services import ColdCallService

    result = ColdCallService.mark_company(company=company, user=user)

    if result.get("no_phone"):
        if _is_ajax(request):
            return JsonResponse(
                {"ok": False, "error": "У компании не задан основной телефон."}, status=400
            )
        messages.error(request, "У компании не задан основной телефон.")
        return redirect("company_detail", company_id=company.id)

    last_call = result.get("call")

    if _is_ajax(request):
        company.refresh_from_db(
            fields=[
                "primary_contact_is_cold_call",
                "primary_cold_marked_at",
                "primary_cold_marked_by",
            ]
        )
        return _cold_call_json(
            entity="company",
            entity_id=str(company.id),
            is_cold_call=True,
            marked_at=company.primary_cold_marked_at,
            marked_by=str(company.primary_cold_marked_by or ""),
            can_reset=bool(require_admin(user)),
            message="Отмечено: холодный звонок (основной контакт).",
        )

    messages.success(request, "Отмечено: холодный звонок (основной контакт).")
    meta = {}
    if last_call:
        meta["call_id"] = str(last_call.id)
    log_event(
        actor=user,
        verb=ActivityEvent.Verb.UPDATE,
        entity_type="company",
        entity_id=company.id,
        company_id=company.id,
        message="Отмечено: холодный звонок (осн. контакт)",
        meta=meta,
    )
    return redirect("company_detail", company_id=company.id)


@login_required
@policy_required(resource_type="action", resource="ui:companies:cold_call:toggle")
def contact_cold_call_toggle(request: HttpRequest, contact_id) -> HttpResponse:
    """
    Отметить контакт как холодный звонок.
    Отметку можно поставить только один раз.
    """
    if request.method != "POST":
        return redirect("dashboard")
    user: User = request.user
    contact = get_object_or_404(
        Contact.objects.select_related("company", "cold_marked_by"), id=contact_id
    )
    company = contact.company
    if not company:
        messages.error(request, "Контакт не привязан к компании.")
        return redirect("dashboard")
    if not _can_edit_company(user, company):
        if _is_ajax(request):
            return JsonResponse(
                {"ok": False, "error": "Нет прав на изменение контактов этой компании."}, status=403
            )
        messages.error(request, "Нет прав на изменение контактов этой компании.")
        return redirect("company_detail", company_id=company.id)

    # Проверка подтверждения
    confirmed = request.POST.get("confirmed") == "1"
    if not confirmed:
        if _is_ajax(request):
            return JsonResponse(
                {"ok": False, "error": "Требуется подтверждение действия."}, status=400
            )
        messages.error(request, "Требуется подтверждение действия.")
        return redirect("company_detail", company_id=company.id)

    # Проверка: уже отмечен?
    if contact.is_cold_call:
        if _is_ajax(request):
            return _cold_call_json(
                entity="contact",
                entity_id=str(contact.id),
                is_cold_call=True,
                marked_at=contact.cold_marked_at,
                marked_by=str(contact.cold_marked_by or ""),
                can_reset=bool(require_admin(user)),
                message="Контакт уже отмечен как холодный.",
            )
        messages.info(request, "Контакт уже отмечен как холодный.")
        return redirect("company_detail", company_id=company.id)

    from companies.services import ColdCallService

    result = ColdCallService.mark_contact(contact=contact, user=user)
    last_call = result.get("call")

    if _is_ajax(request):
        contact.refresh_from_db(fields=["is_cold_call", "cold_marked_at", "cold_marked_by"])
        return _cold_call_json(
            entity="contact",
            entity_id=str(contact.id),
            is_cold_call=True,
            marked_at=contact.cold_marked_at,
            marked_by=str(contact.cold_marked_by or ""),
            can_reset=bool(require_admin(user)),
            message="Отмечено: холодный звонок (контакт).",
        )

    messages.success(request, "Отмечено: холодный звонок (контакт).")
    meta = {"contact_id": str(contact.id)}
    if last_call:
        meta["call_id"] = str(last_call.id)
    log_event(
        actor=user,
        verb=ActivityEvent.Verb.UPDATE,
        entity_type="contact",
        entity_id=str(contact.id),
        company_id=company.id,
        message="Отмечено: холодный звонок (контакт)",
        meta=meta,
    )
    return redirect("company_detail", company_id=company.id)


@login_required
@require_can_view_company
def company_cold_call_reset(request: HttpRequest, company_id) -> HttpResponse:
    """
    Откатить отметку холодного звонка для основного контакта компании.
    Доступно только администраторам.
    """
    if request.method != "POST":
        return redirect("company_detail", company_id=company_id)

    user: User = request.user
    if not require_admin(user):
        if _is_ajax(request):
            return JsonResponse(
                {
                    "ok": False,
                    "error": "Только администратор может откатить отметку холодного звонка.",
                },
                status=403,
            )
        messages.error(request, "Только администратор может откатить отметку холодного звонка.")
        return redirect("company_detail", company_id=company_id)

    company = get_object_or_404(
        Company.objects.select_related("responsible", "branch"), id=company_id
    )

    if not company.primary_contact_is_cold_call:
        if _is_ajax(request):
            return _cold_call_json(
                entity="company",
                entity_id=str(company.id),
                is_cold_call=False,
                marked_at=company.primary_cold_marked_at,
                marked_by=str(company.primary_cold_marked_by or ""),
                can_reset=True,
                message="Основной контакт не отмечен как холодный.",
            )
        messages.info(request, "Основной контакт не отмечен как холодный.")
        return redirect("company_detail", company_id=company.id)

    from companies.services import ColdCallService

    ColdCallService.reset_company(company=company, user=user)

    if _is_ajax(request):
        return _cold_call_json(
            entity="company",
            entity_id=str(company.id),
            is_cold_call=False,
            marked_at=None,
            marked_by="",
            can_reset=True,
            message="Отметка холодного звонка отменена (основной контакт).",
        )

    messages.success(request, "Отметка холодного звонка отменена (основной контакт).")
    log_event(
        actor=user,
        verb=ActivityEvent.Verb.UPDATE,
        entity_type="company",
        entity_id=company.id,
        company_id=company.id,
        message="Откат: холодный звонок (осн. контакт)",
    )
    return redirect("company_detail", company_id=company.id)


@login_required
@policy_required(resource_type="action", resource="ui:companies:cold_call:reset")
def contact_cold_call_reset(request: HttpRequest, contact_id) -> HttpResponse:
    """
    Откатить отметку холодного звонка для контакта.
    Доступно только администраторам.
    """
    if request.method != "POST":
        return redirect("dashboard")

    user: User = request.user
    if not require_admin(user):
        if _is_ajax(request):
            return JsonResponse(
                {
                    "ok": False,
                    "error": "Только администратор может откатить отметку холодного звонка.",
                },
                status=403,
            )
        messages.error(request, "Только администратор может откатить отметку холодного звонка.")
        return redirect("dashboard")

    contact = get_object_or_404(Contact.objects.select_related("company"), id=contact_id)
    company = contact.company
    if not company:
        messages.error(request, "Контакт не привязан к компании.")
        return redirect("dashboard")

    if not contact.is_cold_call:
        if _is_ajax(request):
            return _cold_call_json(
                entity="contact",
                entity_id=str(contact.id),
                is_cold_call=False,
                marked_at=contact.cold_marked_at,
                marked_by=str(contact.cold_marked_by or ""),
                can_reset=True,
                message="Контакт не отмечен как холодный.",
            )
        messages.info(request, "Контакт не отмечен как холодный.")
        return redirect("company_detail", company_id=company.id)

    from companies.services import ColdCallService

    ColdCallService.reset_contact(contact=contact, user=user)

    if _is_ajax(request):
        return _cold_call_json(
            entity="contact",
            entity_id=str(contact.id),
            is_cold_call=False,
            marked_at=None,
            marked_by="",
            can_reset=True,
            message="Отметка холодного звонка отменена (контакт).",
        )

    messages.success(request, "Отметка холодного звонка отменена (контакт).")
    log_event(
        actor=user,
        verb=ActivityEvent.Verb.UPDATE,
        entity_type="contact",
        entity_id=str(contact.id),
        company_id=company.id,
        message="Откат: холодный звонок (контакт)",
    )
    return redirect("company_detail", company_id=company.id)


@login_required
@policy_required(resource_type="action", resource="ui:companies:cold_call:toggle")
def contact_phone_cold_call_toggle(request: HttpRequest, contact_phone_id) -> HttpResponse:
    """
    Отметить конкретный номер телефона контакта как холодный звонок.
    Отметку можно поставить только один раз.
    """
    if request.method != "POST":
        return redirect("dashboard")
    user: User = request.user
    try:
        contact_phone = get_object_or_404(
            ContactPhone.objects.select_related("contact__company", "cold_marked_by"),
            id=contact_phone_id,
        )
    except Exception as e:
        logger.error(f"Error finding ContactPhone {contact_phone_id}: {e}", exc_info=True)
        if _is_ajax(request):
            return JsonResponse(
                {"ok": False, "error": "Ошибка: номер телефона не найден."}, status=404
            )
        messages.error(request, "Ошибка: номер телефона не найден.")
        return redirect("dashboard")
    contact = contact_phone.contact
    company = contact.company if contact else None
    if not company:
        messages.error(request, "Контакт не привязан к компании.")
        return redirect("dashboard")
    if not _can_edit_company(user, company):
        if _is_ajax(request):
            return JsonResponse(
                {"ok": False, "error": "Нет прав на изменение контактов этой компании."}, status=403
            )
        messages.error(request, "Нет прав на изменение контактов этой компании.")
        return redirect("company_detail", company_id=company.id)

    # Проверка подтверждения
    confirmed = request.POST.get("confirmed") == "1"
    if not confirmed:
        if _is_ajax(request):
            return JsonResponse(
                {"ok": False, "error": "Требуется подтверждение действия."}, status=400
            )
        messages.error(request, "Требуется подтверждение действия.")
        return redirect("company_detail", company_id=company.id)

    # Проверка: уже отмечен?
    if contact_phone.is_cold_call:
        if _is_ajax(request):
            return _cold_call_json(
                entity="contact_phone",
                entity_id=str(contact_phone.id),
                is_cold_call=True,
                marked_at=contact_phone.cold_marked_at,
                marked_by=str(contact_phone.cold_marked_by or ""),
                can_reset=bool(require_admin(user)),
                message="Этот номер уже отмечен как холодный.",
            )
        messages.info(request, "Этот номер уже отмечен как холодный.")
        return redirect("company_detail", company_id=company.id)

    from companies.services import ColdCallService

    result = ColdCallService.mark_contact_phone(contact_phone=contact_phone, user=user)
    last_call = result.get("call")

    if _is_ajax(request):
        contact_phone.refresh_from_db(fields=["is_cold_call", "cold_marked_at", "cold_marked_by"])
        return _cold_call_json(
            entity="contact_phone",
            entity_id=str(contact_phone.id),
            is_cold_call=True,
            marked_at=contact_phone.cold_marked_at,
            marked_by=str(contact_phone.cold_marked_by or ""),
            can_reset=bool(require_admin(user)),
            message=f"Отмечено: холодный звонок (номер {contact_phone.value}).",
        )

    messages.success(request, f"Отмечено: холодный звонок (номер {contact_phone.value}).")
    meta = {"contact_phone_id": str(contact_phone.id)}
    if last_call:
        meta["call_id"] = str(last_call.id)
    log_event(
        actor=user,
        verb=ActivityEvent.Verb.UPDATE,
        entity_type="contact_phone",
        entity_id=str(contact_phone.id),
        company_id=company.id,
        message=f"Отмечено: холодный звонок (номер {contact_phone.value})",
        meta=meta,
    )
    return redirect("company_detail", company_id=company.id)


@login_required
@policy_required(resource_type="action", resource="ui:companies:cold_call:reset")
def contact_phone_cold_call_reset(request: HttpRequest, contact_phone_id) -> HttpResponse:
    """
    Откатить отметку холодного звонка для конкретного номера телефона контакта.
    Доступно только администраторам.
    """
    if request.method != "POST":
        return redirect("dashboard")

    user: User = request.user
    if not require_admin(user):
        if _is_ajax(request):
            return JsonResponse(
                {
                    "ok": False,
                    "error": "Только администратор может откатить отметку холодного звонка.",
                },
                status=403,
            )
        messages.error(request, "Только администратор может откатить отметку холодного звонка.")
        return redirect("dashboard")

    contact_phone = get_object_or_404(
        ContactPhone.objects.select_related("contact__company"), id=contact_phone_id
    )
    contact = contact_phone.contact
    company = contact.company if contact else None
    if not company:
        messages.error(request, "Контакт не привязан к компании.")
        return redirect("dashboard")

    if not contact_phone.is_cold_call and not contact_phone.cold_marked_at:
        if _is_ajax(request):
            return _cold_call_json(
                entity="contact_phone",
                entity_id=str(contact_phone.id),
                is_cold_call=False,
                marked_at=contact_phone.cold_marked_at,
                marked_by=str(contact_phone.cold_marked_by or ""),
                can_reset=True,
                message="Этот номер не отмечен как холодный.",
            )
        messages.info(request, "Этот номер не отмечен как холодный.")
        return redirect("company_detail", company_id=company.id)

    from companies.services import ColdCallService

    ColdCallService.reset_contact_phone(contact_phone=contact_phone, user=user)

    if _is_ajax(request):
        return _cold_call_json(
            entity="contact_phone",
            entity_id=str(contact_phone.id),
            is_cold_call=False,
            marked_at=None,
            marked_by="",
            can_reset=True,
            message=f"Отметка холодного звонка отменена (номер {contact_phone.value}).",
        )

    messages.success(request, f"Отметка холодного звонка отменена (номер {contact_phone.value}).")
    log_event(
        actor=user,
        verb=ActivityEvent.Verb.UPDATE,
        entity_type="contact_phone",
        entity_id=str(contact_phone.id),
        company_id=company.id,
        message=f"Откат: холодный звонок (номер {contact_phone.value})",
    )
    return redirect("company_detail", company_id=company.id)


@login_required
@policy_required(resource_type="action", resource="ui:companies:cold_call:toggle")
def company_phone_cold_call_toggle(request: HttpRequest, company_phone_id) -> HttpResponse:
    """
    Отметить конкретный дополнительный номер телефона компании как холодный звонок.
    Аналогично contact_phone_cold_call_toggle, но для CompanyPhone.
    """
    if request.method != "POST":
        return redirect("dashboard")
    user: User = request.user
    try:
        company_phone = get_object_or_404(
            CompanyPhone.objects.select_related("company", "cold_marked_by"), id=company_phone_id
        )
    except Exception as e:
        logger.error(f"Error finding CompanyPhone {company_phone_id}: {e}", exc_info=True)
        if _is_ajax(request):
            return JsonResponse(
                {"ok": False, "error": "Ошибка: номер телефона не найден."}, status=404
            )
        messages.error(request, "Ошибка: номер телефона не найден.")
        return redirect("dashboard")
    company = company_phone.company
    if not _can_edit_company(user, company):
        if _is_ajax(request):
            return JsonResponse(
                {"ok": False, "error": "Нет прав на изменение данных этой компании."}, status=403
            )
        messages.error(request, "Нет прав на изменение данных этой компании.")
        return redirect("company_detail", company_id=company.id)

    # Проверка подтверждения
    confirmed = request.POST.get("confirmed") == "1"
    if not confirmed:
        if _is_ajax(request):
            return JsonResponse(
                {"ok": False, "error": "Требуется подтверждение действия."}, status=400
            )
        messages.error(request, "Требуется подтверждение действия.")
        return redirect("company_detail", company_id=company.id)

    # Проверка: уже отмечен?
    if company_phone.is_cold_call:
        if _is_ajax(request):
            return _cold_call_json(
                entity="company_phone",
                entity_id=str(company_phone.id),
                is_cold_call=True,
                marked_at=company_phone.cold_marked_at,
                marked_by=str(company_phone.cold_marked_by or ""),
                can_reset=bool(require_admin(user)),
                message="Этот номер уже отмечен как холодный.",
            )
        messages.info(request, "Этот номер уже отмечен как холодный.")
        return redirect("company_detail", company_id=company.id)

    from companies.services import ColdCallService

    result = ColdCallService.mark_company_phone(company_phone=company_phone, user=user)
    last_call = result.get("call")

    if _is_ajax(request):
        company_phone.refresh_from_db(fields=["is_cold_call", "cold_marked_at", "cold_marked_by"])
        return _cold_call_json(
            entity="company_phone",
            entity_id=str(company_phone.id),
            is_cold_call=True,
            marked_at=company_phone.cold_marked_at,
            marked_by=str(company_phone.cold_marked_by or ""),
            can_reset=bool(require_admin(user)),
            message=f"Отмечено: холодный звонок (номер {company_phone.value}).",
        )

    messages.success(request, f"Отмечено: холодный звонок (номер {company_phone.value}).")
    meta = {"company_phone_id": str(company_phone.id)}
    if last_call:
        meta["call_id"] = str(last_call.id)
    log_event(
        actor=user,
        verb=ActivityEvent.Verb.UPDATE,
        entity_type="company_phone",
        entity_id=str(company_phone.id),
        company_id=company.id,
        message=f"Отмечено: холодный звонок (номер {company_phone.value})",
        meta=meta,
    )
    return redirect("company_detail", company_id=company.id)


@login_required
@policy_required(resource_type="action", resource="ui:companies:cold_call:reset")
def company_phone_cold_call_reset(request: HttpRequest, company_phone_id) -> HttpResponse:
    """
    Откатить отметку холодного звонка для конкретного дополнительного номера телефона компании.
    Доступно только администраторам.
    """
    if request.method != "POST":
        return redirect("dashboard")

    user: User = request.user
    if not require_admin(user):
        if _is_ajax(request):
            return JsonResponse(
                {
                    "ok": False,
                    "error": "Только администратор может откатить отметку холодного звонка.",
                },
                status=403,
            )
        messages.error(request, "Только администратор может откатить отметку холодного звонка.")
        return redirect("dashboard")

    company_phone = get_object_or_404(
        CompanyPhone.objects.select_related("company"), id=company_phone_id
    )
    company = company_phone.company

    if not company_phone.is_cold_call and not company_phone.cold_marked_at:
        if _is_ajax(request):
            return _cold_call_json(
                entity="company_phone",
                entity_id=str(company_phone.id),
                is_cold_call=False,
                marked_at=company_phone.cold_marked_at,
                marked_by=str(company_phone.cold_marked_by or ""),
                can_reset=True,
                message="Этот номер не отмечен как холодный.",
            )
        messages.info(request, "Этот номер не отмечен как холодный.")
        return redirect("company_detail", company_id=company.id)

    from companies.services import ColdCallService

    ColdCallService.reset_company_phone(company_phone=company_phone, user=user)

    if _is_ajax(request):
        return _cold_call_json(
            entity="company_phone",
            entity_id=str(company_phone.id),
            is_cold_call=False,
            marked_at=None,
            marked_by="",
            can_reset=True,
            message=f"Отметка холодного звонка отменена (номер {company_phone.value}).",
        )

    messages.success(request, f"Отметка холодного звонка отменена (номер {company_phone.value}).")
    log_event(
        actor=user,
        verb=ActivityEvent.Verb.UPDATE,
        entity_type="company_phone",
        entity_id=str(company_phone.id),
        company_id=company.id,
        message=f"Откат: холодный звонок (номер {company_phone.value})",
    )
    return redirect("company_detail", company_id=company.id)
