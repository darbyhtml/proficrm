"""Company edit / update / transfer / contract (W1.2 refactor).

Extracted из `backend/ui/views/company_detail.py` в W1.2. Zero behavior change.

Endpoints:
- `company_edit` — GET/POST /companies/<uuid>/edit/
- `company_transfer` — POST /companies/<uuid>/transfer/
- `company_update` — POST /companies/<uuid>/update/
- `company_inline_update` — POST /companies/<uuid>/inline-update/
- `company_contract_update` — POST /companies/<uuid>/contract-update/
"""

from __future__ import annotations

import logging

from ui.views._base import (
    ActivityEvent,
    Company,
    CompanyContractForm,
    CompanyEditForm,
    CompanyEmail,
    CompanyInlineEditForm,
    CompanyPhone,
    CompanyQuickEditForm,
    ContractType,
    HttpRequest,
    HttpResponse,
    JsonResponse,
    User,
    ValidationError,
    _can_edit_company,
    _invalidate_company_count_cache,
    get_object_or_404,
    log_event,
    login_required,
    messages,
    policy_required,
    redirect,
    render,
    require_can_view_company,
    timezone,
    transaction,
)

logger = logging.getLogger(__name__)


@login_required
@policy_required(resource_type="action", resource="ui:companies:contract:update")
@require_can_view_company
def company_contract_update(request: HttpRequest, company_id) -> HttpResponse:
    if request.method != "POST":
        return redirect("company_detail", company_id=company_id)

    user: User = request.user
    company = get_object_or_404(
        Company.objects.select_related("responsible", "branch"), id=company_id
    )
    if not _can_edit_company(user, company):
        messages.error(request, "Нет прав на изменение договора по этой компании.")
        return redirect("company_detail", company_id=company.id)

    form = CompanyContractForm(request.POST, instance=company)
    if not form.is_valid():
        messages.error(request, "Проверьте поля договора.")
        return redirect("company_detail", company_id=company.id)

    contract_type = form.cleaned_data.get("contract_type")
    if contract_type:
        if contract_type.is_annual:
            # Для годовых: очищаем дату окончания и явно берём сумму из POST (модалка рендерит свой input)
            company.contract_until = None
            raw_amount = (request.POST.get("contract_amount") or "").strip()
            if raw_amount:
                try:
                    from decimal import Decimal

                    company.contract_amount = Decimal(raw_amount.replace(",", "."))
                except (ValueError, TypeError):
                    company.contract_amount = None
            else:
                company.contract_amount = None
        else:
            # Для негодовых: очищаем сумму
            company.contract_amount = None

    form.save()
    messages.success(request, "Данные договора обновлены.")
    log_event(
        actor=user,
        verb=ActivityEvent.Verb.UPDATE,
        entity_type="company",
        entity_id=company.id,
        company_id=company.id,
        message="Обновлены данные договора",
    )
    return redirect("company_detail", company_id=company.id)


