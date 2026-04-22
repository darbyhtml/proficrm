"""
Views: список кампаний и детали кампании.
"""

from __future__ import annotations

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.files.storage import default_storage
from django.core.paginator import Paginator
from django.db.models import Count, Exists, OuterRef, Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from accounts.models import Branch, User
from companies.models import Company, CompanySphere, CompanyStatus, Contact, ContactEmail, Region
from companies.permissions import get_users_for_lists
from mailer.constants import COOLDOWN_DAYS_DEFAULT, PER_USER_DAILY_LIMIT_DEFAULT
from mailer.forms import CampaignGenerateRecipientsForm, CampaignRecipientAddForm
from mailer.mail_content import apply_signature
from mailer.models import (
    Campaign,
    CampaignQueue,
    CampaignRecipient,
    GlobalMailAccount,
    SendLog,
    SmtpBzQuota,
)
from mailer.utils import html_to_text, msk_day_bounds
from mailer.views._helpers import _can_manage_campaign, _smtp_bz_today_stats_cached
from policy.decorators import policy_required
from policy.engine import enforce

logger = logging.getLogger(__name__)


@login_required
@policy_required(resource_type="page", resource="ui:mail:campaigns")
def campaigns(request: HttpRequest) -> HttpResponse:
    # W2.1.5: inline enforce() preserved as defense-in-depth.
    enforce(
        user=request.user,
        resource_type="page",
        resource="ui:mail:campaigns",
        context={"path": request.path},
    )
    user: User = request.user
    is_admin = user.role == User.Role.ADMIN
    is_group_manager = user.role == User.Role.GROUP_MANAGER
    is_branch_director = user.role == User.Role.BRANCH_DIRECTOR
    is_sales_head = user.role == User.Role.SALES_HEAD

    qs = (
        Campaign.objects.filter(is_template=False)
        .select_related("created_by", "created_by__branch")
        .defer("body_html", "body_text", "filter_meta")
        .order_by("-created_at")
    )
    if user.role == User.Role.MANAGER:
        qs = qs.filter(created_by=user)
    elif is_branch_director or is_sales_head:
        if user.branch:
            qs = qs.filter(created_by__branch=user.branch)
        else:
            qs = qs.filter(created_by=user)

    show_creator_column = is_admin or is_group_manager or is_branch_director or is_sales_head
    filter_branch_id = request.GET.get("branch", "").strip()[:36]
    filter_manager_id = request.GET.get("manager", "").strip()[:10]
    if show_creator_column and filter_branch_id:
        try:
            import uuid as _uuid

            _uuid.UUID(filter_branch_id)
            qs = qs.filter(created_by__branch_id=filter_branch_id)
        except (ValueError, TypeError):
            pass
    if show_creator_column and filter_manager_id:
        try:
            mgr_id = int(filter_manager_id)
            qs = qs.filter(created_by_id=mgr_id)
        except (ValueError, TypeError):
            pass

    CAMPAIGNS_PER_PAGE = 30
    paginator = Paginator(qs, CAMPAIGNS_PER_PAGE)
    page = paginator.get_page(request.GET.get("page"))
    campaigns_list = list(page.object_list)

    # Аналитика для администратора
    analytics = None
    if is_admin:
        from django.utils import timezone as _tz

        now = _tz.now()
        start_day_utc, end_day_utc, msk_now = msk_day_bounds(now)
        today = msk_now.date()
        today_str = today.strftime("%Y-%m-%d")
        smtp_cfg = GlobalMailAccount.load()
        per_user_daily_limit = smtp_cfg.per_user_daily_limit or PER_USER_DAILY_LIMIT_DEFAULT
        quota = SmtpBzQuota.load()
        if quota.last_synced_at and not quota.sync_error and quota.emails_limit > 0:
            max_per_hour = quota.max_per_hour or 100
        else:
            max_per_hour = 100

        cache_key_summary = f"mail:campaigns:analytics_summary:{today_str}"
        cached_summary = cache.get(cache_key_summary)
        if isinstance(cached_summary, dict):
            total_sent_today = cached_summary.get("total_sent_today", 0)
            total_failed_today = cached_summary.get("total_failed_today", 0)
            campaigns_stats = cached_summary.get("campaigns_stats", {})
            sent_last_hour = cached_summary.get("sent_last_hour", 0)
        else:
            totals = SendLog.objects.filter(
                provider="smtp_global",
                created_at__gte=start_day_utc,
                created_at__lt=end_day_utc,
            ).aggregate(
                total_sent_today=Count("id", filter=Q(status="sent")),
                total_failed_today=Count("id", filter=Q(status="failed")),
            )
            total_sent_today = int(totals.get("total_sent_today") or 0)
            total_failed_today = int(totals.get("total_failed_today") or 0)
            campaigns_stats = Campaign.objects.aggregate(
                total=Count("id"),
                active=Count(
                    "id", filter=Q(status__in=[Campaign.Status.READY, Campaign.Status.SENDING])
                ),
                paused=Count("id", filter=Q(status=Campaign.Status.PAUSED)),
                sent=Count("id", filter=Q(status=Campaign.Status.SENT)),
            )
            sent_last_hour = SendLog.objects.filter(
                provider="smtp_global",
                status="sent",
                created_at__gte=now - _tz.timedelta(hours=1),
            ).count()
            cache.set(
                cache_key_summary,
                {
                    "total_sent_today": total_sent_today,
                    "total_failed_today": total_failed_today,
                    "campaigns_stats": campaigns_stats,
                    "sent_last_hour": sent_last_hour,
                },
                timeout=60,
            )

        global_limit = (
            quota.emails_limit
            if (quota.last_synced_at and not quota.sync_error and quota.emails_limit)
            else smtp_cfg.rate_per_day or 0
        )
        all_users = list(
            User.objects.filter(
                role__in=[
                    User.Role.MANAGER,
                    User.Role.ADMIN,
                    User.Role.BRANCH_DIRECTOR,
                    User.Role.GROUP_MANAGER,
                ]
            ).select_related("branch")
        )
        user_ids = [u.id for u in all_users]
        send_agg = (
            SendLog.objects.filter(
                provider="smtp_global",
                created_at__gte=start_day_utc,
                created_at__lt=end_day_utc,
                campaign__created_by_id__in=user_ids,
            )
            .values("campaign__created_by_id")
            .annotate(
                sent_today=Count("id", filter=Q(status="sent")),
                failed_today=Count("id", filter=Q(status="failed")),
            )
        )
        send_map = {row["campaign__created_by_id"]: row for row in send_agg}
        camp_agg = (
            Campaign.objects.filter(created_by_id__in=user_ids)
            .values("created_by_id")
            .annotate(
                campaigns_count=Count("id"),
                active_campaigns=Count(
                    "id", filter=Q(status__in=[Campaign.Status.READY, Campaign.Status.SENDING])
                ),
            )
        )
        camp_map = {row["created_by_id"]: row for row in camp_agg}
        user_stats = []
        for u in all_users:
            s = send_map.get(u.id, {})
            c = camp_map.get(u.id, {})
            sent_today_v = int(s.get("sent_today") or 0)
            failed_today_v = int(s.get("failed_today") or 0)
            campaigns_count = int(c.get("campaigns_count") or 0)
            active_campaigns = int(c.get("active_campaigns") or 0)
            remaining = (
                max(0, per_user_daily_limit - sent_today_v) if per_user_daily_limit else None
            )
            user_stats.append(
                {
                    "user": u,
                    "sent_today": sent_today_v,
                    "failed_today": failed_today_v,
                    "remaining": remaining,
                    "limit": per_user_daily_limit,
                    "campaigns_count": campaigns_count,
                    "active_campaigns": active_campaigns,
                    "is_limit_reached": per_user_daily_limit
                    and sent_today_v >= per_user_daily_limit,
                }
            )
        user_stats.sort(key=lambda x: x["sent_today"], reverse=True)

        from mailer.tasks import _is_working_hours

        is_working = _is_working_hours(now)
        next_working_time = None
        if not is_working:
            if msk_now.hour >= 18:
                next_working_time = msk_now.replace(
                    hour=9, minute=0, second=0, microsecond=0
                ) + _tz.timedelta(days=1)
            else:
                next_working_time = msk_now.replace(hour=9, minute=0, second=0, microsecond=0)
        queue_entries_all = list(
            CampaignQueue.objects.filter(
                status__in=[CampaignQueue.Status.PENDING, CampaignQueue.Status.PROCESSING]
            )
            .select_related("campaign", "campaign__created_by")
            .order_by("-priority", "queued_at")
        )
        queue_list = [
            {
                "position": idx,
                "campaign": qe.campaign,
                "status": qe.status,
                "queued_at": qe.queued_at,
                "started_at": qe.started_at,
                "next_working_time": next_working_time,
            }
            for idx, qe in enumerate(queue_entries_all, 1)
        ]
        smtp_bz_stats = {}
        if smtp_cfg.smtp_bz_api_key:
            smtp_bz_stats = _smtp_bz_today_stats_cached(
                api_key=smtp_cfg.smtp_bz_api_key, today_str=today_str
            )
        analytics = {
            "user_stats": user_stats,
            "total_sent_today": total_sent_today,
            "total_failed_today": total_failed_today,
            "global_limit": global_limit,
            "per_user_limit": per_user_daily_limit,
            "campaigns_stats": campaigns_stats,
            "sent_last_hour": sent_last_hour,
            "max_per_hour": max_per_hour,
            "queue_list": queue_list,
            "is_working_time": is_working,
            "current_time_msk": msk_now.strftime("%H:%M"),
            "smtp_bz": smtp_bz_stats,
        }

    # Информация о квоте и лимите пользователя
    quota = SmtpBzQuota.load()
    from django.utils import timezone as _tz

    now = _tz.now()
    start_day_utc, end_day_utc, now_msk = msk_day_bounds(now)
    sent_today_user = SendLog.objects.filter(
        provider="smtp_global",
        status="sent",
        campaign__created_by=user,
        created_at__gte=start_day_utc,
        created_at__lt=end_day_utc,
    ).count()

    smtp_cfg = GlobalMailAccount.load()
    per_user_daily_limit = smtp_cfg.per_user_daily_limit or PER_USER_DAILY_LIMIT_DEFAULT

    user_campaigns_count = Campaign.objects.filter(created_by=user).count()
    user_active_campaigns = Campaign.objects.filter(
        created_by=user, status__in=[Campaign.Status.READY, Campaign.Status.SENDING]
    ).count()

    user_limit_info = {
        "sent_today": sent_today_user,
        "limit": per_user_daily_limit,
        "remaining": (
            max(0, per_user_daily_limit - sent_today_user) if per_user_daily_limit else None
        ),
        "is_limit_reached": per_user_daily_limit and sent_today_user >= per_user_daily_limit,
        "campaigns_count": user_campaigns_count,
        "active_campaigns": user_active_campaigns,
    }

    from mailer.tasks import _is_working_hours

    is_working_time = _is_working_hours(now)
    msk_now = now_msk
    current_time_msk = msk_now.strftime("%H:%M")

    if quota and quota.last_synced_at and not quota.sync_error and quota.emails_limit > 0:
        global_limit = quota.emails_limit
        max_per_hour = quota.max_per_hour or 100
        emails_available = quota.emails_available or 0
    else:
        global_limit = smtp_cfg.rate_per_day
        max_per_hour = 100
        emails_available = global_limit

    sent_today = SendLog.objects.filter(
        provider="smtp_global",
        status="sent",
        created_at__gte=start_day_utc,
        created_at__lt=end_day_utc,
    ).count()
    sent_last_hour = SendLog.objects.filter(
        provider="smtp_global", status="sent", created_at__gte=now - _tz.timedelta(hours=1)
    ).count()

    campaign_ids = [c.id for c in campaigns_list]
    queue_entries = {
        str(q.campaign_id): q
        for q in CampaignQueue.objects.filter(campaign_id__in=campaign_ids).select_related(
            "campaign"
        )
    }

    rec_agg = (
        CampaignRecipient.objects.filter(campaign_id__in=campaign_ids)
        .values("campaign_id")
        .annotate(
            sent=Count("id", filter=Q(status=CampaignRecipient.Status.SENT)),
            pending=Count("id", filter=Q(status=CampaignRecipient.Status.PENDING)),
            failed=Count("id", filter=Q(status=CampaignRecipient.Status.FAILED)),
            total=Count("id"),
        )
    )
    counts_by_campaign = {str(r["campaign_id"]): r for r in rec_agg}
    for camp in campaigns_list:
        c = counts_by_campaign.get(str(camp.id), {})
        camp.counts = {
            "sent": int(c.get("sent") or 0),
            "pending": int(c.get("pending") or 0),
            "failed": int(c.get("failed") or 0),
            "total": int(c.get("total") or 0),
        }

    creator_ids = [c.created_by_id for c in campaigns_list if getattr(c, "created_by_id", None)]
    creator_ids = list(dict.fromkeys([cid for cid in creator_ids if cid]))
    creator_sent_map = {}
    if creator_ids:
        creator_sent_map = {
            row["campaign__created_by_id"]: int(row["sent_today"] or 0)
            for row in (
                SendLog.objects.filter(
                    provider="smtp_global",
                    status="sent",
                    created_at__gte=start_day_utc,
                    created_at__lt=end_day_utc,
                    campaign__created_by_id__in=creator_ids,
                )
                .values("campaign__created_by_id")
                .annotate(sent_today=Count("id"))
            )
        }

    for camp in campaigns_list:
        camp_pause_reasons = []
        if camp.status in (Campaign.Status.READY, Campaign.Status.SENDING):
            if not is_working_time:
                camp_pause_reasons.append(
                    f"Вне рабочего времени (текущее время МСК: {current_time_msk}, рабочие часы: 9:00-18:00 МСК)"
                )
            camp_sent_today = (
                creator_sent_map.get(camp.created_by_id, 0) if camp.created_by_id else 0
            )
            if per_user_daily_limit and camp_sent_today >= per_user_daily_limit:
                camp_pause_reasons.append(
                    f"Достигнут лимит отправки для аккаунта: {camp_sent_today}/{per_user_daily_limit} писем в день"
                )
            if emails_available <= 0:
                camp_pause_reasons.append(
                    f"Квота исчерпана: доступно {emails_available} из {global_limit} писем"
                )
            elif sent_today >= global_limit:
                camp_pause_reasons.append(
                    f"Достигнут глобальный дневной лимит: {sent_today}/{global_limit} писем"
                )
            if sent_last_hour >= max_per_hour:
                camp_pause_reasons.append(
                    f"Достигнут лимит в час: {sent_last_hour}/{max_per_hour} писем"
                )
            if not smtp_cfg.is_enabled:
                camp_pause_reasons.append("SMTP не включен администратором")

        camp.pause_reasons = camp_pause_reasons
        camp.is_working_time = is_working_time
        camp.current_time_msk = current_time_msk
        camp.queue_entry = queue_entries.get(str(camp.id))

    campaigns_grouped = []
    if show_creator_column and campaigns_list:
        from collections import OrderedDict

        by_branch = OrderedDict()
        for camp in campaigns_list:
            branch = getattr(camp.created_by, "branch", None) if camp.created_by else None
            branch_key = (branch.id if branch else None, branch.name if branch else "Без филиала")
            creator = camp.created_by
            creator_id = creator.id if creator else 0
            if branch_key not in by_branch:
                by_branch[branch_key] = OrderedDict()
            if creator_id not in by_branch[branch_key]:
                by_branch[branch_key][creator_id] = (creator, [])
            by_branch[branch_key][creator_id][1].append(camp)
        for (branch_id, branch_name), creators in by_branch.items():
            managers = [{"user": u, "campaigns": camps} for u, camps in creators.values()]
            campaigns_grouped.append(
                {"branch_id": branch_id, "branch_name": branch_name, "managers": managers}
            )

    branches_for_filter = []
    managers_for_filter = []
    if show_creator_column:
        if is_admin or is_group_manager:
            branches_for_filter = list(Branch.objects.all().order_by("name"))
        elif user.branch:
            branches_for_filter = [user.branch]
        manager_ids = list(qs.values_list("created_by_id", flat=True).distinct())
        manager_ids = [x for x in manager_ids if x]
        if manager_ids:
            managers_for_filter = list(
                User.objects.filter(id__in=manager_ids).order_by(
                    "first_name", "last_name", "username"
                )
            )

    return render(
        request,
        "ui/mail/campaigns.html",
        {
            "campaigns": campaigns_list,
            "campaigns_grouped": campaigns_grouped,
            "page": page,
            "paginator": paginator,
            "is_admin": is_admin,
            "is_group_manager": is_group_manager,
            "is_branch_director": is_branch_director,
            "is_sales_head": is_sales_head,
            "analytics": analytics,
            "quota": quota,
            "user_limit_info": user_limit_info,
            "show_creator_column": show_creator_column,
            "is_working_time": is_working_time,
            "current_time_msk": current_time_msk,
            "filter_branch_id": filter_branch_id,
            "filter_manager_id": filter_manager_id,
            "branches_for_filter": branches_for_filter,
            "managers_for_filter": managers_for_filter,
        },
    )


