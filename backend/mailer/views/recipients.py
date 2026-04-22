"""
Views для управления получателями кампаний.
"""

from __future__ import annotations

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone

from accounts.models import User
from audit.models import ActivityEvent
from audit.service import log_event
from companies.models import Company, ContactEmail
from mailer.constants import COOLDOWN_DAYS_DEFAULT
from mailer.forms import CampaignGenerateRecipientsForm, CampaignRecipientAddForm
from mailer.models import Campaign, CampaignQueue, CampaignRecipient, EmailCooldown, Unsubscribe
from mailer.views._helpers import _can_manage_campaign
from policy.decorators import policy_required
from policy.engine import enforce

logger = logging.getLogger(__name__)


@login_required
@policy_required(resource_type="action", resource="ui:mail:campaigns:pick")
def campaign_pick(request: HttpRequest) -> JsonResponse:
    """
    Список кампаний, доступных пользователю для добавления email из карточки компании.
    Поддерживает поиск (?q=...) и пагинацию (?page=N, page_size=25/50).
    """
    user: User = request.user
    # W2.1.5: inline enforce() preserved as defense-in-depth.
    enforce(
        user=request.user,
        resource_type="action",
        resource="ui:mail:campaigns:pick",
        context={"path": request.path, "method": request.method},
    )
    qs = (
        Campaign.objects.filter(is_template=False)
        .only("id", "name", "status")
        .order_by("-created_at")
    )
    if user.role == User.Role.MANAGER:
        qs = qs.filter(created_by=user)
    elif user.role in (User.Role.BRANCH_DIRECTOR, User.Role.SALES_HEAD) and user.branch_id:
        qs = qs.filter(created_by__branch_id=user.branch_id)
    elif user.role not in (User.Role.ADMIN, User.Role.GROUP_MANAGER) and not user.is_superuser:
        qs = qs.filter(created_by=user)

    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(name__icontains=q)

    from django.core.paginator import Paginator as _Paginator

    try:
        page_size = min(int(request.GET.get("page_size") or 25), 50)
    except (ValueError, TypeError):
        page_size = 25
    paginator = _Paginator(qs, page_size)
    try:
        page_num = int(request.GET.get("page") or 1)
    except (ValueError, TypeError):
        page_num = 1
    # Ограничиваем номер страницы снизу и сверху — предотвращает дорогие запросы
    page_num = max(1, min(page_num, paginator.num_pages or 1))
    page = paginator.get_page(page_num)

    items = [{"id": str(c.id), "name": c.name, "status": c.status} for c in page.object_list]
    return JsonResponse(
        {
            "ok": True,
            "campaigns": items,
            "total": paginator.count,
            "num_pages": paginator.num_pages,
            "page": page.number,
            "has_next": page.has_next(),
        }
    )


@login_required
@policy_required(resource_type="action", resource="ui:mail:campaigns:add_email")
def campaign_add_email(request: HttpRequest) -> JsonResponse:
    """Добавить email в выбранную кампанию (AJAX)."""
    user: User = request.user
    # W2.1.5: inline enforce() preserved as defense-in-depth.
    enforce(
        user=request.user,
        resource_type="action",
        resource="ui:mail:campaigns:add_email",
        context={"path": request.path, "method": request.method},
    )
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "Метод не разрешен."}, status=405)

    campaign_id = (request.POST.get("campaign_id") or "").strip()
    email = (request.POST.get("email") or "").strip().lower()
    company_id = (request.POST.get("company_id") or "").strip()

    if not campaign_id or not email:
        return JsonResponse({"ok": False, "error": "campaign_id и email обязательны."}, status=400)

    camp = get_object_or_404(Campaign, id=campaign_id)
    if not _can_manage_campaign(user, camp):
        return JsonResponse({"ok": False, "error": "Нет прав на эту кампанию."}, status=403)

    now = timezone.now()
    cd = EmailCooldown.objects.filter(
        created_by=user, email__iexact=email, until_at__gt=now
    ).first()
    if cd:
        return JsonResponse(
            {
                "ok": False,
                "error": f"Этот email временно нельзя использовать (до {cd.until_at:%d.%m.%Y %H:%M}).",
            },
            status=400,
        )

    if Unsubscribe.objects.filter(email__iexact=email).exists():
        CampaignRecipient.objects.get_or_create(
            campaign=camp,
            email=email,
            defaults={"status": CampaignRecipient.Status.UNSUBSCRIBED},
        )
        return JsonResponse(
            {
                "ok": True,
                "status": "unsubscribed",
                "message": "Email в отписках — добавлен как «Отписался».",
            }
        )

    company_uuid = None
    if company_id:
        try:
            from uuid import UUID

            company_uuid = UUID(company_id)
        except Exception:
            company_uuid = None

    r, created = CampaignRecipient.objects.get_or_create(
        campaign=camp,
        email=email,
        defaults={"status": CampaignRecipient.Status.PENDING, "company_id": company_uuid},
    )
    if not created and r.status != CampaignRecipient.Status.PENDING:
        if r.status == CampaignRecipient.Status.FAILED:
            r.status = CampaignRecipient.Status.PENDING
            r.last_error = ""
            if company_uuid and not r.company_id:
                r.company_id = company_uuid
            r.save(update_fields=["status", "last_error", "company_id", "updated_at"])
            return JsonResponse(
                {
                    "ok": True,
                    "status": "pending",
                    "message": "Email был с ошибкой — возвращён в очередь.",
                }
            )
        return JsonResponse(
            {
                "ok": True,
                "status": r.status,
                "message": "Email уже есть в кампании. Повторная отправка не выполняется автоматически.",
            }
        )

    return JsonResponse(
        {
            "ok": True,
            "status": r.status,
            "message": "Email добавлен." if created else "Email уже был в кампании.",
        }
    )


