"""Company + contact phone CRUD + comments (W1.2 refactor).

Extracted из `backend/ui/views/company_detail.py` в W1.2. Zero behavior change.

Endpoints:
- `company_main_phone_update` — POST /companies/<uuid>/main-phone-update/
- `company_phone_value_update` — POST /company-phones/<id>/value-update/
- `company_phone_delete` — POST /company-phones/<id>/delete/
- `company_phone_create` — POST /companies/<uuid>/phones/create/
- `company_main_phone_comment_update` — POST /companies/<uuid>/main-phone-comment-update/
- `company_phone_comment_update` — POST /company-phones/<id>/comment-update/
- `contact_phone_comment_update` — POST /contact-phones/<id>/comment-update/
"""

from __future__ import annotations

import logging

from ui.views._base import (
    ActivityEvent,
    Company,
    CompanyPhone,
    ContactPhone,
    Http404,
    HttpRequest,
    HttpResponse,
    JsonResponse,
    User,
    _can_edit_company,
    format_phone,
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
def company_main_phone_update(request: HttpRequest, company_id) -> HttpResponse:
    """Обновление основного телефона компании (AJAX)"""
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

    # Phase 2 extract: валидация и duplicate-check вынесены в companies.services.
    from companies.services import check_phone_duplicate, validate_phone_main

    normalized, err = validate_phone_main(request.POST.get("phone") or "")
    if err:
        return JsonResponse({"success": False, "error": err}, status=400)
    # Для основного телефона проверяем только дубли с доп. номерами
    # (check_main=True игнорируется т.к. company.phone и есть "основной"
    # — но check_phone_duplicate сравнивает normalized с company.phone,
    # так что для основного — check только среди CompanyPhone).
    if normalized and CompanyPhone.objects.filter(company=company, value=normalized).exists():
        return JsonResponse(
            {"success": False, "error": "Такой телефон уже есть в дополнительных номерах."},
            status=400,
        )

    company.phone = normalized
    company.save(update_fields=["phone", "updated_at"])

    log_event(
        actor=user,
        verb=ActivityEvent.Verb.UPDATE,
        entity_type="company",
        entity_id=company.id,
        company_id=company.id,
        message="Инлайн: обновлен основной телефон",
    )

    try:
        from ui.templatetags.ui_extras import phone_local_info  # type: ignore

        local_info = phone_local_info(normalized)
    except Exception:
        local_info = ""

    return JsonResponse(
        {
            "success": True,
            "phone": normalized,
            "display": format_phone(normalized) if normalized else "—",
            "local_info": local_info,
        }
    )


@login_required
@policy_required(resource_type="action", resource="ui:companies:update")
def company_phone_value_update(request: HttpRequest, company_phone_id) -> HttpResponse:
    """Обновление значения дополнительного телефона компании (AJAX)"""
    if request.method != "POST":
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"success": False, "error": "Метод не разрешен."}, status=405)
        return redirect("dashboard")

    user: User = request.user
    company_phone = get_object_or_404(
        CompanyPhone.objects.select_related("company"), id=company_phone_id
    )
    company = company_phone.company
    if not _can_edit_company(user, company):
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse(
                {"success": False, "error": "Нет прав на редактирование этой компании."}, status=403
            )
        messages.error(request, "Нет прав на редактирование этой компании.")
        return redirect("company_detail", company_id=company.id)

    # Phase 2 extract: валидация и duplicate-check в companies.services.
    from companies.services import check_phone_duplicate, validate_phone_strict

    normalized, err = validate_phone_strict(request.POST.get("phone") or "")
    if err:
        return JsonResponse({"success": False, "error": err}, status=400)
    dup_err = check_phone_duplicate(
        company=company,
        normalized=normalized,
        exclude_phone_id=company_phone.id,
    )
    if dup_err:
        return JsonResponse({"success": False, "error": dup_err}, status=400)

    company_phone.value = normalized
    company_phone.save(update_fields=["value"])

    log_event(
        actor=user,
        verb=ActivityEvent.Verb.UPDATE,
        entity_type="company_phone",
        entity_id=str(company_phone.id),
        company_id=company.id,
        message="Инлайн: обновлен дополнительный телефон",
    )

    try:
        from ui.templatetags.ui_extras import phone_local_info  # type: ignore

        local_info = phone_local_info(normalized)
    except Exception:
        local_info = ""

    return JsonResponse(
        {
            "success": True,
            "phone": normalized,
            "display": format_phone(normalized),
            "local_info": local_info,
        }
    )


