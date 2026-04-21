"""Company deal CRUD (W1.2 refactor).

Extracted из `backend/ui/views/company_detail.py` в W1.2. Zero behavior change.

Endpoints:
- `company_deal_add` — POST /companies/<uuid>/deals/add/
- `company_deal_delete` — POST /companies/<uuid>/deals/<id>/delete/
"""

from __future__ import annotations

import logging

from ui.views._base import (
    ActivityEvent,
    Company,
    CompanyDeal,
    Decimal,
    HttpRequest,
    HttpResponse,
    User,
    _can_edit_company,
    _safe_next_v3,
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
def company_deal_add(request: HttpRequest, company_id) -> HttpResponse:
    if request.method != "POST":
        return redirect("company_detail", company_id=company_id)

    user: User = request.user
    company = get_object_or_404(
        Company.objects.select_related("responsible", "branch"), id=company_id
    )
    if not _can_edit_company(user, company):
        messages.error(request, "Нет прав на добавление сделок по этой компании.")
        return redirect("company_detail", company_id=company.id)

    program = (request.POST.get("program") or "").strip()[:1000]

    price_raw = (request.POST.get("price_per_person") or "").strip()
    price_per_person = None
    if price_raw:
        try:
            price_per_person = Decimal(price_raw.replace(",", "."))
            if price_per_person < 0:
                price_per_person = None
        except Exception:
            price_per_person = None

    listeners_raw = (request.POST.get("listeners_count") or "").strip()
    listeners_count = None
    if listeners_raw:
        try:
            listeners_count = int(listeners_raw)
            if listeners_count < 0:
                listeners_count = None
        except Exception:
            listeners_count = None

    deal = CompanyDeal.objects.create(
        company=company,
        created_by=user,
        program=program,
        price_per_person=price_per_person,
        listeners_count=listeners_count,
    )

    messages.success(request, "Сделка добавлена.")
    log_event(
        actor=user,
        verb=ActivityEvent.Verb.CREATE,
        entity_type="deal",
        entity_id=str(deal.id),
        company_id=company.id,
        message="Добавлена сделка",
    )
    nxt = _safe_next_v3(request, company.id)
    if nxt:
        return redirect(nxt)
    return redirect("company_detail", company_id=company.id)


@login_required
@policy_required(resource_type="action", resource="ui:companies:update")
@require_can_view_company
def company_deal_delete(request: HttpRequest, company_id, deal_id: int) -> HttpResponse:
    if request.method != "POST":
        return redirect("company_detail", company_id=company_id)

    user: User = request.user
    company = get_object_or_404(
        Company.objects.select_related("responsible", "branch"), id=company_id
    )
    if not _can_edit_company(user, company):
        messages.error(request, "Нет прав на удаление сделок по этой компании.")
        return redirect("company_detail", company_id=company.id)

    deal = get_object_or_404(
        CompanyDeal.objects.select_related("company"), id=deal_id, company_id=company.id
    )
    deal.delete()

    messages.success(request, "Сделка удалена.")
    log_event(
        actor=user,
        verb=ActivityEvent.Verb.DELETE,
        entity_type="deal",
        entity_id=str(deal_id),
        company_id=company.id,
        message="Удалена сделка",
    )
    nxt = _safe_next_v3(request, company.id)
    if nxt:
        return redirect(nxt)
    return redirect("company_detail", company_id=company.id)
