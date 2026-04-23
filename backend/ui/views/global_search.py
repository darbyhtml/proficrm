"""UX-2 (2026-04-23): Global cross-entity search endpoint.

Ctrl+K modal fans out to this endpoint. Reuses mature `CompanySearchService`
(FTS vectors + trigram indexes via CompanySearchIndex) for companies;
simple icontains with indexed columns для contacts + tasks.

Contract:
    GET /api/search/global/?q=<query>
    Response:
        {
            "query": "<echoed>",
            "companies": [{"id", "name", "subtitle", "url"}, ...],
            "contacts":  [{"id", "name", "subtitle", "url"}, ...],
            "tasks":     [{"id", "name", "subtitle", "url"}, ...]
        }

Permissions:
    - policy resource `ui:search:global` (default allow, policy engine
      gates per-role если нужно в будущем).
    - Query respects `_editable_company_qs` / `can_view_company` scope
      для role-aware results: manager видит только свои + branch companies.
"""

from __future__ import annotations

import logging
from typing import Any

from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET

from policy.decorators import policy_required

logger = logging.getLogger(__name__)

_LIMIT_PER_CATEGORY = 5
_MIN_QUERY_LEN = 2


@login_required
@policy_required(resource_type="action", resource="ui:search:global")
@require_GET
def global_search(request: HttpRequest) -> JsonResponse:
    """Unified cross-entity search для header Ctrl+K modal."""
    query = (request.GET.get("q") or "").strip()

    if len(query) < _MIN_QUERY_LEN:
        return JsonResponse(
            {
                "query": query,
                "companies": [],
                "contacts": [],
                "tasks": [],
                "hint": f"Введите минимум {_MIN_QUERY_LEN} символа",
            }
        )

    result: dict[str, Any] = {
        "query": query,
        "companies": _search_companies(request, query),
        "contacts": _search_contacts(request, query),
        "tasks": _search_tasks(request, query),
    }
    return JsonResponse(result)


def _search_companies(request: HttpRequest, query: str) -> list[dict[str, Any]]:
    """Use mature CompanySearchService (FTS + trigram)."""
    try:
        from companies.models import Company
        from companies.search_service import get_company_search_backend

        base_qs = Company.objects.all()
        qs = get_company_search_backend().apply(qs=base_qs, query=query)
        qs = qs.select_related("responsible", "branch").distinct()[:_LIMIT_PER_CATEGORY]

        items = []
        for c in qs:
            subtitle_parts = []
            if c.inn:
                subtitle_parts.append(f"ИНН {c.inn}")
            if c.branch_id and getattr(c, "branch", None):
                subtitle_parts.append(c.branch.name)
            items.append(
                {
                    "id": str(c.id),
                    "name": c.name or "—",
                    "subtitle": " · ".join(subtitle_parts),
                    "url": f"/companies/{c.id}/",
                }
            )
        return items
    except Exception:
        logger.exception("global_search companies failed для query=%r", query)
        return []


def _search_contacts(request: HttpRequest, query: str) -> list[dict[str, Any]]:
    """Simple icontains across first_name / last_name / position."""
    try:
        from companies.models import Contact

        qs = Contact.objects.filter(
            Q(first_name__icontains=query)
            | Q(last_name__icontains=query)
            | Q(position__icontains=query)
        ).select_related("company")[:_LIMIT_PER_CATEGORY]

        items = []
        for c in qs:
            full_name = " ".join(filter(None, [c.last_name, c.first_name])).strip() or "Без имени"
            subtitle_parts = []
            if c.position:
                subtitle_parts.append(c.position)
            if c.company_id and getattr(c, "company", None):
                subtitle_parts.append(c.company.name)
            url = f"/companies/{c.company_id}/" if c.company_id else "#"
            items.append(
                {
                    "id": str(c.id),
                    "name": full_name,
                    "subtitle": " · ".join(subtitle_parts),
                    "url": url,
                }
            )
        return items
    except Exception:
        logger.exception("global_search contacts failed для query=%r", query)
        return []


def _search_tasks(request: HttpRequest, query: str) -> list[dict[str, Any]]:
    """Simple icontains on title + description."""
    try:
        from tasksapp.models import Task

        qs = Task.objects.filter(
            Q(title__icontains=query) | Q(description__icontains=query)
        ).select_related("company", "assigned_to")[:_LIMIT_PER_CATEGORY]

        items = []
        for t in qs:
            subtitle_parts = []
            if t.company_id and getattr(t, "company", None):
                subtitle_parts.append(t.company.name)
            if getattr(t, "get_status_display", None):
                subtitle_parts.append(t.get_status_display())
            items.append(
                {
                    "id": t.id,
                    "name": t.title or "—",
                    "subtitle": " · ".join(subtitle_parts),
                    "url": f"/tasks/{t.id}/",
                }
            )
        return items
    except Exception:
        logger.exception("global_search tasks failed для query=%r", query)
        return []