@login_required
@policy_required(resource_type="action", resource="ui:companies:update")
def company_phone_delete(request: HttpRequest, company_phone_id) -> HttpResponse:
    """F4 R3: удаление доп. телефона компании (AJAX).

    Classic не содержал отдельного endpoint для удаления одного номера.
    В v3/b/ popup-меню предлагает действие «Удалить» — добавляем сюда.
    """
    if request.method != "POST":
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"success": False, "error": "Метод не разрешен."}, status=405)
        return redirect("dashboard")
    user: User = request.user
    company_phone = get_object_or_404(
        CompanyPhone.objects.select_related("company"), id=company_phone_id
    )
    company = company_phone.company
    if not _can_edit_company(user, company):
        return JsonResponse(
            {"success": False, "error": "Нет прав на редактирование этой компании."}, status=403
        )
    phone_value = company_phone.value
    company_phone.delete()
    log_event(
        actor=user,
        verb=ActivityEvent.Verb.DELETE,
        entity_type="company_phone",
        entity_id=str(company_phone_id),
        company_id=company.id,
        message=f"Удалён дополнительный телефон: {phone_value}",
    )
    return JsonResponse({"success": True})


@login_required
@policy_required(resource_type="action", resource="ui:companies:update")
@require_can_view_company
def company_phone_create(request: HttpRequest, company_id) -> HttpResponse:
    """Создание дополнительного телефона компании (AJAX)"""
    if request.method != "POST":
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"success": False, "error": "Метод не разрешен."}, status=405)
        return redirect("company_detail", company_id=company_id)

    user: User = request.user
    company = get_object_or_404(
        Company.objects.select_related("responsible", "branch"), id=company_id
    )
    if not _can_edit_company(user, company):
        return JsonResponse(
            {"success": False, "error": "Нет прав на редактирование этой компании."}, status=403
        )

    # Phase 2 extract: валидация/duplicate/comment-check в companies.services.
    from companies.services import (
        check_phone_duplicate,
        validate_phone_comment,
        validate_phone_strict,
    )

    normalized, err = validate_phone_strict(request.POST.get("phone") or "")
    if err:
        return JsonResponse({"success": False, "error": err}, status=400)
    # Для create только проверка main-дубля вне транзакции — внутри же
    # пересчитаем с select_for_update для гонок на доп. номерах.
    if (company.phone or "").strip() == normalized:
        return JsonResponse(
            {"success": False, "error": "Этот телефон уже указан как основной."},
            status=400,
        )
    comment_raw, c_err = validate_phone_comment(request.POST.get("comment") or "")
    if c_err:
        return JsonResponse({"success": False, "error": c_err}, status=400)

    from django.db import transaction
    from django.db.models import Max

    with transaction.atomic():
        dup_err = check_phone_duplicate(company=company, normalized=normalized)
        if dup_err:
            return JsonResponse({"success": False, "error": dup_err}, status=400)

        max_order = (
            CompanyPhone.objects.select_for_update()
            .filter(company=company)
            .aggregate(m=Max("order"))
            .get("m")
        )
        next_order = int(max_order) + 1 if max_order is not None else 0

        company_phone = CompanyPhone.objects.create(
            company=company,
            value=normalized,
            order=next_order,
            comment=comment_raw,
        )
    log_event(
        actor=user,
        verb=ActivityEvent.Verb.CREATE,
        entity_type="company_phone",
        entity_id=str(company_phone.id),
        company_id=company.id,
        message="Инлайн: добавлен дополнительный телефон",
    )

    try:
        from ui.templatetags.ui_extras import phone_local_info  # type: ignore

        local_info = phone_local_info(normalized)
    except Exception:
        local_info = ""

    return JsonResponse(
        {
            "success": True,
            "id": company_phone.id,
            "phone": normalized,
            "display": format_phone(normalized),
            "local_info": local_info,
        }
    )