@login_required
@policy_required(resource_type="page", resource="ui:mail:campaigns:detail")
def campaign_detail(request: HttpRequest, campaign_id) -> HttpResponse:
    # W2.1.5: inline enforce() preserved as defense-in-depth.
    enforce(
        user=request.user,
        resource_type="page",
        resource="ui:mail:campaigns:detail",
        context={"path": request.path},
    )
    user: User = request.user
    camp = get_object_or_404(
        Campaign.objects.select_related("created_by"),
        id=campaign_id,
    )
    if not _can_manage_campaign(user, camp):
        messages.error(request, "Доступ запрещён.")
        return redirect("campaigns")

    smtp_cfg = GlobalMailAccount.load()
    counts = camp.recipients.aggregate(
        pending=Count("id", filter=Q(status=CampaignRecipient.Status.PENDING)),
        sent=Count("id", filter=Q(status=CampaignRecipient.Status.SENT)),
        failed=Count("id", filter=Q(status=CampaignRecipient.Status.FAILED)),
        unsub=Count("id", filter=Q(status=CampaignRecipient.Status.UNSUBSCRIBED)),
        total=Count("id"),
    )
    counts["sent_log"] = (
        SendLog.objects.filter(
            campaign=camp, provider="smtp_global", status="sent", recipient__isnull=False
        ).aggregate(n=Count("recipient_id", distinct=True))["n"]
        or 0
    )

    per_page_param = request.GET.get("per_page", "").strip()
    if per_page_param:
        try:
            per_page = int(per_page_param)
            if per_page in [25, 50, 100, 200]:
                request.session["campaign_recipients_per_page"] = per_page
            else:
                per_page = request.session.get("campaign_recipients_per_page", 50)
        except (ValueError, TypeError):
            per_page = request.session.get("campaign_recipients_per_page", 50)
    else:
        per_page = request.session.get("campaign_recipients_per_page", 50)

    view = (request.GET.get("view") or "").strip().lower()
    allowed_views = {"pending", "sent", "failed", "unsub", "sent_log", "all"}
    if view not in allowed_views:
        view = "pending" if counts["pending"] > 0 else "all"

    all_recipients_qs = camp.recipients.all().order_by("company_id", "-updated_at")

    if view == "pending":
        all_recipients_qs = all_recipients_qs.filter(status=CampaignRecipient.Status.PENDING)
    elif view == "sent":
        all_recipients_qs = all_recipients_qs.filter(status=CampaignRecipient.Status.SENT)
    elif view == "failed":
        all_recipients_qs = all_recipients_qs.filter(status=CampaignRecipient.Status.FAILED)
    elif view == "unsub":
        all_recipients_qs = all_recipients_qs.filter(status=CampaignRecipient.Status.UNSUBSCRIBED)
    elif view == "sent_log":
        sent_exists = SendLog.objects.filter(
            campaign=camp,
            provider="smtp_global",
            status="sent",
            recipient_id=OuterRef("id"),
        )
        all_recipients_qs = all_recipients_qs.annotate(_sent_log=Exists(sent_exists)).filter(
            _sent_log=True
        )

    paginator = Paginator(all_recipients_qs, per_page)
    page = paginator.get_page(request.GET.get("page"))
    page_recipients = list(page.object_list)

    company_ids = [r.company_id for r in page_recipients if r.company_id]
    companies_map = {}
    if company_ids:
        companies_map = {
            str(c.id): c
            for c in Company.objects.filter(id__in=company_ids).only(
                "id", "name", "contact_name", "contact_position"
            )
        }

    contact_ids = [r.contact_id for r in page_recipients if r.contact_id]
    contacts_map = {}
    if contact_ids:
        contacts_map = {
            str(c.id): c
            for c in Contact.objects.filter(id__in=contact_ids).only(
                "id", "first_name", "last_name", "position"
            )
        }

    recipients_by_company = {}
    for r in page_recipients:
        company_id = str(r.company_id) if r.company_id else "no_company"
        if company_id not in recipients_by_company:
            recipients_by_company[company_id] = {
                "company": companies_map.get(company_id) if company_id != "no_company" else None,
                "recipients": [],
            }
        if r.contact_id and str(r.contact_id) in contacts_map:
            r.contact = contacts_map[str(r.contact_id)]
        if r.company_id and str(r.company_id) in companies_map:
            r.company = companies_map[str(r.company_id)]
        recipients_by_company[company_id]["recipients"].append(r)

    params = request.GET.copy()
    params.pop("page", None)
    if per_page != 50:
        params["per_page"] = str(per_page)
    qs_no_page = params.urlencode()
    recent = page_recipients

    from zoneinfo import ZoneInfo

    from django.utils import timezone as _tz

    from mailer.tasks import _is_working_hours

    is_admin = user.role == User.Role.ADMIN
    quota = SmtpBzQuota.load() if is_admin else None
    if (
        is_admin
        and quota
        and quota.last_synced_at
        and not quota.sync_error
        and quota.emails_limit > 0
    ):
        global_limit = quota.emails_limit
        max_per_hour = quota.max_per_hour or 100
        emails_available = quota.emails_available or 0
    else:
        global_limit = smtp_cfg.rate_per_day
        max_per_hour = 100
        emails_available = global_limit

    now = _tz.now()
    start_day_utc, end_day_utc, now_msk = msk_day_bounds(now)
    _sl_stats = SendLog.objects.filter(
        provider="smtp_global",
        status="sent",
        created_at__gte=start_day_utc,
        created_at__lt=end_day_utc,
    ).aggregate(
        sent_today=Count("id"),
        sent_today_user=Count("id", filter=Q(campaign__created_by=user)),
    )
    sent_today = int(_sl_stats["sent_today"] or 0)
    sent_today_user = int(_sl_stats["sent_today_user"] or 0)
    sent_last_hour = SendLog.objects.filter(
        provider="smtp_global",
        status="sent",
        created_at__gte=now - _tz.timedelta(hours=1),
    ).count()

    per_user_daily_limit = smtp_cfg.per_user_daily_limit or PER_USER_DAILY_LIMIT_DEFAULT
    is_working_time = _is_working_hours(now)

    user_campaigns_count = Campaign.objects.filter(created_by=user).count()
    user_active_campaigns = Campaign.objects.filter(
        created_by=user, status__in=[Campaign.Status.READY, Campaign.Status.SENDING]
    ).count()

    msk_now = now_msk
    current_time_msk = msk_now.strftime("%H:%M")

    pause_reasons = []
    if not is_working_time:
        pause_reasons.append(
            f"Вне рабочего времени (текущее время МСК: {current_time_msk}, рабочие часы: 9:00-18:00 МСК)"
        )
    if per_user_daily_limit and sent_today_user >= per_user_daily_limit:
        pause_reasons.append(
            f"Достигнут лимит отправки для вашего аккаунта: {sent_today_user}/{per_user_daily_limit} писем в день"
        )
    if emails_available <= 0:
        pause_reasons.append(
            f"Квота исчерпана: доступно {emails_available} из {global_limit} писем"
        )
    elif sent_today >= global_limit:
        pause_reasons.append(
            f"Достигнут глобальный дневной лимит: {sent_today}/{global_limit} писем"
        )
    if sent_last_hour >= max_per_hour:
        pause_reasons.append(f"Достигнут лимит в час: {sent_last_hour}/{max_per_hour} писем")
    if not smtp_cfg.is_enabled:
        pause_reasons.append("SMTP не включен администратором")

    _sl_agg = SendLog.objects.filter(
        provider="smtp_global",
        created_at__gte=start_day_utc,
        created_at__lt=end_day_utc,
    ).aggregate(
        failed_today=Count("id", filter=Q(status="failed")),
        failed_today_campaign=Count("id", filter=Q(status="failed", campaign=camp)),
    )
    send_stats = {
        "sent_today": sent_today,
        "sent_today_user": sent_today_user,
        "sent_last_hour": sent_last_hour,
        "failed_today": int(_sl_agg["failed_today"] or 0),
        "failed_today_campaign": int(_sl_agg["failed_today_campaign"] or 0),
    }

    recent_errors = (
        SendLog.objects.filter(campaign=camp, provider="smtp_global", status="failed")
        .select_related("recipient")
        .order_by("-created_at")[:10]
    )

    error_types = {}
    for error_log in SendLog.objects.filter(
        campaign=camp, provider="smtp_global", status="failed"
    ).order_by("-created_at")[:100]:
        error_msg = (error_log.error or "").strip()
        if not error_msg:
            error_msg = "Неизвестная ошибка"
        if "Connection timed out" in error_msg or "timeout" in error_msg.lower():
            error_key = "Connection timeout"
        elif "DNS error" in error_msg or "Domain name not found" in error_msg:
            error_key = "DNS error"
        elif "No route to host" in error_msg:
            error_key = "No route to host"
        elif "blocked" in error_msg.lower() or "security" in error_msg.lower():
            error_key = "Blocked by security"
        elif "invalid mailbox" in error_msg.lower() or "user not found" in error_msg.lower():
            error_key = "Invalid mailbox / User not found"
        elif "out of storage" in error_msg.lower() or "OverQuota" in error_msg:
            error_key = "Mailbox full"
        elif "Unrouteable address" in error_msg:
            error_key = "Unrouteable address"
        else:
            error_key = "Other errors"

        if error_key not in error_types:
            error_types[error_key] = {"count": 0, "examples": []}
        error_types[error_key]["count"] += 1
        if len(error_types[error_key]["examples"]) < 3:
            error_types[error_key]["examples"].append(
                {
                    "email": error_log.recipient.email if error_log.recipient else "—",
                    "error": error_msg[:200],
                    "time": error_log.created_at,
                }
            )

    attachment_size = None
    if camp.attachment and camp.attachment.name and default_storage.exists(camp.attachment.name):
        try:
            attachment_size = camp.attachment.size
        except OSError:
            pass

    preview_html = camp.body_html or ""
    if preview_html:
        auto_plain = html_to_text(preview_html)
        preview_html, _ = apply_signature(
            user=user, body_html=preview_html, body_text=auto_plain or camp.body_text or ""
        )

    selected_regions: list[str] = []
    try:
        meta_region = (camp.filter_meta or {}).get("region")
        if isinstance(meta_region, list):
            selected_regions = [str(x) for x in meta_region if str(x).strip()]
        elif isinstance(meta_region, (str, int)):
            meta_region_str = str(meta_region).strip()
            selected_regions = [meta_region_str] if meta_region_str else []
    except Exception:
        selected_regions = []

    qe = CampaignQueue.objects.filter(campaign=camp).first()

    return render(
        request,
        "ui/mail/campaign_detail.html",
        {
            "campaign": camp,
            "now": now,
            "smtp_cfg": smtp_cfg,
            "is_working_time": is_working_time,
            "current_time_msk": current_time_msk,
            "pause_reasons": pause_reasons,
            "send_stats": send_stats,
            "per_user_daily_limit": per_user_daily_limit,
            "sent_today_user": sent_today_user,
            "sent_today": sent_today,
            "rate_per_day": global_limit,
            "rate_per_hour": max_per_hour,
            "user_campaigns_count": user_campaigns_count,
            "user_active_campaigns": user_active_campaigns,
            "is_admin": is_admin,
            "preview_html": preview_html,
            "emails_available": emails_available,
            "counts": counts,
            "recent": recent,
            "smtp_from_email": (smtp_cfg.from_email or smtp_cfg.smtp_username or "").strip(),
            "smtp_from_name_default": (smtp_cfg.from_name or "CRM ПРОФИ").strip(),
            "recipient_add_form": CampaignRecipientAddForm(),
            "generate_form": CampaignGenerateRecipientsForm(),
            "branches": (
                Branch.objects.order_by("name")
                if (user.role == User.Role.ADMIN or user.role == User.Role.GROUP_MANAGER)
                else (
                    Branch.objects.filter(id=user.branch_id)
                    if user.branch_id
                    else Branch.objects.none()
                )
            ),
            "responsibles": get_users_for_lists(request.user),
            "statuses": CompanyStatus.objects.order_by("name"),
            "spheres": CompanySphere.objects.order_by("name"),
            "regions": Region.objects.order_by("name"),
            "selected_regions": selected_regions,
            "user": user,
            "recipients_by_company": recipients_by_company,
            "page": page,
            "qs": qs_no_page,
            "per_page": per_page,
            "can_delete_campaign": _can_manage_campaign(user, camp),
            "view": view,
            "recent_errors": recent_errors,
            "error_types": error_types,
            "quota": quota,
            "queue_entry": qe,
            "deferred_until": (getattr(qe, "deferred_until", None) if qe else None),
            "defer_reason": (getattr(qe, "defer_reason", None) or "") if qe else "",
            "is_deferred_daily_limit": bool(
                qe
                and (getattr(qe, "defer_reason", None) or "") == "daily_limit"
                and getattr(qe, "deferred_until", None)
                and qe.deferred_until > timezone.now()
            ),
            "attachment_ext": (
                (
                    (camp.attachment_original_name or camp.attachment.name).split(".")[-1].upper()
                    if (camp.attachment_original_name or camp.attachment)
                    and "." in (camp.attachment_original_name or camp.attachment.name)
                    else ""
                )
                if camp.attachment
                else ""
            ),
            "attachment_filename": (
                camp.attachment_original_name
                or (
                    camp.attachment.name.split("/")[-1]
                    if camp.attachment and camp.attachment.name
                    else ""
                )
            ),
            "attachment_size": attachment_size,
        },
    )