@login_required
@policy_required(resource_type="action", resource="ui:companies:update")
@require_can_view_company
def company_edit(request: HttpRequest, company_id) -> HttpResponse:
    user: User = request.user
    company = get_object_or_404(
        Company.objects.select_related("responsible", "branch", "status"), id=company_id
    )
    if not _can_edit_company(user, company):
        messages.error(request, "Нет прав на редактирование данных компании.")
        return redirect("company_detail", company_id=company.id)

    company_emails: list[CompanyEmail] = []
    company_phones: list[CompanyPhone] = []

    if request.method == "POST":
        form = CompanyEditForm(request.POST, instance=company, user=user)
        if form.is_valid():
            # Вспомогательные структуры: (index, value)
            new_company_emails: list[tuple[int, str]] = []
            new_company_phones: list[tuple[int, str]] = []

            # Сохраняем множественные email адреса
            for key, value in request.POST.items():
                if key.startswith("company_emails_"):
                    raw = (value or "").strip()
                    if not raw:
                        continue
                    try:
                        index = int(key.replace("company_emails_", ""))
                    except (ValueError, TypeError):
                        continue
                    new_company_emails.append((index, raw))

            # Сохраняем множественные телефоны компании
            for key, value in request.POST.items():
                if key.startswith("company_phones_"):
                    raw = (value or "").strip()
                    if not raw:
                        continue
                    try:
                        index = int(key.replace("company_phones_", ""))
                    except (ValueError, TypeError):
                        continue
                    new_company_phones.append((index, raw))

            # Валидация телефонов: проверка на дубликаты и использование в других контактах
            from companies.normalizers import normalize_phone as _normalize_phone

            # Собираем все телефоны (основной + дополнительные)
            all_phones = []
            main_phone = (form.cleaned_data.get("phone") or "").strip()
            if main_phone:
                normalized_main = _normalize_phone(main_phone)
                if normalized_main:
                    all_phones.append(normalized_main)

            normalized_phones = []
            for _, phone_value in new_company_phones:
                normalized = _normalize_phone(phone_value)
                if normalized:
                    normalized_phones.append(normalized)
                    all_phones.append(normalized)

            # Проверка на дубликаты в самой форме (включая основной телефон)
            if len(all_phones) != len(set(all_phones)):
                form.add_error(
                    None,
                    "Есть повторяющиеся телефоны (основной телефон не должен совпадать с дополнительными).",
                )
                # Восстанавливаем введённые значения для отображения ошибки
                for key, value in request.POST.items():
                    if key.startswith("company_emails_"):
                        company_emails.append(
                            CompanyEmail(company=company, value=(value or "").strip())
                        )
                    if key.startswith("company_phones_"):
                        company_phones.append(
                            CompanyPhone(company=company, value=(value or "").strip())
                        )
                return render(
                    request,
                    "ui/company_edit.html",
                    {
                        "company": company,
                        "form": form,
                        "company_emails": company_emails,
                        "company_phones": company_phones,
                    },
                )

            # Сохраняем форму (включая основной телефон)
            form.save()

            # Удаляем старые значения и создаем новые в упорядоченном виде
            CompanyEmail.objects.filter(company=company).delete()
            CompanyPhone.objects.filter(company=company).delete()

            for order, email_value in sorted(new_company_emails, key=lambda x: x[0]):
                CompanyEmail.objects.create(company=company, value=email_value, order=order)

            for order, phone_value in sorted(new_company_phones, key=lambda x: x[0]):
                # Нормализуем телефон перед сохранением
                normalized = _normalize_phone(phone_value)
                CompanyPhone.objects.create(
                    company=company, value=normalized if normalized else phone_value, order=order
                )

            messages.success(request, "Данные компании обновлены.")
            log_event(
                actor=user,
                verb=ActivityEvent.Verb.UPDATE,
                entity_type="company",
                entity_id=company.id,
                company_id=company.id,
                message="Обновлены данные компании",
            )
            return redirect("company_detail", company_id=company.id)
        else:
            # При ошибках в форме восстанавливаем введённые значения из POST,
            # чтобы пользователь не потерял данные.
            for key, value in request.POST.items():
                if key.startswith("company_emails_"):
                    company_emails.append(
                        CompanyEmail(company=company, value=(value or "").strip())
                    )
                if key.startswith("company_phones_"):
                    company_phones.append(
                        CompanyPhone(company=company, value=(value or "").strip())
                    )
    else:
        form = CompanyEditForm(instance=company, user=user)
        # Загружаем существующие email и телефоны для отображения в форме
        company_emails = list(company.emails.all())
        company_phones = list(company.phones.all())

    return render(
        request,
        "ui/company_edit.html",
        {
            "company": company,
            "form": form,
            "company_emails": company_emails,
            "company_phones": company_phones,
            "contract_types": ContractType.objects.order_by("order", "name"),
        },
    )


@login_required
@policy_required(resource_type="action", resource="ui:companies:transfer")
@transaction.atomic
@require_can_view_company
def company_transfer(request: HttpRequest, company_id) -> HttpResponse:
    if request.method != "POST":
        return redirect("company_detail", company_id=company_id)

    user: User = request.user
    company = get_object_or_404(
        Company.objects.select_related("responsible", "branch"), id=company_id
    )

    new_resp_id = (request.POST.get("responsible_id") or "").strip()
    if not new_resp_id:
        messages.error(request, "Выберите ответственного.")
        return redirect("company_detail", company_id=company.id)

    new_resp = get_object_or_404(User, id=new_resp_id, is_active=True)

    from django.core.exceptions import PermissionDenied as DjangoPermissionDenied

    from companies.services import CompanyService

    try:
        CompanyService.transfer(company=company, user=user, new_responsible=new_resp)
    except DjangoPermissionDenied:
        messages.error(request, "Нет прав на передачу компании.")
        return redirect("company_detail", company_id=company.id)
    except ValidationError as exc:
        messages.error(request, exc.message if hasattr(exc, "message") else str(exc))
        return redirect("company_detail", company_id=company.id)

    messages.success(request, f"Ответственный обновлён: {new_resp}.")
    return redirect("company_detail", company_id=company.id)