@login_required
@policy_required(resource_type="action", resource="ui:companies:update")
@require_can_view_company
def company_main_phone_comment_update(request: HttpRequest, company_id) -> HttpResponse:
    """Обновление комментария к основному телефону компании (AJAX)"""
    if request.method != "POST":
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"error": "Метод не разрешен."}, status=405)
        return redirect("company_detail", company_id=company_id)

    user: User = request.user
    try:
        company = Company.objects.select_related("responsible", "branch").get(id=company_id)
    except Company.DoesNotExist:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"error": "Компания не найдена."}, status=404)
        raise Http404("Компания не найдена")

    if not _can_edit_company(user, company):
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"error": "Нет прав на редактирование этой компании."}, status=403)
        messages.error(request, "Нет прав на редактирование этой компании.")
        return redirect("company_detail", company_id=company.id)

    comment = (request.POST.get("comment") or "").strip()[:255]
    company.phone_comment = comment
    company.save(update_fields=["phone_comment"])

    log_event(
        actor=user,
        verb=ActivityEvent.Verb.UPDATE,
        entity_type="company",
        entity_id=company.id,
        company_id=company.id,
        message=f"Обновлен комментарий к основному телефону: {comment[:50] if comment else '(удален)'}",
    )

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({"success": True, "comment": comment})

    messages.success(request, "Комментарий обновлен.")
    return redirect("company_detail", company_id=company.id)


@login_required
@policy_required(resource_type="action", resource="ui:companies:update")
def company_phone_comment_update(request: HttpRequest, company_phone_id) -> HttpResponse:
    """Обновление комментария к дополнительному телефону компании (AJAX)"""
    if request.method != "POST":
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"error": "Метод не разрешен."}, status=405)
        return redirect("dashboard")

    user: User = request.user
    try:
        company_phone = CompanyPhone.objects.select_related("company").get(id=company_phone_id)
    except CompanyPhone.DoesNotExist:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"error": "Номер телефона не найден."}, status=404)
        raise Http404("Номер телефона не найден")

    company = company_phone.company
    if not _can_edit_company(user, company):
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"error": "Нет прав на редактирование этой компании."}, status=403)
        messages.error(request, "Нет прав на редактирование этой компании.")
        return redirect("company_detail", company_id=company.id)

    comment = (request.POST.get("comment") or "").strip()[:255]
    company_phone.comment = comment
    company_phone.save(update_fields=["comment"])

    log_event(
        actor=user,
        verb=ActivityEvent.Verb.UPDATE,
        entity_type="company_phone",
        entity_id=str(company_phone.id),
        company_id=company.id,
        message=f"Обновлен комментарий к телефону {company_phone.value}: {comment[:50] if comment else '(удален)'}",
    )

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({"success": True, "comment": comment})

    messages.success(request, "Комментарий обновлен.")
    return redirect("company_detail", company_id=company.id)


@login_required
@policy_required(resource_type="action", resource="ui:companies:update")
def contact_phone_comment_update(request: HttpRequest, contact_phone_id) -> HttpResponse:
    """Обновление комментария к телефону контакта (AJAX)"""
    if request.method != "POST":
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"error": "Метод не разрешен."}, status=405)
        return redirect("dashboard")

    user: User = request.user
    try:
        contact_phone = ContactPhone.objects.select_related("contact__company").get(
            id=contact_phone_id
        )
    except ContactPhone.DoesNotExist:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"error": "Номер телефона не найден."}, status=404)
        raise Http404("Номер телефона не найден")

    contact = contact_phone.contact
    company = contact.company if contact else None
    if not company:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"error": "Контакт не привязан к компании."}, status=400)
        messages.error(request, "Контакт не привязан к компании.")
        return redirect("dashboard")

    if not _can_edit_company(user, company):
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"error": "Нет прав на редактирование этой компании."}, status=403)
        messages.error(request, "Нет прав на редактирование этой компании.")
        return redirect("company_detail", company_id=company.id)

    comment = (request.POST.get("comment") or "").strip()[:255]
    contact_phone.comment = comment
    contact_phone.save(update_fields=["comment"])

    log_event(
        actor=user,
        verb=ActivityEvent.Verb.UPDATE,
        entity_type="contact_phone",
        entity_id=str(contact_phone.id),
        company_id=company.id,
        message=f"Обновлен комментарий к телефону {contact_phone.value}: {comment[:50] if comment else '(удален)'}",
    )

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({"success": True, "comment": comment})

    messages.success(request, "Комментарий обновлен.")
    return redirect("company_detail", company_id=company.id)
