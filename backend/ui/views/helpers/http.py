"""Generic request-processing helpers для UI views.

Extracted из backend/ui/views/_base.py в W1.1 refactor.
Zero behavior change — copy-paste, callers still import from _base.py via re-exports.
"""

from __future__ import annotations

from datetime import datetime

from django.http import HttpRequest, JsonResponse
from django.utils import timezone


def _is_ajax(request: HttpRequest) -> bool:
    # Django 4+ убрал request.is_ajax(); используем заголовок как и в других AJAX endpoints проекта.
    return (request.headers.get("X-Requested-With") or "") == "XMLHttpRequest"


def _safe_next_v3(request: HttpRequest, company_id) -> str | None:
    """F4 R3: если POST/GET содержит `next`, который указывает на v3-preview
    этой же компании — вернуть его. Whitelist защита от open-redirect.

    Используется в view-хендлерах create/delete (deal/note/task/phone/email),
    чтобы после submit возвращаться туда, откуда пришёл запрос (v3/b/ vs
    классическая карточка).
    """
    nxt = (request.POST.get("next") or request.GET.get("next") or "").strip()
    if not nxt:
        return None
    prefix = f"/companies/{company_id}/v3/"
    # whitelist: только внутренние v3-URL этой компании
    if nxt.startswith(prefix) and "\n" not in nxt and "\r" not in nxt:
        return nxt
    return None


def _dt_label(dt: datetime | None) -> str:
    if not dt:
        return ""
    try:
        return timezone.localtime(dt).strftime("%d.%m.%Y %H:%M")
    except Exception:
        try:
            return dt.strftime("%d.%m.%Y %H:%M")
        except Exception:
            return ""


def _cold_call_json(
    *,
    entity: str,
    entity_id: str,
    is_cold_call: bool,
    marked_at: datetime | None,
    marked_by: str,
    can_reset: bool,
    message: str,
) -> JsonResponse:
    return JsonResponse(
        {
            "ok": True,
            "entity": entity,
            "id": entity_id,
            "is_cold_call": bool(is_cold_call),
            "has_mark": bool(marked_at),
            "marked_at": _dt_label(marked_at),
            "marked_by": marked_by or "",
            "can_reset": bool(can_reset),
            "message": message or "",
        }
    )
