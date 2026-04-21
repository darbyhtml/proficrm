from __future__ import annotations

import logging

from phonebridge.models import CallRequest, PhoneDevice
from ui.views._base import (
    ActivityEvent,
    Company,
    CompanyContractForm,
    CompanyDeal,
    CompanyDeletionRequest,
    CompanyEditForm,
    CompanyEmail,
    CompanyInlineEditForm,
    CompanyNote,
    CompanyNoteAttachment,
    CompanyNoteForm,
    CompanyPhone,
    CompanyQuickEditForm,
    CompanySearchIndex,
    CompanyStatus,
    Contact,
    ContactEmailFormSet,
    ContactForm,
    ContactPhone,
    ContactPhoneFormSet,
    ContractType,
    Decimal,
    FileResponse,
    Http404,
    HttpRequest,
    HttpResponse,
    HttpResponseNotFound,
    IntegrityError,
    JsonResponse,
    Max,
    Notification,
    Prefetch,
    Q,
    Task,
    UiUserPreference,
    User,
    ValidationError,
    _can_delete_company,
    _can_delete_task_ui,
    _can_edit_company,
    _can_edit_task_ui,
    _can_manage_task_status_ui,
    _cold_call_json,
    _company_branch_id,
    _detach_client_branches,
    _invalidate_company_count_cache,
    _is_ajax,
    _notify_branch_leads,
    _notify_head_deleted_with_branches,
    _safe_next_v3,
    format_phone,
    get_object_or_404,
    get_transfer_targets,
    log_event,
    login_required,
    messages,
    mimetypes,
    models,
    notify,
    policy_required,
    redirect,
    render,
    require_admin,
    require_can_view_company,
    require_can_view_note_company,
    timedelta,
    timezone,
    transaction,
    validate_email,
)

logger = logging.getLogger(__name__)