@login_required
@policy_required(resource_type="action", resource="ui:mail:campaigns:recipients:add")
def campaign_recipient_add(request: HttpRequest, campaign_id) -> HttpResponse:
    # W2.1.5: inline enforce() preserved as defense-in-depth.
    enforce(
        user=request.user,
        resource_type="action",
        resource="ui:mail:campaigns:recipients:add",
        context={"path": request.path, "method": request.method},
    )
    user: User = request.user
    camp = get_object_or_404(Campaign, id=campaign_id)
    if not _can_manage_campaign(user, camp):
        messages.error(request, "Доступ запрещён.")
        return redirect("campaigns")
    if request.method != "POST":
        return redirect("campaign_detail", campaign_id=camp.id)

    form = CampaignRecipientAddForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Некорректный email.")
        return redirect("campaign_detail", campaign_id=camp.id)

    email = (form.cleaned_data["email"] or "").strip().lower()
    if not email:
        messages.error(request, "Введите email.")
        return redirect("campaign_detail", campaign_id=camp.id)

    now = timezone.now()
    cd = EmailCooldown.objects.filter(
        created_by=user, email__iexact=email, until_at__gt=now
    ).first()
    if cd:
        messages.error(
            request,
            f"Этот email временно нельзя использовать для рассылки (до {cd.until_at:%d.%m.%Y %H:%M}).",
        )
        return redirect("campaign_detail", campaign_id=camp.id)

    if Unsubscribe.objects.filter(email__iexact=email).exists():
        CampaignRecipient.objects.get_or_create(
            campaign=camp,
            email=email,
            defaults={"status": CampaignRecipient.Status.UNSUBSCRIBED},
        )
        messages.warning(request, f"{email} в списке отписавшихся — добавлен как 'Отписался'.")
        return redirect("campaign_detail", campaign_id=camp.id)

    recipient, created = CampaignRecipient.objects.get_or_create(
        campaign=camp, email=email, defaults={"status": CampaignRecipient.Status.PENDING}
    )
    if not created and recipient.status != CampaignRecipient.Status.PENDING:
        if recipient.status == CampaignRecipient.Status.FAILED:
            recipient.status = CampaignRecipient.Status.PENDING
            recipient.last_error = ""
            recipient.save(update_fields=["status", "last_error", "updated_at"])
            messages.success(request, f"Получатель был с ошибкой: {email} (возвращён в очередь)")
        else:
            messages.info(
                request,
                f"Получатель уже есть: {email} (статус: {recipient.get_status_display()}). Повторная отправка не включена.",
            )
    elif created:
        messages.success(request, f"Добавлен получатель: {email}")
    else:
        messages.info(request, f"Получатель уже есть: {email}")
    return redirect("campaign_detail", campaign_id=camp.id)


