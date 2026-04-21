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


# company_main_email_update, company_email_value_update moved в ui.views.pages.company.emails (W1.2)


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


@login_required
@policy_required(resource_type="action", resource="ui:companies:update")
@require_can_view_company
def company_note_pin_toggle(request: HttpRequest, company_id, note_id: int) -> HttpResponse:
    if request.method != "POST":
        return redirect("company_detail", company_id=company_id)

    user: User = request.user
    company = get_object_or_404(
        Company.objects.select_related("responsible", "branch"), id=company_id
    )
    if not _can_edit_company(user, company):
        messages.error(request, "Нет прав на закрепление заметок по этой компании.")
        return redirect("company_detail", company_id=company.id)

    note = get_object_or_404(
        CompanyNote.objects.select_related("company"), id=note_id, company_id=company.id
    )
    now = timezone.now()

    if note.is_pinned:
        note.is_pinned = False
        note.pinned_at = None
        note.pinned_by = None
        note.save(update_fields=["is_pinned", "pinned_at", "pinned_by"])
        messages.success(request, "Заметка откреплена.")
        log_event(
            actor=user,
            verb=ActivityEvent.Verb.UPDATE,
            entity_type="note",
            entity_id=str(note.id),
            company_id=company.id,
            message="Откреплена заметка",
        )
        return redirect("company_detail", company_id=company.id)

    # Закрепляем: снимаем закрепление с других заметок (одна закреплённая на компанию)
    CompanyNote.objects.filter(company=company, is_pinned=True).exclude(id=note.id).update(
        is_pinned=False, pinned_at=None, pinned_by=None
    )
    note.is_pinned = True
    note.pinned_at = now
    note.pinned_by = user
    note.save(update_fields=["is_pinned", "pinned_at", "pinned_by"])

    messages.success(request, "Заметка закреплена.")
    log_event(
        actor=user,
        verb=ActivityEvent.Verb.UPDATE,
        entity_type="note",
        entity_id=str(note.id),
        company_id=company.id,
        message="Закреплена заметка",
    )
    return redirect("company_detail", company_id=company.id)


@login_required
@policy_required(resource_type="page", resource="ui:companies:detail")
@require_can_view_note_company
def company_note_attachment_open(request: HttpRequest, company_id, note_id: int) -> HttpResponse:
    """
    Открыть вложение заметки в новом окне (inline). Доступ: всем пользователям (как просмотр компании).
    """
    company = get_object_or_404(Company.objects.all(), id=company_id)
    note = get_object_or_404(
        CompanyNote.objects.select_related("company"), id=note_id, company_id=company.id
    )
    if not note.attachment:
        raise Http404("Файл не найден")
    ctype = (note.attachment_content_type or "").strip()
    if not ctype:
        ctype = (
            mimetypes.guess_type(note.attachment_name or note.attachment.name)[0]
            or "application/octet-stream"
        )
    try:
        return FileResponse(
            open(note.attachment.path, "rb"),
            as_attachment=False,
            filename=(note.attachment_name or "file"),
            content_type=ctype,
        )
    except FileNotFoundError:
        return HttpResponseNotFound("Файл вложения не найден.")


@login_required
@policy_required(resource_type="page", resource="ui:companies:detail")
@require_can_view_note_company
def company_note_attachment_by_id_open(
    request: HttpRequest, company_id, note_id: int, attachment_id: int
) -> HttpResponse:
    """Открыть одно из вложений заметки (CompanyNoteAttachment) по id."""
    company = get_object_or_404(Company.objects.all(), id=company_id)
    note = get_object_or_404(
        CompanyNote.objects.select_related("company"), id=note_id, company_id=company.id
    )
    att = get_object_or_404(CompanyNoteAttachment.objects.filter(note=note), id=attachment_id)
    if not att.file:
        raise Http404("Файл не найден")
    ctype = (
        (att.content_type or "").strip()
        or mimetypes.guess_type(att.file_name or att.file.name)[0]
        or "application/octet-stream"
    )
    try:
        return FileResponse(
            open(att.file.path, "rb"),
            as_attachment=False,
            filename=(att.file_name or "file"),
            content_type=ctype,
        )
    except FileNotFoundError:
        return HttpResponseNotFound("Файл вложения не найден.")


@login_required
@policy_required(resource_type="page", resource="ui:companies:detail")
@require_can_view_note_company
def company_note_attachment_by_id_download(
    request: HttpRequest, company_id, note_id: int, attachment_id: int
) -> HttpResponse:
    """Скачать одно из вложений заметки (CompanyNoteAttachment) по id."""
    company = get_object_or_404(Company.objects.all(), id=company_id)
    note = get_object_or_404(
        CompanyNote.objects.select_related("company"), id=note_id, company_id=company.id
    )
    att = get_object_or_404(CompanyNoteAttachment.objects.filter(note=note), id=attachment_id)
    if not att.file:
        raise Http404("Файл не найден")
    ctype = (
        (att.content_type or "").strip()
        or mimetypes.guess_type(att.file_name or att.file.name)[0]
        or "application/octet-stream"
    )
    try:
        return FileResponse(
            open(att.file.path, "rb"),
            as_attachment=True,
            filename=(att.file_name or "file"),
            content_type=ctype,
        )
    except FileNotFoundError:
        return HttpResponseNotFound("Файл вложения не найден.")


