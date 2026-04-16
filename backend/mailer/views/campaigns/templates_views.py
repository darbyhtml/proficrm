"""
Views: шаблоны кампаний — сохранение, создание из шаблона, удаление, список.
"""
from __future__ import annotations

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from accounts.models import User
from mailer.models import Campaign
from accounts.permissions import require_admin
from policy.engine import enforce
from mailer.views._helpers import _can_manage_campaign

logger = logging.getLogger(__name__)


@login_required
def campaign_save_as_template(request: HttpRequest, campaign_id) -> HttpResponse:
    """Сохранить кампанию как шаблон (копия с is_template=True, без получателей)."""
    enforce(user=request.user, resource_type="action", resource="ui:mail:campaigns:create", context={"path": request.path, "method": request.method})
    if request.method != "POST":
        return redirect("campaign_detail", campaign_id=campaign_id)
    camp = get_object_or_404(Campaign, id=campaign_id)
    if not _can_manage_campaign(request.user, camp):
        messages.error(request, "Нет прав на сохранение этой кампании как шаблон.")
        return redirect("campaign_detail", campaign_id=camp.id)
    if camp.is_template:
        messages.info(request, "Эта кампания уже является шаблоном.")
        return redirect("campaign_templates")
    Campaign.objects.create(
        created_by=request.user,
        name=camp.name,
        subject=camp.subject,
        sender_name=camp.sender_name,
        body_html=camp.body_html,
        body_text=camp.body_text,
        status=Campaign.Status.DRAFT,
        is_template=True,
    )
    messages.success(request, f"Шаблон «{camp.name}» сохранён.")
    return redirect("campaign_templates")


@login_required
def campaign_create_from_template(request: HttpRequest, template_id) -> HttpResponse:
    """Создать новую кампанию на основе шаблона."""
    enforce(user=request.user, resource_type="action", resource="ui:mail:campaigns:create", context={"path": request.path, "method": request.method})
    if request.method != "POST":
        return redirect("campaign_templates")
    tmpl = get_object_or_404(Campaign, id=template_id, is_template=True)
    new_camp = Campaign.objects.create(
        created_by=request.user,
        name=tmpl.name,
        subject=tmpl.subject,
        sender_name=tmpl.sender_name,
        body_html=tmpl.body_html,
        body_text=tmpl.body_text,
        status=Campaign.Status.DRAFT,
        is_template=False,
    )
    messages.success(request, f"Кампания «{new_camp.name}» создана из шаблона.")
    return redirect("campaign_detail", campaign_id=new_camp.id)


@login_required
def campaign_template_delete(request: HttpRequest, template_id) -> HttpResponse:
    """Удалить шаблон."""
    if request.method != "POST":
        return redirect("campaign_templates")
    tmpl = get_object_or_404(Campaign, id=template_id, is_template=True)
    user: User = request.user
    is_admin = require_admin(user)
    if not is_admin and tmpl.created_by_id != user.id:
        messages.error(request, "Нет прав для удаления этого шаблона.")
        return redirect("campaign_templates")
    name = tmpl.name
    tmpl.delete()
    messages.success(request, f"Шаблон «{name}» удалён.")
    return redirect("campaign_templates")


@login_required
def campaign_templates(request: HttpRequest) -> HttpResponse:
    """Список шаблонов писем."""
    enforce(user=request.user, resource_type="page", resource="ui:mail:campaigns", context={"path": request.path})
    user: User = request.user
    is_admin = require_admin(user)
    templates_qs = Campaign.objects.filter(is_template=True).select_related("created_by").order_by("-created_at")
    return render(request, "ui/mail/templates.html", {
        "templates": templates_qs,
        "is_admin": is_admin,
    })