@login_required
@policy_required(resource_type="action", resource="ui:mail:campaigns:recipients:delete")
def campaign_recipient_delete(request: HttpRequest, campaign_id, recipient_id) -> HttpResponse:
    # W2.1.5: inline enforce() preserved as defense-in-depth.
    enforce(
        user=request.user,
        resource_type="action",
        resource="ui:mail:campaigns:recipients:delete",
        context={"path": request.path, "method": request.method},
    )
    user: User = request.user
    camp = get_object_or_404(Campaign, id=campaign_id)
    if not _can_manage_campaign(user, camp):
        messages.error(request, "Доступ запрещён.")
        return redirect("campaigns")
    if request.method != "POST":
        return redirect("campaign_detail", campaign_id=camp.id)

    r = get_object_or_404(CampaignRecipient, id=recipient_id, campaign=camp)
    email = r.email
    r.delete()
    messages.success(request, f"Удалён получатель: {email}")
    return redirect("campaign_detail", campaign_id=camp.id)


@login_required
@policy_required(resource_type="action", resource="ui:mail:campaigns:recipients:bulk_delete")
def campaign_recipients_bulk_delete(request: HttpRequest, campaign_id) -> HttpResponse:
    """Массовое удаление получателей."""
    # W2.1.5: inline enforce() preserved as defense-in-depth.
    enforce(
        user=request.user,
        resource_type="action",
        resource="ui:mail:campaigns:recipients:bulk_delete",
        context={"path": request.path, "method": request.method},
    )
    user: User = request.user
    camp = get_object_or_404(Campaign, id=campaign_id)
    if not _can_manage_campaign(user, camp):
        messages.error(request, "Доступ запрещён.")
        return redirect("campaigns")
    if request.method != "POST":
        return redirect("campaign_detail", campaign_id=camp.id)

    recipient_ids = request.POST.getlist("recipient_ids")
    if not recipient_ids:
        messages.warning(request, "Не выбраны получатели для удаления.")
        return redirect("campaign_detail", campaign_id=camp.id)

    try:
        from uuid import UUID

        valid_ids = [UUID(rid) for rid in recipient_ids]
    except (ValueError, TypeError):
        messages.error(request, "Некорректные ID получателей.")
        return redirect("campaign_detail", campaign_id=camp.id)

    recipients = CampaignRecipient.objects.filter(id__in=valid_ids, campaign=camp)
    count = recipients.count()
    if count == 0:
        messages.warning(request, "Не найдено получателей для удаления.")
        return redirect("campaign_detail", campaign_id=camp.id)

    if count > 500:
        messages.error(request, "За раз можно удалить не более 500 получателей.")
        return redirect("campaign_detail", campaign_id=camp.id)

    recipients.delete()
    messages.success(request, f"Удалено получателей: {count}")
    log_event(
        actor=user,
        verb=ActivityEvent.Verb.DELETE,
        entity_type="campaign",
        entity_id=camp.id,
        message=f"Массовое удаление получателей: {count}",
    )
    return redirect("campaign_detail", campaign_id=camp.id)


