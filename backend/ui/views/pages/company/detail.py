"""Company detail main card + tasks history + timeline AJAX (W1.2 refactor).

Extracted из `backend/ui/views/company_detail.py` в W1.2 — финальный extraction.
После этого файл `company_detail.py` удалён полностью.

Endpoints:
- `company_detail` — GET /companies/<uuid>/
- `company_tasks_history` — GET /companies/<uuid>/tasks-history/
- `company_timeline_items` — GET /companies/<uuid>/timeline/items/?offset=&limit=
"""

from __future__ import annotations

import logging

from ui.views._base import (
    ActivityEvent,
    Company,
    CompanyContractForm,
    CompanyDeal,
    CompanyDeletionRequest,
    CompanyNote,
    CompanyNoteForm,
    CompanyPhone,
    CompanyQuickEditForm,
    CompanyStatus,
    Contact,
    ContactPhone,
    HttpRequest,
    HttpResponse,
    Prefetch,
    Task,
    UiUserPreference,
    User,
    _can_delete_company,
    _can_delete_task_ui,
    _can_edit_company,
    _can_edit_task_ui,
    _can_manage_task_status_ui,
    get_object_or_404,
    get_transfer_targets,
    login_required,
    models,
    policy_required,
    redirect,
    render,
    require_admin,
    require_can_view_company,
    timezone,
)

logger = logging.getLogger(__name__)


@login_required
@policy_required(resource_type="page", resource="ui:companies:detail")
@require_can_view_company
def company_detail(request: HttpRequest, company_id) -> HttpResponse:
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