@login_required
@policy_required(resource_type="page", resource="ui:companies:detail")
@require_can_view_company
def company_detail(request: HttpRequest, company_id) -> HttpResponse:
    logger = logging.getLogger(__name__)
    user: User = request.user
    # Загружаем компанию с связанными объектами, включая поля для истории холодных звонков
    company = get_object_or_404(
        Company.objects.select_related(
            "responsible",
            "branch",
            "status",
            "head_company",
            "contract_type",
            "primary_cold_marked_by",
            "primary_cold_marked_call",
        ).prefetch_related(
            "emails",
            Prefetch(
                "phones",
                queryset=CompanyPhone.objects.select_related(
                    "cold_marked_by", "cold_marked_call"
                ).order_by("order", "value"),
            ),
        ),
        id=company_id,
    )
    can_edit_company = _can_edit_company(user, company)
    can_view_activity = bool(
        user.is_superuser
        or user.role
        in (
            User.Role.ADMIN,
            User.Role.GROUP_MANAGER,
            User.Role.BRANCH_DIRECTOR,
            User.Role.SALES_HEAD,
        )
    )
    can_delete_company = _can_delete_company(user, company)
    can_request_delete = bool(user.role == User.Role.MANAGER and company.responsible_id == user.id)
    delete_req = (
        CompanyDeletionRequest.objects.filter(
            company=company, status=CompanyDeletionRequest.Status.PENDING
        )
        .select_related("requested_by", "decided_by")
        .order_by("-created_at")
        .first()
    )

    # "Организация" (головная карточка) и "филиалы" (дочерние карточки клиента)
    head = company.head_company or company
    org_head = Company.objects.select_related("responsible", "branch").filter(id=head.id).first()
    org_branches = (
        Company.objects.select_related("responsible", "branch")
        .filter(head_company_id=head.id)
        .order_by("name")[:200]
    )

    # Загружаем контакты с связанными объектами для истории холодных звонков
    contacts = (
        Contact.objects.filter(company=company)
        .select_related("cold_marked_by", "cold_marked_call")
        .prefetch_related(
            "emails",
            Prefetch(
                "phones",
                queryset=ContactPhone.objects.select_related("cold_marked_by", "cold_marked_call"),
            ),
        )
        .order_by("last_name", "first_name")[:200]
    )

    # Новая логика: любой звонок может быть холодным, без ограничений по времени
    # Кнопка доступна только если контакт еще не отмечен как холодный
    for c in contacts:
        # Кнопка доступна только если контакт еще не отмечен
        c.cold_mark_available = not c.is_cold_call  # type: ignore[attr-defined]

    # Основной контакт (company.phone)
    # Кнопка доступна только если основной контакт еще не отмечен
    primary_cold_available = not company.primary_contact_is_cold_call

    # Проверка прав администратора для отката
    is_admin = require_admin(user)
    is_group_manager = user.role == User.Role.GROUP_MANAGER
    pinned_note = (
        CompanyNote.objects.filter(
            company=company, is_pinned=True, note_type=CompanyNote.NoteType.NOTE
        )
        .select_related("author", "pinned_by")
        .prefetch_related("note_attachments")
        .order_by("-pinned_at", "-created_at")
        .first()
    )
    notes = (
        CompanyNote.objects.filter(company=company, note_type=CompanyNote.NoteType.NOTE)
        .select_related("author", "pinned_by")
        .prefetch_related("note_attachments")
        .order_by("-is_pinned", "-pinned_at", "-created_at")[:60]
    )
    deals = (
        CompanyDeal.objects.filter(company=company)
        .select_related("created_by")
        .order_by("-created_at")[:50]
    )
    # Сортируем задачи: сначала просроченные (по дедлайну, старые сначала), потом по дедлайну (ближайшие сначала), потом по дате создания (новые сначала)
    # Исключаем выполненные задачи из списка "Последние задачи"
    now = timezone.now()
    local_now = timezone.localtime(now)
    # Индикатор: можно ли звонить (рабочее время компании + часовой пояс)
    from companies.services import get_worktime_status

    worktime = get_worktime_status(company)

    # ROLE: Тендерист не работает с задачами — только заметки.
    # Раньше задачи по компании ему показывались в карточке (баг из аудита).
    if user.role == User.Role.TENDERIST:
        tasks = Task.objects.none()
    else:
        tasks = (
            Task.objects.filter(company=company)
            .exclude(status=Task.Status.DONE)  # Исключаем выполненные задачи
            .select_related("assigned_to", "type", "created_by")
            .annotate(
                is_overdue=models.Case(
                    models.When(
                        models.Q(due_at__lt=now)
                        & ~models.Q(status__in=[Task.Status.DONE, Task.Status.CANCELLED]),
                        then=models.Value(1),
                    ),
                    default=models.Value(0),
                    output_field=models.IntegerField(),
                )
            )
            .order_by("-is_overdue", "due_at", "-created_at")[:25]
        )
    for t in tasks:
        t.can_manage_status = _can_manage_task_status_ui(user, t)  # type: ignore[attr-defined]
        t.can_edit_task = _can_edit_task_ui(user, t)  # type: ignore[attr-defined]
        t.can_delete_task = _can_delete_task_ui(user, t)  # type: ignore[attr-defined]

    note_form = CompanyNoteForm()
    activity = []
    if can_view_activity:
        activity = ActivityEvent.objects.filter(company_id=company.id).select_related("actor")[:50]

    history_events = list(
        company.history_events.select_related("actor", "from_user", "to_user").order_by(
            "occurred_at"
        )[:50]
    )

    # ===== ПОЛНЫЙ ТАЙМЛАЙН (2026-04-20 Refactor phase 1): вынесено в service =====
    # Эта 50-строчная сборка из 7 источников была продублирована в
    # `company_timeline_items`. Теперь обе функции используют `build_company_timeline()`.
    from companies.services.timeline import build_company_timeline

    _all_timeline = build_company_timeline(company=company)
    # F4 R2 (2026-04-18): пагинация timeline — первые 50, остальное по AJAX.
    # Без пагинации на компаниях с длинной историей (~4600 items) страница
    # раздувалась до >2 МБ HTML и тормозила первый paint.
    TIMELINE_INITIAL = 50
    timeline_total_count = len(_all_timeline)
    timeline_items = _all_timeline[:TIMELINE_INITIAL]
    timeline_has_more = timeline_total_count > TIMELINE_INITIAL
    # Сохраняем полный список в request.session для AJAX-подгрузки.
    # (альтернатива — повторный запрос БД, но это дороже при 4600 items)
    # Пока же endpoint сам пересчитывает — session не раздуваем.

    quick_form = CompanyQuickEditForm(instance=company)
    contract_form = CompanyContractForm(instance=company)

    transfer_targets = get_transfer_targets(user)

    # Подсветка договора: используем настройки из ContractType
    from companies.services import get_contract_alert

    contract_alert, contract_days_left = get_contract_alert(company)

    # Принудительно загружаем телефоны, чтобы убедиться, что prefetch работает.
    # Это гарантирует, что телефоны будут доступны в шаблоне.
    # SECURITY: ранее здесь были logger.info/warning с UUID компании и количеством
    # телефонов — это попадало в production-логи как INFO/WARNING и создавало
    # PII-утечку для audit системы. Убрано. Если нужно диагностировать —
    # установить LEVEL=DEBUG в settings для этого логгера.
    company_phones_list = list(company.phones.all())

    # Получаем режим просмотра карточки: из GET параметра, session или preferences (по умолчанию classic)
    detail_view_mode = request.GET.get("view", "").strip().lower()
    if detail_view_mode not in ["classic", "modern"]:
        detail_view_mode = request.session.get("company_detail_view_mode")
        if not detail_view_mode:
            prefs = UiUserPreference.load_for_user(user)
            detail_view_mode = prefs.company_detail_view_mode or "classic"
            request.session["company_detail_view_mode"] = detail_view_mode

    # Подготовка данных для modern layout (pinned/latest note, ближайшие задачи)
    display_note = pinned_note
    if not display_note and notes:
        display_note = notes[0]  # Первая заметка из отсортированного списка (latest)

    # Дополнительный список заметок для превью на вкладке "Обзор" (без дублирования display_note)
    notes_overview_preview: list[CompanyNote] = []
    if notes:
        if display_note:
            notes_overview_preview = [n for n in notes if n.id != display_note.id][:3]
        else:
            notes_overview_preview = list(notes[:3])

    # Ближайшие задачи для modern layout (2-3 по дедлайну, исключая выполненные)
    upcoming_tasks = list(tasks[:3])  # Уже отсортированы: просроченные -> ближайшие

    from companies.models import ContractType

    contract_types_list = ContractType.objects.all().order_by("order", "name")
    # Сумма договора для input в модалке — всегда с точкой (для type="number")
    contract_amount_value = ""
    if getattr(company, "contract_amount", None) is not None:
        try:
            contract_amount_value = f"{float(company.contract_amount):.2f}"
        except (TypeError, ValueError):
            pass

    return render(
        request,
        "ui/company_detail.html",
        {
            "company": company,
            "contract_amount_value": contract_amount_value,
            "org_head": org_head,
            "org_branches": org_branches,
            "can_edit_company": can_edit_company,
            "contacts": contacts,
            "primary_cold_available": primary_cold_available,
            "is_admin": is_admin,
            "is_group_manager": is_group_manager,
            "notes": notes,
            "deals": deals,
            "pinned_note": pinned_note,
            "note_form": note_form,
            "tasks": tasks,
            "local_now": local_now,  # Для корректного сравнения дат в шаблоне
            "worktime": worktime,
            "activity": activity,
            "can_view_activity": can_view_activity,
            "can_delete_company": can_delete_company,
            "can_request_delete": can_request_delete,
            "delete_req": delete_req,
            "quick_form": quick_form,
            "contract_form": contract_form,
            "contract_types": contract_types_list,  # Для JavaScript определения годовых договоров
            "transfer_targets": transfer_targets,
            "contract_alert": contract_alert,
            "contract_days_left": contract_days_left,
            "company_phones_list": company_phones_list,  # Явно передаем список телефонов для отладки
            "detail_view_mode": detail_view_mode,
            "display_note": display_note,  # Для modern layout: pinned или latest
            "upcoming_tasks": upcoming_tasks,  # Ближайшие 2-3 задачи для modern layout
            "notes_overview_preview": notes_overview_preview,  # Заметки для превью на вкладке «Обзор» (без текущей display_note)
            "statuses": CompanyStatus.objects.order_by(
                "name"
            ),  # Для быстрого изменения статуса в Modern
            "contacts_rest": list(contacts)[
                5:
            ],  # Контакты с 6-го для кнопки «Показать всех» в Modern
            "history_events": history_events,  # История передвижений карточки
            "timeline_items": timeline_items,  # Единая лента: звонки + письма + передвижения
        },
    )