@login_required
@policy_required(resource_type="action", resource="ui:mail:campaigns:recipients:generate")
def campaign_generate_recipients(request: HttpRequest, campaign_id) -> HttpResponse:
    """
    Генерируем получателей из email контактов + основного email компании.
    """
    # W2.1.5: inline enforce() preserved as defense-in-depth.
    enforce(
        user=request.user,
        resource_type="action",
        resource="ui:mail:campaigns:recipients:generate",
        context={"path": request.path, "method": request.method},
    )
    user: User = request.user
    camp = get_object_or_404(Campaign, id=campaign_id)
    if not _can_manage_campaign(user, camp):
        messages.error(request, "Доступ запрещён.")
        return redirect("campaigns")

    if request.method != "POST":
        return redirect("campaign_detail", campaign_id=camp.id)

    form = CampaignGenerateRecipientsForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Неверные параметры.")
        return redirect("campaign_detail", campaign_id=camp.id)

    limit = int(form.cleaned_data["limit"])
    if limit < 1 or limit > 5000:
        messages.error(request, "Лимит должен быть от 1 до 5000.")
        return redirect("campaign_detail", campaign_id=camp.id)

    include_company_email = bool(request.POST.get("include_company_email"))
    include_contact_emails = bool(request.POST.get("include_contact_emails"))
    contact_email_types = form.cleaned_data.get("contact_email_types", [])

    if not include_company_email and not include_contact_emails:
        messages.error(request, "Выберите хотя бы один источник email адресов.")
        return redirect("campaign_detail", campaign_id=camp.id)

    if include_contact_emails and not contact_email_types:
        contact_email_types = [choice[0] for choice in ContactEmail.EmailType.choices]

    company_qs = Company.objects.all()

    branch = (request.POST.get("branch") or "").strip()
    if not branch and user.branch_id:
        branch = str(user.branch_id)

    if (
        user.role in (User.Role.MANAGER, User.Role.SALES_HEAD, User.Role.BRANCH_DIRECTOR)
        and user.branch_id
    ):
        if branch and branch != str(user.branch_id):
            messages.error(request, "Вы можете выбрать только свой филиал.")
            return redirect("campaign_detail", campaign_id=camp.id)
        branch = str(user.branch_id)

    responsible = (request.POST.get("responsible") or "").strip()
    if not responsible:
        responsible = str(user.id)

    statuses = request.POST.getlist("status")
    spheres = request.POST.getlist("sphere")
    sphere_ids = []
    for s in spheres:
        if s and s.strip():
            try:
                sphere_ids.append(int(s.strip()))
            except (ValueError, TypeError):
                pass

    regions = request.POST.getlist("region") or []
    region_ids: list[int] = []
    for r in regions:
        r = (r or "").strip()
        if not r:
            continue
        try:
            region_ids.append(int(r))
        except (ValueError, TypeError):
            pass

    if branch:
        company_qs = company_qs.filter(branch_id=branch)
    if responsible:
        company_qs = company_qs.filter(responsible_id=responsible)
    if statuses:
        company_qs = company_qs.filter(status_id__in=statuses)
    if sphere_ids:
        company_qs = company_qs.filter(spheres__id__in=sphere_ids).distinct()
    if region_ids:
        company_qs = company_qs.filter(region_id__in=region_ids)

    company_ids = list(company_qs.order_by().values_list("id", flat=True).distinct())

    # "Защита от дурака": перепроверяем фильтры
    if company_ids and (branch or responsible or statuses or sphere_ids or region_ids):
        valid_qs = Company.objects.filter(id__in=company_ids)
        if branch:
            valid_qs = valid_qs.filter(branch_id=branch)
        if responsible:
            valid_qs = valid_qs.filter(responsible_id=responsible)
        if statuses:
            valid_qs = valid_qs.filter(status_id__in=statuses)
        if sphere_ids:
            valid_qs = valid_qs.filter(spheres__id__in=sphere_ids)
        if region_ids:
            valid_qs = valid_qs.filter(region_id__in=region_ids)
        valid_company_ids = list(valid_qs.values_list("id", flat=True).distinct())
        if len(valid_company_ids) != len(company_ids):
            logger.warning(
                f"Campaign {camp.id}: Filter re-check removed {len(company_ids) - len(valid_company_ids)} companies "
                f"(branch={branch!r}, responsible={responsible!r}, statuses={statuses}, spheres={sphere_ids}, regions={region_ids})."
            )
            company_ids = valid_company_ids

    if not branch and user.role in (User.Role.ADMIN, User.Role.GROUP_MANAGER):
        messages.warning(
            request, "Филиал: «Любой». В рассылку могут попасть компании из других регионов."
        )

    created = 0
    skipped_cooldown = 0

    now = timezone.now()
    cooldown_emails = {
        (e or "").strip().lower()
        for e in EmailCooldown.objects.filter(created_by=user, until_at__gt=now).values_list(
            "email", flat=True
        )
    }

    candidates: dict[str, tuple[str | None, str | None]] = {}

    if include_contact_emails and company_ids:
        emails_qs = ContactEmail.objects.filter(contact__company_id__in=company_ids)
        if contact_email_types:
            emails_qs = emails_qs.filter(type__in=contact_email_types)
        for value, contact_id, company_id in emails_qs.values_list(
            "value", "contact_id", "contact__company_id"
        ).iterator():
            email = (value or "").strip().lower()
            if not email or email in candidates:
                continue
            candidates[email] = (
                str(contact_id) if contact_id else None,
                str(company_id) if company_id else None,
            )
            if len(candidates) >= (limit * 3):
                break

    if include_company_email and company_ids:
        for email_value, company_id in (
            Company.objects.filter(id__in=company_ids).values_list("email", "id").iterator()
        ):
            email = (email_value or "").strip().lower()
            if not email or email in candidates:
                continue
            candidates[email] = (None, str(company_id))
            if len(candidates) >= (limit * 3):
                break

        from companies.models import CompanyEmail

        for email_value, company_id in (
            CompanyEmail.objects.filter(company_id__in=company_ids)
            .values_list("value", "company_id")
            .iterator()
        ):
            email = (email_value or "").strip().lower()
            if not email or email in candidates:
                continue
            candidates[email] = (None, str(company_id))
            if len(candidates) >= (limit * 3):
                break

    if not candidates:
        messages.info(request, "Email адреса не найдены по выбранным источникам/фильтрам.")
    else:
        for e in list(candidates.keys()):
            if e in cooldown_emails:
                skipped_cooldown += 1
                candidates.pop(e, None)

        unsub_set = set(
            Unsubscribe.objects.filter(email__in=list(candidates.keys())).values_list(
                "email", flat=True
            )
        )
        unsub_set = {(e or "").strip().lower() for e in unsub_set if (e or "").strip()}

        existing_set = set(
            CampaignRecipient.objects.filter(
                campaign=camp, email__in=list(candidates.keys())
            ).values_list("email", flat=True)
        )
        existing_set = {(e or "").strip().lower() for e in existing_set if (e or "").strip()}

        to_create = []
        for email, (contact_id, company_id) in candidates.items():
            if created >= limit:
                break
            if email in existing_set:
                continue
            status = (
                CampaignRecipient.Status.UNSUBSCRIBED
                if email in unsub_set
                else CampaignRecipient.Status.PENDING
            )
            to_create.append(
                CampaignRecipient(
                    campaign=camp,
                    email=email,
                    status=status,
                    contact_id=contact_id,
                    company_id=company_id,
                )
            )
            created += 1

        if to_create:
            CampaignRecipient.objects.bulk_create(to_create, ignore_conflicts=True)

        if unsub_set:
            CampaignRecipient.objects.filter(campaign=camp, email__in=list(unsub_set)).exclude(
                status=CampaignRecipient.Status.UNSUBSCRIBED
            ).update(status=CampaignRecipient.Status.UNSUBSCRIBED, updated_at=now)

    normalized_sphere_ids = []
    if sphere_ids:
        for sid in sphere_ids:
            try:
                normalized_sphere_ids.append(int(sid))
            except (ValueError, TypeError):
                pass

    camp.filter_meta = {
        "branch": branch,
        "responsible": responsible,
        "status": statuses,
        "sphere": normalized_sphere_ids,
        "region": region_ids,
        "limit": limit,
        "include_company_email": include_company_email,
        "include_contact_emails": include_contact_emails,
        "contact_email_types": contact_email_types,
    }
    camp.save(update_fields=["filter_meta", "updated_at"])

    msg = f"Получатели сгенерированы: +{created}"
    if skipped_cooldown:
        msg += f" (пропущено из-за паузы: {skipped_cooldown})"
    messages.success(request, msg)
    log_event(
        actor=user,
        verb=ActivityEvent.Verb.UPDATE,
        entity_type="campaign",
        entity_id=camp.id,
        message="Сгенерированы получатели",
        meta={"added": created},
    )
    return redirect("campaign_detail", campaign_id=camp.id)