@login_required
@policy_required(resource_type="action", resource="ui:companies:update")
@require_can_view_company
def company_update(request: HttpRequest, company_id) -> HttpResponse:
    if request.method != "POST":
        return redirect("company_detail", company_id=company_id)

    user: User = request.user
    company = get_object_or_404(
        Company.objects.select_related("responsible", "branch"), id=company_id
    )
    if not _can_edit_company(user, company):
        messages.error(
            request,
            "Редактирование доступно только создателю/ответственному/директору филиала/управляющему.",
        )
        return redirect("company_detail", company_id=company.id)

    form = CompanyQuickEditForm(request.POST, instance=company)
    if form.is_valid():
        form.save()
        _invalidate_company_count_cache()  # Инвалидируем кэш при обновлении (на случай изменения статуса/филиала)
        messages.success(request, "Карточка компании обновлена.")
        log_event(
            actor=user,
            verb=ActivityEvent.Verb.UPDATE,
            entity_type="company",
            entity_id=company.id,
            company_id=company.id,
            message="Изменены статус/сферы компании",
        )
    else:
        messages.error(request, "Не удалось обновить компанию. Проверь поля.")
    return redirect("company_detail", company_id=company.id)


@login_required
@policy_required(resource_type="action", resource="ui:companies:update")
@require_can_view_company
def company_inline_update(request: HttpRequest, company_id) -> HttpResponse:
    """
    Инлайн-обновление одного поля компании (AJAX) из карточки компании.
    Вход: POST {field, value}. Выход: JSON.
    """
    if request.method != "POST":
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"ok": False, "error": "Метод не разрешен."}, status=405)
        return redirect("company_detail", company_id=company_id)

    user: User = request.user
    company = get_object_or_404(
        Company.objects.select_related("responsible", "branch", "region", "contract_type"),
        id=company_id,
    )
    if not _can_edit_company(user, company):
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse(
                {"ok": False, "error": "Нет прав на редактирование этой компании."}, status=403
            )
        messages.error(request, "Нет прав на редактирование этой компании.")
        return redirect("company_detail", company_id=company.id)

    field = (request.POST.get("field") or "").strip()
    if field not in CompanyInlineEditForm.ALLOWED_FIELDS:
        return JsonResponse({"ok": False, "error": "Недопустимое поле."}, status=400)

    value = request.POST.get("value")
    data = {field: value}
    form = CompanyInlineEditForm(data=data, instance=company, field=field)
    if not form.is_valid():
        return JsonResponse(
            {"ok": False, "errors": form.errors, "error": "Проверь значение поля."}, status=400
        )

    form.save()

    # Для внешних ключей и спец-полей приводим значение к строке для JSON.
    if field == "region":
        updated_value = company.region.name if company.region else ""
    elif field == "employees_count":
        v = getattr(company, field, None)
        updated_value = str(v) if v is not None else ""
    elif field == "contract_amount":
        # Для суммы форматируем как число с двумя знаками после запятой
        amount = getattr(company, field, None)
        if amount is not None:
            updated_value = f"{float(amount):.2f}"
        else:
            updated_value = ""
    else:
        updated_value = getattr(company, field, "")

    log_event(
        actor=user,
        verb=ActivityEvent.Verb.UPDATE,
        entity_type="company",
        entity_id=company.id,
        company_id=company.id,
        message=f"Инлайн-обновление поля компании: {field}",
        meta={"field": field},
    )

    # Для часового пояса отдаём доп. данные, чтобы можно было обновить UI без перезагрузки
    if field == "work_timezone":
        try:
            from zoneinfo import ZoneInfo

            from core.timezone_utils import RUS_TZ_CHOICES, guess_ru_timezone_from_address

            guessed = guess_ru_timezone_from_address(company.address or "")
            effective_tz = (
                ((company.work_timezone or "").strip()) or guessed or "Europe/Moscow"
            ).strip()
            label_map = {tz: lbl for tz, lbl in (RUS_TZ_CHOICES or [])}
            effective_label = label_map.get(effective_tz, effective_tz)
            now_hhmm = timezone.now().astimezone(ZoneInfo(effective_tz)).strftime("%H:%M")
        except Exception:
            effective_tz = (company.work_timezone or "").strip() or ""
            effective_label = effective_tz
            now_hhmm = ""

        return JsonResponse(
            {
                "ok": True,
                "field": field,
                # value = сохранённое значение (может быть пустым = авто)
                "value": (updated_value or ""),
                "effective_tz": effective_tz,
                "effective_label": effective_label,
                "effective_now_hhmm": now_hhmm,
            }
        )

    return JsonResponse({"ok": True, "field": field, "value": updated_value})
