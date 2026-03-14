"""
Views: создание, редактирование, удаление и клонирование кампаний.
"""
from __future__ import annotations

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.files.storage import default_storage
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from accounts.models import User
from audit.models import ActivityEvent
from audit.service import log_event
from mailer.forms import CampaignForm
from mailer.models import Campaign, GlobalMailAccount
from policy.engine import enforce
from mailer.views._helpers import _can_manage_campaign, _contains_links

logger = logging.getLogger(__name__)


@login_required
def campaign_create(request: HttpRequest) -> HttpResponse:
    enforce(user=request.user, resource_type="action", resource="ui:mail:campaigns:create", context={"path": request.path, "method": request.method})
    user: User = request.user
    smtp_cfg = GlobalMailAccount.load()
    if request.method == "POST":
        form = CampaignForm(request.POST, request.FILES)
        if form.is_valid():
            camp: Campaign = form.save(commit=False)
            camp.created_by = user
            camp.status = Campaign.Status.DRAFT
            camp.save()
            messages.success(request, "Кампания создана.")
            if _contains_links(camp.body_html or ""):
                messages.warning(
                    request,
                    "В письме есть ссылки. Такие письма иногда попадают в спам или блокируются почтовиками. "
                    "Это не запрещено, просто предупреждение.",
                )
            return redirect("campaign_detail", campaign_id=camp.id)
    else:
        form = CampaignForm()
    return render(
        request,
        "ui/mail/campaign_form.html",
        {
            "form": form,
            "mode": "create",
            "smtp_from_email": (smtp_cfg.from_email or smtp_cfg.smtp_username or "").strip(),
            "attachment_ext": "",
            "attachment_filename": "",
        },
    )


@login_required
def campaign_edit(request: HttpRequest, campaign_id) -> HttpResponse:
    enforce(user=request.user, resource_type="action", resource="ui:mail:campaigns:edit", context={"path": request.path, "method": request.method})
    user: User = request.user
    camp = get_object_or_404(Campaign, id=campaign_id)
    if not _can_manage_campaign(user, camp):
        messages.error(request, "Доступ запрещён.")
        return redirect("campaigns")
    smtp_cfg = GlobalMailAccount.load()

    if request.method == "POST":
        form = CampaignForm(request.POST, request.FILES, instance=camp)
        if form.is_valid():
            form.save()
            messages.success(request, "Кампания сохранена.")
            if _contains_links(camp.body_html or ""):
                messages.warning(
                    request,
                    "В письме есть ссылки. Такие письма иногда попадают в спам или блокируются почтовиками. "
                    "Это не запрещено, просто предупреждение.",
                )
            return redirect("campaign_detail", campaign_id=camp.id)
    else:
        form = CampaignForm(instance=camp)

    attachment_filename = ""
    attachment_ext = ""
    attachment_size = None
    if getattr(camp, "attachment", None):
        try:
            attachment_filename = (camp.attachment_original_name or (camp.attachment.name.split("/")[-1] if camp.attachment and camp.attachment.name else "")).strip()
            if attachment_filename and "." in attachment_filename:
                attachment_ext = attachment_filename.split(".")[-1].upper()
            else:
                attachment_ext = ""
            if camp.attachment and camp.attachment.name and default_storage.exists(camp.attachment.name):
                try:
                    attachment_size = camp.attachment.size
                except OSError:
                    pass
        except Exception:
            attachment_filename = ""
            attachment_ext = ""
    return render(
        request,
        "ui/mail/campaign_form.html",
        {
            "form": form,
            "mode": "edit",
            "campaign": camp,
            "smtp_from_email": (smtp_cfg.from_email or smtp_cfg.smtp_username or "").strip(),
            "attachment_ext": attachment_ext,
            "attachment_filename": attachment_filename,
            "attachment_size": attachment_size,
        },
    )


@login_required
def campaign_delete(request: HttpRequest, campaign_id) -> HttpResponse:
    enforce(user=request.user, resource_type="action", resource="ui:mail:campaigns:delete", context={"path": request.path, "method": request.method})
    user: User = request.user
    camp = get_object_or_404(Campaign, id=campaign_id)
    if not _can_manage_campaign(user, camp):
        messages.error(request, "Нет прав на удаление этой кампании.")
        return redirect("campaigns")
    if request.method != "POST":
        return redirect("campaign_detail", campaign_id=camp.id)

    camp_name = camp.name
    camp_id_str = str(camp.id)
    if camp.attachment:
        try:
            camp.attachment.delete(save=False)
        except Exception:
            pass
    camp.delete()
    messages.success(request, f"Кампания «{camp_name}» удалена.")
    log_event(actor=user, verb=ActivityEvent.Verb.DELETE, entity_type="campaign", entity_id=camp_id_str, message="Удалена рассылочная кампания")
    return redirect("campaigns")


@login_required
def campaign_clone(request: HttpRequest, campaign_id) -> HttpResponse:
    """Дублировать кампанию — копирует название, тему, тело. Без получателей."""
    enforce(user=request.user, resource_type="action", resource="ui:mail:campaigns:create", context={"path": request.path, "method": request.method})
    if request.method != "POST":
        return redirect("campaign_detail", campaign_id=campaign_id)
    camp = get_object_or_404(Campaign, id=campaign_id)
    if not _can_manage_campaign(request.user, camp):
        messages.error(request, "Нет прав на клонирование этой кампании.")
        return redirect("campaign_detail", campaign_id=camp.id)
    new_camp = Campaign.objects.create(
        created_by=request.user,
        name=f"{camp.name} (копия)",
        subject=camp.subject,
        sender_name=camp.sender_name,
        body_html=camp.body_html,
        body_text=camp.body_text,
        status=Campaign.Status.DRAFT,
    )
    messages.success(request, f"Создана копия кампании «{camp.name}».")
    return redirect("campaign_detail", campaign_id=new_camp.id)
