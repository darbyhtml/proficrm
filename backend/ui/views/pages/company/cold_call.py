"""Cold-call toggles + resets (W1.2 extraction → W1.4 dedup).

W1.2 (2026-04-21): Extracted from `company_detail.py` — 8 functions × ~85 LOC = 691 LOC total.
W1.4 (2026-04-22): Deduplicated → 1 generic toggle + 1 generic reset + 8 thin wrappers.
Zero external API change — все 8 URL endpoints остались теми же.

8 endpoints для 4 entity × 2 action (toggle/reset):
- `company_cold_call_toggle` — POST /companies/<uuid>/cold-call/toggle/
- `company_cold_call_reset` — POST /companies/<uuid>/cold-call/reset/
- `contact_cold_call_toggle` — POST /contacts/<uuid>/cold-call/toggle/
- `contact_cold_call_reset` — POST /contacts/<uuid>/cold-call/reset/
- `contact_phone_cold_call_toggle` — POST /contact-phones/<id>/cold-call/toggle/
- `contact_phone_cold_call_reset` — POST /contact-phones/<id>/cold-call/reset/
- `company_phone_cold_call_toggle` — POST /company-phones/<id>/cold-call/toggle/
- `company_phone_cold_call_reset` — POST /company-phones/<id>/cold-call/reset/

Paramterized по:
- `entity_kind` — ключ в JSON (company/contact/contact_phone/company_phone)
- `is_marked_attr`, `marked_at_attr`, `marked_by_attr` — имена полей на модели
- Service callable (ColdCallService.mark_X / reset_X)
- Человекочитаемые сообщения для success/already/not_marked
- `check_no_phone` — только Company.toggle делает edge-case check

Safety net: 24 URL-layer tests в backend/ui/tests_cold_call_views.py (W1.4 #1).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

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


# ---------------------------------------------------------------------------
# Generic config + handlers (W1.4 dedup)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _CCConfig:
    """Entity-specific config для generic cold-call handlers."""

    entity_kind: str  # "company" | "contact" | "contact_phone" | "company_phone"
    is_marked_attr: str
    marked_at_attr: str
    marked_by_attr: str
    # Сервис-методы (привязываются к ColdCallService в wrapper)
    mark_fn: Callable[..., dict[str, Any]] | None = None
    reset_fn: Callable[..., dict[str, Any]] | None = None
    service_kwarg: str = ""  # e.g. "company" / "contact" / "contact_phone"
    # Сообщения
    permission_error: str = "Нет прав на изменение признака 'Холодный звонок'."
    already_marked_msg: str = "Уже отмечен как холодный."
    not_marked_msg: str = "Не отмечен как холодный."
    success_mark_msg: str = "Отмечено: холодный звонок."
    success_reset_msg: str = "Отметка холодного звонка отменена."
    log_mark_msg: str = "Отмечено: холодный звонок"
    log_reset_msg: str = "Откат: холодный звонок"
    # Entity-kind-flags
    check_no_phone: bool = False  # только Company.toggle


def _cc_get_marked(entity: Any, cfg: _CCConfig) -> tuple[bool, Any, Any]:
    """Читает current is_marked / marked_at / marked_by с entity per cfg."""
    return (
        bool(getattr(entity, cfg.is_marked_attr, False)),
        getattr(entity, cfg.marked_at_attr, None),
        getattr(entity, cfg.marked_by_attr, None),
    )


def _cc_toggle_impl(
    *,
    request: HttpRequest,
    entity: Any,
    company: Company | None,
    user: User,
    cfg: _CCConfig,
    already_redirect_target: tuple[str, Any],
    entity_label: str = "",
) -> HttpResponse:
    """Generic toggle handler (shared by 4 wrappers).

    entity_label — человекочитаемое имя (например, phone number) для log и
    сообщений, если `{entity_label}` в шаблоне. Default empty.
    """
    # Permission — только для toggle (reset делает свой admin check)
    if not _can_edit_company(user, company):
        if _is_ajax(request):
            return JsonResponse({"ok": False, "error": cfg.permission_error}, status=403)
        messages.error(request, cfg.permission_error)
        if company:
            return redirect("company_detail", company_id=company.id)
        return redirect("dashboard")

    # Подтверждение
    confirmed = request.POST.get("confirmed") == "1"
    if not confirmed:
        if _is_ajax(request):
            return JsonResponse(
                {"ok": False, "error": "Требуется подтверждение действия."}, status=400
            )
        messages.error(request, "Требуется подтверждение действия.")
        return redirect(already_redirect_target[0], **already_redirect_target[1])

    # Already-marked?
    is_marked, marked_at, marked_by = _cc_get_marked(entity, cfg)
    if is_marked:
        if _is_ajax(request):
            return _cold_call_json(
                entity=cfg.entity_kind,
                entity_id=str(entity.id),
                is_cold_call=True,
                marked_at=marked_at,
                marked_by=str(marked_by or ""),
                can_reset=bool(require_admin(user)),
                message=cfg.already_marked_msg,
            )
        messages.info(request, cfg.already_marked_msg)
        return redirect(already_redirect_target[0], **already_redirect_target[1])

    # Service call
    from companies.services import ColdCallService

    result = cfg.mark_fn(**{cfg.service_kwarg: entity, "user": user})  # type: ignore[misc]

    # Edge case: Company.toggle — no main phone
    if cfg.check_no_phone and result.get("no_phone"):
        if _is_ajax(request):
            return JsonResponse(
                {"ok": False, "error": "У компании не задан основной телефон."}, status=400
            )
        messages.error(request, "У компании не задан основной телефон.")
        return redirect(already_redirect_target[0], **already_redirect_target[1])

    last_call = result.get("call")

    # AJAX response
    if _is_ajax(request):
        entity.refresh_from_db(fields=[cfg.is_marked_attr, cfg.marked_at_attr, cfg.marked_by_attr])
        _, new_marked_at, new_marked_by = _cc_get_marked(entity, cfg)
        return _cold_call_json(
            entity=cfg.entity_kind,
            entity_id=str(entity.id),
            is_cold_call=True,
            marked_at=new_marked_at,
            marked_by=str(new_marked_by or ""),
            can_reset=bool(require_admin(user)),
            message=cfg.success_mark_msg,
        )

    # Non-AJAX: message + redirect + log
    messages.success(request, cfg.success_mark_msg)
    meta: dict[str, Any] = {}
    if last_call:
        meta["call_id"] = str(last_call.id)
    # Extra meta per entity
    if cfg.entity_kind in ("contact", "contact_phone", "company_phone"):
        meta[f"{cfg.entity_kind}_id"] = str(entity.id)
    log_event(
        actor=user,
        verb=ActivityEvent.Verb.UPDATE,
        entity_type=cfg.entity_kind,
        entity_id=entity.id if cfg.entity_kind == "company" else str(entity.id),
        company_id=company.id if company else None,
        message=cfg.log_mark_msg,
        meta=meta,
    )
    return redirect(already_redirect_target[0], **already_redirect_target[1])


def _cc_reset_impl(
    *,
    request: HttpRequest,
    entity: Any,
    company: Company | None,
    user: User,
    cfg: _CCConfig,
    already_redirect_target: tuple[str, Any],
) -> HttpResponse:
    """Generic reset handler (shared by 4 wrappers). Requires admin."""
    # Not-marked?
    is_marked, marked_at, marked_by = _cc_get_marked(entity, cfg)
    # Для phones проверка чуть расширена: not is_marked AND not marked_at
    # Для company/contact — просто not is_marked (исторически так было в company_*)
    if cfg.entity_kind in ("contact_phone", "company_phone"):
        not_set = (not is_marked) and (not marked_at)
    else:
        not_set = not is_marked
    if not_set:
        if _is_ajax(request):
            return _cold_call_json(
                entity=cfg.entity_kind,
                entity_id=str(entity.id),
                is_cold_call=False,
                marked_at=marked_at,
                marked_by=str(marked_by or ""),
                can_reset=True,
                message=cfg.not_marked_msg,
            )
        messages.info(request, cfg.not_marked_msg)
        return redirect(already_redirect_target[0], **already_redirect_target[1])

    # Service call
    cfg.reset_fn(**{cfg.service_kwarg: entity, "user": user})  # type: ignore[misc]

    # AJAX response
    if _is_ajax(request):
        return _cold_call_json(
            entity=cfg.entity_kind,
            entity_id=str(entity.id),
            is_cold_call=False,
            marked_at=None,
            marked_by="",
            can_reset=True,
            message=cfg.success_reset_msg,
        )

    # Non-AJAX
    messages.success(request, cfg.success_reset_msg)
    log_event(
        actor=user,
        verb=ActivityEvent.Verb.UPDATE,
        entity_type=cfg.entity_kind,
        entity_id=entity.id if cfg.entity_kind == "company" else str(entity.id),
        company_id=company.id if company else None,
        message=cfg.log_reset_msg,
    )
    return redirect(already_redirect_target[0], **already_redirect_target[1])


def _cc_admin_guard(request: HttpRequest) -> HttpResponse | None:
    """Возвращает 403 response если user не admin. Иначе None."""
    user: User = request.user
    if require_admin(user):
        return None
    if _is_ajax(request):
        return JsonResponse(
            {
                "ok": False,
                "error": "Только администратор может откатить отметку холодного звонка.",
            },
            status=403,
        )
    messages.error(request, "Только администратор может откатить отметку холодного звонка.")
    return None  # caller ответственен за redirect


# ---------------------------------------------------------------------------
# 8 thin wrappers — preserve public URL routing
# ---------------------------------------------------------------------------


# --- Company ---
_CC_COMPANY = _CCConfig(
    entity_kind="company",
    is_marked_attr="primary_contact_is_cold_call",
    marked_at_attr="primary_cold_marked_at",
    marked_by_attr="primary_cold_marked_by",
    service_kwarg="company",
    permission_error="Нет прав на изменение признака 'Холодный звонок'.",
    already_marked_msg="Основной контакт уже отмечен как холодный.",
    not_marked_msg="Основной контакт не отмечен как холодный.",
    success_mark_msg="Отмечено: холодный звонок (основной контакт).",
    success_reset_msg="Отметка холодного звонка отменена (основной контакт).",
    log_mark_msg="Отмечено: холодный звонок (осн. контакт)",
    log_reset_msg="Откат: холодный звонок (осн. контакт)",
    check_no_phone=True,
)


@login_required
@require_can_view_company
@policy_required(resource_type="action", resource="ui:companies:cold_call:toggle")
def company_cold_call_toggle(request: HttpRequest, company_id) -> HttpResponse:
    """Отметить основной контакт компании как холодный звонок.

    W2.1.3c: @policy_required добавлен (existing resource, parity с
    contact_*/phone_* variants которые уже codified). @require_can_view_company
    остаётся как visibility check (defense-in-depth).
    """
    if request.method != "POST":
        return redirect("company_detail", company_id=company_id)
    from companies.services import ColdCallService

    company = get_object_or_404(
        Company.objects.select_related("responsible", "branch", "primary_cold_marked_by"),
        id=company_id,
    )
    cfg = _CCConfig(**{**_CC_COMPANY.__dict__, "mark_fn": ColdCallService.mark_company})
    return _cc_toggle_impl(
        request=request,
        entity=company,
        company=company,
        user=request.user,
        cfg=cfg,
        already_redirect_target=("company_detail", {"company_id": company.id}),
    )


@login_required
@require_can_view_company
@policy_required(resource_type="action", resource="ui:companies:cold_call:reset")
def company_cold_call_reset(request: HttpRequest, company_id) -> HttpResponse:
    """Откатить отметку холодного звонка для основного контакта компании (admin only).

    W2.1.3c: @policy_required добавлен (existing resource, parity с
    contact_*/phone_* variants). @require_can_view_company + inline
    _cc_admin_guard в _cc_reset_impl остаются (defense-in-depth).
    """
    if request.method != "POST":
        return redirect("company_detail", company_id=company_id)
    guard = _cc_admin_guard(request)
    if guard is not None:
        return guard
    # non-AJAX non-admin — fallthrough:
    if not require_admin(request.user):
        return redirect("company_detail", company_id=company_id)
    from companies.services import ColdCallService

    company = get_object_or_404(
        Company.objects.select_related("responsible", "branch"), id=company_id
    )
    cfg = _CCConfig(**{**_CC_COMPANY.__dict__, "reset_fn": ColdCallService.reset_company})
    return _cc_reset_impl(
        request=request,
        entity=company,
        company=company,
        user=request.user,
        cfg=cfg,
        already_redirect_target=("company_detail", {"company_id": company.id}),
    )


# --- Contact ---
_CC_CONTACT = _CCConfig(
    entity_kind="contact",
    is_marked_attr="is_cold_call",
    marked_at_attr="cold_marked_at",
    marked_by_attr="cold_marked_by",
    service_kwarg="contact",
    permission_error="Нет прав на изменение контактов этой компании.",
    already_marked_msg="Контакт уже отмечен как холодный.",
    not_marked_msg="Контакт не отмечен как холодный.",
    success_mark_msg="Отмечено: холодный звонок (контакт).",
    success_reset_msg="Отметка холодного звонка отменена (контакт).",
    log_mark_msg="Отмечено: холодный звонок (контакт)",
    log_reset_msg="Откат: холодный звонок (контакт)",
)


@login_required
@policy_required(resource_type="action", resource="ui:companies:cold_call:toggle")
def contact_cold_call_toggle(request: HttpRequest, contact_id) -> HttpResponse:
    """Отметить контакт как холодный звонок."""
    if request.method != "POST":
        return redirect("dashboard")
    from companies.services import ColdCallService

    contact = get_object_or_404(
        Contact.objects.select_related("company", "cold_marked_by"), id=contact_id
    )
    company = contact.company
    if not company:
        messages.error(request, "Контакт не привязан к компании.")
        return redirect("dashboard")
    cfg = _CCConfig(**{**_CC_CONTACT.__dict__, "mark_fn": ColdCallService.mark_contact})
    return _cc_toggle_impl(
        request=request,
        entity=contact,
        company=company,
        user=request.user,
        cfg=cfg,
        already_redirect_target=("company_detail", {"company_id": company.id}),
    )


@login_required
@policy_required(resource_type="action", resource="ui:companies:cold_call:reset")
def contact_cold_call_reset(request: HttpRequest, contact_id) -> HttpResponse:
    """Откат отметки холодного звонка для контакта (admin only)."""
    if request.method != "POST":
        return redirect("dashboard")
    guard = _cc_admin_guard(request)
    if guard is not None:
        return guard
    if not require_admin(request.user):
        return redirect("dashboard")
    from companies.services import ColdCallService

    contact = get_object_or_404(Contact.objects.select_related("company"), id=contact_id)
    company = contact.company
    if not company:
        messages.error(request, "Контакт не привязан к компании.")
        return redirect("dashboard")
    cfg = _CCConfig(**{**_CC_CONTACT.__dict__, "reset_fn": ColdCallService.reset_contact})
    return _cc_reset_impl(
        request=request,
        entity=contact,
        company=company,
        user=request.user,
        cfg=cfg,
        already_redirect_target=("company_detail", {"company_id": company.id}),
    )


# --- ContactPhone ---
def _cc_contact_phone_cfg() -> _CCConfig:
    return _CCConfig(
        entity_kind="contact_phone",
        is_marked_attr="is_cold_call",
        marked_at_attr="cold_marked_at",
        marked_by_attr="cold_marked_by",
        service_kwarg="contact_phone",
        permission_error="Нет прав на изменение контактов этой компании.",
        already_marked_msg="Этот номер уже отмечен как холодный.",
        not_marked_msg="Этот номер не отмечен как холодный.",
        # success_mark / log_mark — patched per instance с phone value
    )


@login_required
@policy_required(resource_type="action", resource="ui:companies:cold_call:toggle")
def contact_phone_cold_call_toggle(request: HttpRequest, contact_phone_id) -> HttpResponse:
    """Отметить номер телефона контакта как холодный звонок."""
    if request.method != "POST":
        return redirect("dashboard")
    from companies.services import ColdCallService

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
    phone_val = contact_phone.value
    cfg = _CCConfig(
        **{
            **_cc_contact_phone_cfg().__dict__,
            "mark_fn": ColdCallService.mark_contact_phone,
            "success_mark_msg": f"Отмечено: холодный звонок (номер {phone_val}).",
            "log_mark_msg": f"Отмечено: холодный звонок (номер {phone_val})",
        }
    )
    return _cc_toggle_impl(
        request=request,
        entity=contact_phone,
        company=company,
        user=request.user,
        cfg=cfg,
        already_redirect_target=("company_detail", {"company_id": company.id}),
    )


@login_required
@policy_required(resource_type="action", resource="ui:companies:cold_call:reset")
def contact_phone_cold_call_reset(request: HttpRequest, contact_phone_id) -> HttpResponse:
    """Откат отметки холодного звонка для номера контакта (admin only)."""
    if request.method != "POST":
        return redirect("dashboard")
    guard = _cc_admin_guard(request)
    if guard is not None:
        return guard
    if not require_admin(request.user):
        return redirect("dashboard")
    from companies.services import ColdCallService

    contact_phone = get_object_or_404(
        ContactPhone.objects.select_related("contact__company"), id=contact_phone_id
    )
    contact = contact_phone.contact
    company = contact.company if contact else None
    if not company:
        messages.error(request, "Контакт не привязан к компании.")
        return redirect("dashboard")
    phone_val = contact_phone.value
    cfg = _CCConfig(
        **{
            **_cc_contact_phone_cfg().__dict__,
            "reset_fn": ColdCallService.reset_contact_phone,
            "success_reset_msg": f"Отметка холодного звонка отменена (номер {phone_val}).",
            "log_reset_msg": f"Откат: холодный звонок (номер {phone_val})",
        }
    )
    return _cc_reset_impl(
        request=request,
        entity=contact_phone,
        company=company,
        user=request.user,
        cfg=cfg,
        already_redirect_target=("company_detail", {"company_id": company.id}),
    )


# --- CompanyPhone ---
def _cc_company_phone_cfg() -> _CCConfig:
    return _CCConfig(
        entity_kind="company_phone",
        is_marked_attr="is_cold_call",
        marked_at_attr="cold_marked_at",
        marked_by_attr="cold_marked_by",
        service_kwarg="company_phone",
        permission_error="Нет прав на изменение данных этой компании.",
        already_marked_msg="Этот номер уже отмечен как холодный.",
        not_marked_msg="Этот номер не отмечен как холодный.",
    )


@login_required
@policy_required(resource_type="action", resource="ui:companies:cold_call:toggle")
def company_phone_cold_call_toggle(request: HttpRequest, company_phone_id) -> HttpResponse:
    """Отметить дополнительный номер телефона компании как холодный звонок."""
    if request.method != "POST":
        return redirect("dashboard")
    from companies.services import ColdCallService

    try:
        company_phone = get_object_or_404(
            CompanyPhone.objects.select_related("company", "cold_marked_by"),
            id=company_phone_id,
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
    phone_val = company_phone.value
    cfg = _CCConfig(
        **{
            **_cc_company_phone_cfg().__dict__,
            "mark_fn": ColdCallService.mark_company_phone,
            "success_mark_msg": f"Отмечено: холодный звонок (номер {phone_val}).",
            "log_mark_msg": f"Отмечено: холодный звонок (номер {phone_val})",
        }
    )
    return _cc_toggle_impl(
        request=request,
        entity=company_phone,
        company=company,
        user=request.user,
        cfg=cfg,
        already_redirect_target=("company_detail", {"company_id": company.id}),
    )


@login_required
@policy_required(resource_type="action", resource="ui:companies:cold_call:reset")
def company_phone_cold_call_reset(request: HttpRequest, company_phone_id) -> HttpResponse:
    """Откат отметки холодного звонка для доп. номера компании (admin only)."""
    if request.method != "POST":
        return redirect("dashboard")
    guard = _cc_admin_guard(request)
    if guard is not None:
        return guard
    if not require_admin(request.user):
        return redirect("dashboard")
    from companies.services import ColdCallService

    company_phone = get_object_or_404(
        CompanyPhone.objects.select_related("company"), id=company_phone_id
    )
    company = company_phone.company
    phone_val = company_phone.value
    cfg = _CCConfig(
        **{
            **_cc_company_phone_cfg().__dict__,
            "reset_fn": ColdCallService.reset_company_phone,
            "success_reset_msg": f"Отметка холодного звонка отменена (номер {phone_val}).",
            "log_reset_msg": f"Откат: холодный звонок (номер {phone_val})",
        }
    )
    return _cc_reset_impl(
        request=request,
        entity=company_phone,
        company=company,
        user=request.user,
        cfg=cfg,
        already_redirect_target=("company_detail", {"company_id": company.id}),
    )