@login_required
@policy_required(resource_type="page", resource="ui:companies:detail")
@require_can_view_company
def company_tasks_history(request: HttpRequest, company_id) -> HttpResponse:
    """
    История выполненных задач по компании (для модального окна в карточке компании).
    Показываем только задачи со статусом DONE, отсортированные от новых к старым.
    """
    user: User = request.user  # зарезервировано на будущее (фильтрация прав)
    company = get_object_or_404(Company, id=company_id)

    tasks = (
        Task.objects.filter(company=company, status=Task.Status.DONE)
        .select_related("assigned_to", "type", "created_by")
        .order_by("-created_at")[:100]
    )

    local_now = timezone.localtime(timezone.now())

    return render(
        request,
        "ui/partials/company_tasks_history.html",
        {
            "company": company,
            "tasks": tasks,
            "local_now": local_now,
        },
    )


# company_delete_request_create, _cancel, _approve, company_delete_direct moved в ui.views.pages.company.deletion (W1.2)


# company_contract_update moved в ui.views.pages.company.edit (W1.2)


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
    import logging

    logger = logging.getLogger(__name__)
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
    import logging

    logger = logging.getLogger(__name__)
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


# 4 phone CRUD + 3 phone/email comment fns moved в ui.views.pages.company.phones (W1.2)
# (was between emails-stub and notes_pin_toggle)


