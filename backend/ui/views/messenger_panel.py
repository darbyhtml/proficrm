"""
Views для операторской панели мессенджера (Chatwoot-style).

Роли и доступ:
- Все авторизованные пользователи могут *просматривать* диалоги
- Только менеджеры (role=manager) могут отвечать клиентам и быть назначены ответственными
- Просмотр не-менеджерами НЕ считается прочтением (assignee_last_read_at не обновляется)
"""
from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.contrib import messages as django_messages
from django.core.paginator import Paginator
from django.db.models import Count, OuterRef, Subquery, Q, F
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse

from accounts.models import Branch, User
from companies.models import Region
from companies.permissions import get_users_for_lists
from crm.utils import get_effective_user
from messenger.models import AgentProfile, Conversation, Message
from messenger.selectors import visible_conversations_qs
from messenger.utils import ensure_messenger_enabled_view
from policy.decorators import policy_required


def _safe_redirect_url(request, url, fallback="/"):
    """Безопасный редирект — только относительные URL того же хоста."""
    if url and isinstance(url, str) and url.startswith("/") and not url.startswith("//"):
        return url
    return fallback


@login_required
@policy_required(resource_type="page", resource="ui:messenger:conversations:list")
def messenger_conversations_unified(request: HttpRequest) -> HttpResponse:
    """
    Unified страница мессенджера в стиле Chatwoot: три колонки на одной странице.

    Левая колонка: список диалогов (компактные карточки)
    Центральная колонка: выбранный диалог или пустое состояние
    Правая колонка: информация о диалоге/контакте
    """
    ensure_messenger_enabled_view()

    user: User = get_effective_user(request)

    # Определяем, может ли пользователь отвечать (только менеджеры)
    can_reply = user.role == User.Role.MANAGER

    # Базовую видимость берём из domain policy слоя
    qs = visible_conversations_qs(user).select_related(
        "inbox", "contact", "assignee", "branch", "region"
    )

    # Аннотация: превью последнего сообщения
    last_message = (
        Message.objects.filter(conversation=OuterRef("pk"))
        .order_by("-created_at", "-id")
        .values("body")[:1]
    )
    qs = qs.annotate(last_message_body=Subquery(last_message))

    # Не показываем диалоги без единого сообщения
    qs = qs.filter(last_message_body__isnull=False)

    # Счётчик непрочитанных (только для назначенных диалогов текущему пользователю)
    if user.id:
        qs = qs.annotate(
            unread_count=Count(
                "messages__id",
                filter=Q(
                    messages__direction=Message.Direction.IN,
                    assignee_id=user.id,
                )
                & (
                    Q(assignee_last_read_at__isnull=True)
                    | Q(messages__created_at__gt=F("assignee_last_read_at"))
                ),
                distinct=True,
            )
        )
    else:
        qs = qs.annotate(unread_count=Count("id", filter=Q(id__isnull=True)))

    # --- Фильтры ---
    q = (request.GET.get("q") or "").strip()
    if q:
        q_digits = "".join([c for c in q if c.isdigit()])
        q_obj = (
            Q(contact__name__icontains=q)
            | Q(contact__email__icontains=q)
            | Q(contact__phone__icontains=q)
        )
        if q_digits:
            try:
                q_obj = q_obj | Q(id=int(q_digits))
            except (ValueError, TypeError):
                pass
        qs = qs.filter(q_obj)

    status_filter = (request.GET.get("status") or "").strip()
    if status_filter:
        if "," in status_filter:
            statuses = [s.strip() for s in status_filter.split(",")]
            qs = qs.filter(status__in=statuses)
        else:
            qs = qs.filter(status=status_filter)

    branch_id = request.GET.get("branch")
    if branch_id:
        try:
            qs = qs.filter(branch_id=int(branch_id))
        except (ValueError, TypeError):
            pass

    assignee_id = request.GET.get("assignee")
    mine = request.GET.get("mine", "").strip().lower() in ("1", "true", "yes")
    if mine:
        qs = qs.filter(assignee_id=user.id)
        if assignee_id is None:
            assignee_id = str(user.id)
    elif assignee_id:
        try:
            qs = qs.filter(assignee_id=int(assignee_id))
        except (ValueError, TypeError):
            pass

    region_id = request.GET.get("region")
    if region_id:
        try:
            qs = qs.filter(region_id=int(region_id))
        except (ValueError, TypeError):
            pass

    # --- Сортировка ---
    sort_raw = (request.GET.get("sort") or "").strip()
    sort = sort_raw or "last_activity_at"
    direction = (request.GET.get("dir") or "").strip().lower() or "desc"
    direction = "asc" if direction == "asc" else "desc"

    sort_map = {
        "last_message_at": "last_activity_at",
        "last_activity_at": "last_activity_at",
        "created_at": "created_at",
        "status": "status",
        "priority": "priority",
    }
    sort_field = sort_map.get(sort, "last_activity_at")
    order = [sort_field, "id"]
    if direction == "desc":
        order = [f"-{f}" for f in order]
    qs = qs.order_by(*order)

    # Пагинация
    per_page = 50
    paginator = Paginator(qs, per_page)
    page = paginator.get_page(request.GET.get("page"))

    # Справочники для фильтров
    branches = Branch.objects.order_by("name")
    regions = Region.objects.order_by("name")
    assignees_qs = get_users_for_lists(user)
    if user.branch_id and user.role != User.Role.ADMIN:
        assignees_qs = assignees_qs.filter(branch_id=user.branch_id)
    assignees = list(assignees_qs)

    # URL для кнопки "Мои"
    get_mine = request.GET.copy()
    get_mine["mine"] = "1"
    url_mine = get_mine.urlencode()

    # Выбранный диалог (из параметра)
    selected_conversation_id = request.GET.get("conversation")
    selected_conversation = None
    if selected_conversation_id:
        try:
            from django.shortcuts import get_object_or_404

            selected_conversation = get_object_or_404(qs, id=int(selected_conversation_id))
        except (ValueError, TypeError):
            pass

    # Статус оператора
    try:
        agent_profile = AgentProfile.objects.get(user=user)
        agent_status = agent_profile.status
    except AgentProfile.DoesNotExist:
        agent_status = "offline"

    return render(
        request,
        "ui/messenger_conversations_unified.html",
        {
            "page": page,
            "q": q,
            "status_filter": status_filter,
            "branch_id": branch_id,
            "assignee_id": assignee_id,
            "region_id": region_id,
            "sort": sort,
            "dir": direction,
            "branches": branches,
            "regions": regions,
            "assignees": assignees,
            "per_page": per_page,
            "mine": mine,
            "url_mine": url_mine,
            "selected_conversation": selected_conversation,
            "agent_status": agent_status,
            "current_user_id": user.id,
            "can_reply": can_reply,
        },
    )


@login_required
def messenger_agent_status(request: HttpRequest) -> HttpResponse:
    """
    Обновление статуса оператора (online/away/busy/offline).
    POST /messenger/me/status/
    """
    ensure_messenger_enabled_view()

    if request.method != "POST":
        return redirect("messenger_conversations_unified")

    user: User = get_effective_user(request)
    status = (request.POST.get("status") or "").strip()
    allowed = {s.value for s in AgentProfile.Status}
    if status not in allowed:
        django_messages.error(request, "Неверный статус оператора.")
        next_url = _safe_redirect_url(
            request,
            request.POST.get("next"),
            fallback=reverse("messenger_conversations_unified"),
        )
        return redirect(next_url)

    profile, _ = AgentProfile.objects.get_or_create(user=user)
    profile.status = status
    profile.save(update_fields=["status", "updated_at"])

    next_url = _safe_redirect_url(
        request,
        request.POST.get("next"),
        fallback=reverse("messenger_conversations_unified"),
    )
    return redirect(next_url)