@login_required
@policy_required(resource_type="page", resource="ui:companies:detail")
@require_can_view_note_company
def company_note_attachment_download(
    request: HttpRequest, company_id, note_id: int
) -> HttpResponse:
    """
    Скачать вложение заметки (attachment). Доступ: всем пользователям (как просмотр компании).
    """
    company = get_object_or_404(Company.objects.all(), id=company_id)
    note = get_object_or_404(
        CompanyNote.objects.select_related("company"), id=note_id, company_id=company.id
    )
    if not note.attachment:
        raise Http404("Файл не найден")
    ctype = (note.attachment_content_type or "").strip()
    if not ctype:
        ctype = (
            mimetypes.guess_type(note.attachment_name or note.attachment.name)[0]
            or "application/octet-stream"
        )
    try:
        return FileResponse(
            open(note.attachment.path, "rb"),
            as_attachment=True,
            filename=(note.attachment_name or "file"),
            content_type=ctype,
        )
    except FileNotFoundError:
        return HttpResponseNotFound("Файл вложения не найден.")


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


# contact_create, contact_edit, contact_delete moved в ui.views.pages.company.contacts (W1.2)


@login_required
@policy_required(resource_type="action", resource="ui:companies:update")
@require_can_view_company
def company_note_add(request: HttpRequest, company_id) -> HttpResponse:
    if request.method != "POST":
        return redirect("company_detail", company_id=company_id)

    from companies.services import CompanyService

    user: User = request.user
    company = get_object_or_404(
        Company.objects.select_related("responsible", "branch"), id=company_id
    )

    # Заметки по карточке: доступно всем, кто имеет доступ к просмотру карточки (в проекте это все пользователи).
    form = CompanyNoteForm(request.POST, request.FILES)
    extra_files = request.FILES.getlist("attachments") or []
    if form.is_valid():
        note_data: CompanyNote = form.save(commit=False)
        CompanyService.add_note(
            company=company,
            user=user,
            text=note_data.text or "",
            attachment=note_data.attachment or None,
            extra_files=extra_files or None,
        )
    elif (
        extra_files
        and not (request.POST.get("text") or "").strip()
        and not request.FILES.get("attachment")
    ):
        # Только несколько файлов без текста и без одного attachment — создаём заметку вручную
        CompanyService.add_note(
            company=company,
            user=user,
            text="",
            extra_files=extra_files,
        )

    nxt = _safe_next_v3(request, company_id)
    if nxt:
        return redirect(nxt)
    return redirect("company_detail", company_id=company_id)