# company_note_pin_toggle + 4 attachments fns moved в ui.views.pages.company.notes (W1.2)


# company_edit, company_transfer, company_update, company_inline_update moved в ui.views.pages.company.edit (W1.2)


# contact_create, contact_edit, contact_delete moved в ui.views.pages.company.contacts (W1.2)


# company_note_add, company_note_edit, company_note_delete moved в ui.views.pages.company.notes (W1.2)


# company_deal_add, company_deal_delete moved в ui.views.pages.company.deals (W1.2)


# phone_call_create moved в ui.views.pages.company.calls (W1.2)


@login_required
@require_can_view_company
def company_timeline_items(request: HttpRequest, company_id) -> HttpResponse:
    """AJAX-подгрузка timeline-событий (F4 R2 2026-04-18).

    GET /companies/<company_id>/timeline/items/?offset=50&limit=50 →
    HTML-фрагмент с <li> элементами из _company_timeline_items.html.
    Используется кнопкой «Показать ещё» на карточке компании.

    2026-04-20 Refactor phase 1: сборка timeline вынесена в
    `companies.services.timeline.build_company_timeline()` — раньше код
    был продублирован с `company_detail` view (~50 строк одинаковой логики).
    """
    try:
        offset = max(0, int(request.GET.get("offset", 50)))
    except (TypeError, ValueError):
        offset = 50
    try:
        limit = max(1, min(100, int(request.GET.get("limit", 50))))
    except (TypeError, ValueError):
        limit = 50

    company = get_object_or_404(Company, id=company_id)

    from companies.services.timeline import build_company_timeline

    all_items = build_company_timeline(company=company)
    items_slice = all_items[offset : offset + limit]
    has_more = len(all_items) > offset + limit
    return render(
        request,
        "ui/_partials/_company_timeline_items.html",
        {
            "items": items_slice,
            "has_more": has_more,
            "next_offset": offset + limit,
            "total": len(all_items),
        },
    )