@login_required
def campaign_recipients_reset(request: HttpRequest, campaign_id) -> HttpResponse:
    """
    Вернуть получателей для повторной рассылки.
    По умолчанию: FAILED → PENDING.
    Опционально (только админ): SENT/FAILED → PENDING.
    """
    user: User = request.user
    camp = get_object_or_404(Campaign, id=campaign_id)
    if not _can_manage_campaign(user, camp):
        messages.error(request, "Доступ запрещён.")
        return redirect("campaigns")
    if request.method != "POST":
        return redirect("campaign_detail", campaign_id=camp.id)

    scope = (request.POST.get("scope") or "failed").strip().lower()
    if scope not in ("failed", "all"):
        messages.error(request, "Некорректный режим сброса.")
        return redirect("campaign_detail", campaign_id=camp.id)

    if scope == "failed":
        enforce(
            user=request.user,
            resource_type="action",
            resource="ui:mail:campaigns:recipients:reset_failed",
            context={"path": request.path, "method": request.method},
        )
        qs = camp.recipients.filter(status=CampaignRecipient.Status.FAILED)
        updated = qs.update(status=CampaignRecipient.Status.PENDING, last_error="")
        messages.success(
            request,
            f"Возвращено в очередь (только ошибки): {updated}. Нажмите «Старт», чтобы снова запустить рассылку.",
        )
        log_event(
            actor=user,
            verb=ActivityEvent.Verb.UPDATE,
            entity_type="campaign",
            entity_id=camp.id,
            message="Сброс статусов получателей (только ошибки)",
            meta={"reset": updated, "scope": "failed"},
        )
    else:
        enforce(
            user=request.user,
            resource_type="action",
            resource="ui:mail:campaigns:recipients:reset_all",
            context={"path": request.path, "method": request.method},
        )
        if user.role != User.Role.ADMIN and not user.is_superuser:
            messages.error(request, "Недостаточно прав для повторной отправки всем.")
            return redirect("campaign_detail", campaign_id=camp.id)
        qs = camp.recipients.filter(
            status__in=[CampaignRecipient.Status.SENT, CampaignRecipient.Status.FAILED]
        )
        updated = qs.update(status=CampaignRecipient.Status.PENDING, last_error="")
        messages.success(
            request,
            f"Возвращено в очередь (включая отправленные): {updated}. "
            f"Нажмите «Старт», чтобы снова запустить рассылку. ВНИМАНИЕ: письма могут уйти повторно.",
        )
        log_event(
            actor=user,
            verb=ActivityEvent.Verb.UPDATE,
            entity_type="campaign",
            entity_id=camp.id,
            message="Сброс статусов получателей (включая отправленные)",
            meta={"reset": updated, "scope": "all"},
        )

    camp.status = Campaign.Status.DRAFT
    camp.save(update_fields=["status", "updated_at"])
    queue_entry = getattr(camp, "queue_entry", None)
    if queue_entry and queue_entry.status in (
        CampaignQueue.Status.PENDING,
        CampaignQueue.Status.PROCESSING,
    ):
        queue_entry.status = CampaignQueue.Status.CANCELLED
        queue_entry.completed_at = timezone.now()
        queue_entry.save(update_fields=["status", "completed_at"])
    return redirect("campaign_detail", campaign_id=camp.id)