@login_required
@policy_required(resource_type="action", resource="ui:companies:update")
@require_can_view_company
def company_note_edit(request: HttpRequest, company_id, note_id: int) -> HttpResponse:
    if request.method != "POST":
        return redirect("company_detail", company_id=company_id)

    user: User = request.user
    company = get_object_or_404(Company.objects.all(), id=company_id)

    # Редактировать заметки:
    # - админ/суперпользователь/управляющий: любые
    # - остальные: только свои ИЛИ заметки без автора (author=None), если пользователь - ответственный за компанию
    if user.is_superuser or user.role in (User.Role.ADMIN, User.Role.GROUP_MANAGER):
        note = get_object_or_404(
            CompanyNote.objects.select_related("author"), id=note_id, company_id=company.id
        )
    else:
        # Обычные пользователи могут редактировать свои заметки или заметки без автора, если они ответственные за компанию
        note_qs = CompanyNote.objects.select_related("author").filter(
            id=note_id, company_id=company.id
        )
        if company.responsible_id == user.id:
            # Ответственный может редактировать свои заметки и заметки без автора
            note = get_object_or_404(note_qs.filter(Q(author_id=user.id) | Q(author__isnull=True)))
        else:
            # Остальные могут редактировать только свои заметки
            note = get_object_or_404(note_qs, author_id=user.id)

    text = (request.POST.get("text") or "").strip()
    remove_attachment = (request.POST.get("remove_attachment") or "").strip() == "1"
    new_file = request.FILES.get("attachment")

    old_file = note.attachment  # storage object
    old_name = getattr(old_file, "name", "") if old_file else ""

    # Если попросили удалить файл — удалим привязку (и сам файл ниже)
    if remove_attachment and note.attachment:
        note.attachment = None
        note.attachment_name = ""
        note.attachment_ext = ""
        note.attachment_size = 0
        note.attachment_content_type = ""

    # Если загрузили новый файл — заменяем
    if new_file:
        note.attachment = new_file
        try:
            note.attachment_name = (
                (getattr(new_file, "name", "") or "").split("/")[-1].split("\\")[-1]
            )
            note.attachment_ext = (
                note.attachment_name.rsplit(".", 1)[-1].lower()
                if "." in note.attachment_name
                else ""
            )[:16]
            note.attachment_size = int(getattr(new_file, "size", 0) or 0)
            note.attachment_content_type = (getattr(new_file, "content_type", "") or "").strip()[
                :120
            ]
        except Exception as e:
            logger.warning(
                f"Ошибка при извлечении метаданных нового вложения заметки: {e}",
                exc_info=True,
                extra={
                    "company_id": str(company.id),
                    "note_id": note.id if hasattr(note, "id") else None,
                },
            )

    # Доп. вложения: удалить отмеченные
    remove_ids = (request.POST.get("remove_attachment_ids") or "").strip()
    if remove_ids:
        for att_id in remove_ids.split(","):
            try:
                aid = int(att_id.strip())
                att = CompanyNoteAttachment.objects.filter(id=aid, note=note).first()
                if att:
                    try:
                        if att.file:
                            att.file.delete(save=False)
                    except Exception:
                        pass
                    att.delete()
            except ValueError:
                pass

    # Новые доп. вложения
    next_order = (note.note_attachments.aggregate(m=Max("order"))["m"] or -1) + 1
    for i, f in enumerate(request.FILES.getlist("attachments") or []):
        try:
            att = CompanyNoteAttachment(note=note, file=f, order=next_order + i)
            att.save()
        except Exception as e:
            logger.warning(
                f"Ошибка при сохранении доп. вложения заметки: {e}",
                exc_info=True,
                extra={"company_id": str(company.id), "note_id": note.id},
            )

    has_attachments = (
        note.attachment or note.note_attachments.exists() or request.FILES.getlist("attachments")
    )
    if not text and not has_attachments:
        messages.error(request, "Заметка не может быть пустой (нужен текст или файл).")
        return redirect("company_detail", company_id=company.id)

    note.text = text
    note.edited_at = timezone.now()
    note.save()

    # Удаляем старый файл из storage, если он был удалён/заменён
    try:
        new_name = getattr(note.attachment, "name", "") if note.attachment else ""
        should_delete_old = bool(
            old_file
            and old_name
            and (remove_attachment or (new_file is not None))
            and old_name != new_name
        )
        if should_delete_old:
            old_file.delete(save=False)
    except Exception as e:
        logger.warning(
            f"Ошибка при удалении старого файла вложения заметки: {e}",
            exc_info=True,
            extra={
                "company_id": str(company.id),
                "note_id": note.id if hasattr(note, "id") else None,
            },
        )

    messages.success(request, "Заметка обновлена.")
    log_event(
        actor=user,
        verb=ActivityEvent.Verb.UPDATE,
        entity_type="note",
        entity_id=str(note.id),
        company_id=company.id,
        message="Изменена заметка",
    )
    nxt = _safe_next_v3(request, company.id)
    if nxt:
        return redirect(nxt)
    return redirect("company_detail", company_id=company.id)


@login_required
@policy_required(resource_type="action", resource="ui:companies:update")
@require_can_view_company
def company_note_delete(request: HttpRequest, company_id, note_id: int) -> HttpResponse:
    if request.method != "POST":
        return redirect("company_detail", company_id=company_id)

    user: User = request.user
    company = get_object_or_404(Company.objects.all(), id=company_id)

    # Удалять заметки:
    # - админ/суперпользователь/управляющий: любые
    # - остальные: только свои ИЛИ заметки без автора (author=None), если пользователь - ответственный за компанию
    if user.is_superuser or user.role in (User.Role.ADMIN, User.Role.GROUP_MANAGER):
        note = get_object_or_404(
            CompanyNote.objects.select_related("author"), id=note_id, company_id=company.id
        )
    else:
        # Обычные пользователи могут удалять свои заметки или заметки без автора, если они ответственные за компанию
        note_qs = CompanyNote.objects.select_related("author").filter(
            id=note_id, company_id=company.id
        )
        if company.responsible_id == user.id:
            # Ответственный может удалять свои заметки и заметки без автора
            note = get_object_or_404(note_qs.filter(Q(author_id=user.id) | Q(author__isnull=True)))
        else:
            # Остальные могут удалять только свои заметки
            note = get_object_or_404(note_qs, author_id=user.id)
    # Удаляем вложенный файл из storage, затем запись
    try:
        if note.attachment:
            note.attachment.delete(save=False)
    except Exception as e:
        logger.warning(
            f"Ошибка при удалении файла вложения заметки: {e}",
            exc_info=True,
            extra={"company_id": str(company.id), "note_id": note_id},
        )
    note.delete()

    messages.success(request, "Заметка удалена.")
    log_event(
        actor=user,
        verb=ActivityEvent.Verb.DELETE,
        entity_type="note",
        entity_id=str(note_id),
        company_id=company.id,
        message="Удалена заметка",
    )
    nxt = _safe_next_v3(request, company.id)
    if nxt:
        return redirect(nxt)
    return redirect("company_detail", company_id=company.id)


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