@login_required
@policy_required(resource_type="action", resource="ui:mail:campaigns:clear")
def campaign_clear(request: HttpRequest, campaign_id) -> HttpResponse:
    """Очистить кампанию от получателей. Перед очисткой ставим cooldown на email."""
    # W2.1.5: inline enforce() preserved as defense-in-depth.
    enforce(
        user=request.user,
        resource_type="action",
        resource="ui:mail:campaigns:clear",
        context={"path": request.path, "method": request.method},
    )
    user: User = request.user
    camp = get_object_or_404(Campaign, id=campaign_id)
    if not _can_manage_campaign(user, camp):
        messages.error(request, "Доступ запрещён.")
        return redirect("campaigns")
    if request.method != "POST":
        return redirect("campaign_detail", campaign_id=camp.id)

    now = timezone.now()
    until = now + timezone.timedelta(days=COOLDOWN_DAYS_DEFAULT)

    emails = list(camp.recipients.values_list("email", flat=True))
    with transaction.atomic():
        for e in emails:
            email = (e or "").strip().lower()
            if not email:
                continue
            EmailCooldown.objects.update_or_create(
                created_by=user,
                email=email,
                defaults={"until_at": until},
            )

        removed = camp.recipients.count()
        camp.recipients.all().delete()
        camp.status = Campaign.Status.DRAFT
        camp.save(update_fields=["status", "updated_at"])

        queue_entry = getattr(camp, "queue_entry", None)
        if queue_entry and queue_entry.status in (
            CampaignQueue.Status.PENDING,
            CampaignQueue.Status.PROCESSING,
        ):
            queue_entry.status = CampaignQueue.Status.CANCELLED
            queue_entry.completed_at = timezone.now()
            queue_entry.save(update_fields=["status", "completed_at"])

    messages.success(
        request,
        f"Кампания очищена. Удалено получателей: {removed}. Повторно эти email можно использовать через {COOLDOWN_DAYS_DEFAULT} дн.",
    )
    log_event(
        actor=user,
        verb=ActivityEvent.Verb.UPDATE,
        entity_type="campaign",
        entity_id=camp.id,
        message="Очищена кампания",
        meta={"removed": removed, "cooldown_days": COOLDOWN_DAYS_DEFAULT},
    )
    return redirect("campaign_detail", campaign_id=camp.id)
