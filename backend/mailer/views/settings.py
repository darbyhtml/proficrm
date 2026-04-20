"""
Views для настроек почты: подпись, SMTP настройки, admin-панель, quota poll.
"""

from __future__ import annotations

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db.models import Count, Q
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from accounts.permissions import require_admin
from mailer.constants import PER_USER_DAILY_LIMIT_DEFAULT
from mailer.forms import EmailSignatureForm, GlobalMailAccountForm
from mailer.models import (
    Campaign,
    CampaignQueue,
    CampaignRecipient,
    GlobalMailAccount,
    SendLog,
    SmtpBzQuota,
)
from mailer.utils import msk_day_bounds
from mailer.views._helpers import (
    _can_manage_campaign,
    _contains_links,
    _dispatch_test_email,
    _smtp_bz_today_stats_cached,
)
from policy.engine import enforce

logger = logging.getLogger(__name__)


@login_required
def mail_signature(request: HttpRequest) -> HttpResponse:
    """Настройка подписи (персональная, для всех пользователей)."""
    enforce(
        user=request.user,
        resource_type="page",
        resource="ui:mail:signature",
        context={"path": request.path},
    )
    user: User = request.user
    if request.method == "POST":
        form = EmailSignatureForm(request.POST)
        if form.is_valid():
            # clean_signature_html() already runs sanitize_email_html + _normalize_email_img_tags
            html = (form.cleaned_data.get("signature_html") or "").strip()
            user.email_signature_html = html
            user.save(update_fields=["email_signature_html"])
            messages.success(request, "Подпись сохранена.")
            if _contains_links(html):
                messages.warning(
                    request,
                    "В подписи есть ссылки. Такие письма иногда попадают в спам или блокируются почтовиками. "
                    "Это не запрещено, просто предупреждение.",
                )
            return redirect("mail_signature")
    else:
        form = EmailSignatureForm(
            initial={"signature_html": (getattr(user, "email_signature_html", "") or "")}
        )
    return render(request, "ui/mail/signature.html", {"form": form})


@login_required
def mail_settings(request: HttpRequest) -> HttpResponse:
    """Настройки SMTP. Редактирует только администратор (глобально для всей CRM)."""
    enforce(
        user=request.user,
        resource_type="page",
        resource="ui:mail:settings",
        context={"path": request.path},
    )
    user: User = request.user
    is_admin = require_admin(user)
    cfg = GlobalMailAccount.load()

    if request.method == "POST":
        enforce(
            user=request.user,
            resource_type="action",
            resource="ui:mail:settings:update",
            context={"path": request.path, "method": request.method},
        )
        if not is_admin:
            messages.error(request, "Доступ запрещён.")
            return redirect("mail_settings")
        form = GlobalMailAccountForm(request.POST, instance=cfg)
        if form.is_valid():
            password = (form.cleaned_data.get("smtp_password") or "").strip()
            if password:
                from django.conf import settings

                if not getattr(settings, "MAILER_FERNET_KEY", ""):
                    messages.error(request, "MAILER_FERNET_KEY не задан. Нельзя сохранить пароль.")
                    return redirect("mail_settings")
            old_api_key = cfg.smtp_bz_api_key if cfg.pk else None
            form.save()
            cfg.refresh_from_db()
            new_api_key = cfg.smtp_bz_api_key
            if new_api_key and new_api_key != old_api_key:
                from mailer.tasks import sync_smtp_bz_quota

                try:
                    sync_smtp_bz_quota.delay()
                    messages.info(request, "API ключ сохранен. Запущена синхронизация квоты...")
                except Exception as e:
                    logger.error(f"Ошибка при запуске синхронизации квоты: {e}", exc_info=True)
                    messages.warning(
                        request, "API ключ сохранен, но не удалось запустить синхронизацию."
                    )
            if "test_send" in request.POST:
                _dispatch_test_email(request, cfg, x_tag="test:mail_settings")
                return redirect("mail_settings")
            messages.success(request, "Настройки SMTP сохранены.")
            return redirect("mail_settings")
    else:
        form = GlobalMailAccountForm(instance=cfg) if is_admin else None

    from django.conf import settings

    key_missing = not bool(getattr(settings, "MAILER_FERNET_KEY", "") or "")
    quota = SmtpBzQuota.load()
    has_api_key = bool(cfg.smtp_bz_api_key)
    api_connected = bool(has_api_key and quota.last_synced_at and not quota.sync_error)
    api_pending = bool(has_api_key and not quota.last_synced_at and not quota.sync_error)

    return render(
        request,
        "ui/mail/settings.html",
        {
            "quota": quota,
            "api_connected": api_connected,
            "api_pending": api_pending,
            "has_api_key": has_api_key,
            "form": form,
            "account": cfg,
            "key_missing": key_missing,
            "is_admin": is_admin,
            "user_sender_email": (user.email or "").strip(),
        },
    )


@login_required
def mail_admin(request: HttpRequest) -> HttpResponse:
    """
    Админ-панель рассылок с табами: Обзор, Настройки SMTP, Лимиты, Аналитика, Очередь.
    Доступна только администратору.
    """
    enforce(
        user=request.user,
        resource_type="page",
        resource="ui:mail:admin",
        context={"path": request.path},
    )
    user: User = request.user
    is_admin = require_admin(user)
    if not is_admin:
        messages.error(request, "Доступ запрещён.")
        return redirect("campaigns")

    active_tab = request.GET.get("tab", "overview")
    if active_tab not in ("overview", "settings", "limits", "analytics", "queue"):
        active_tab = "overview"

    from django.utils import timezone as _tz

    now = _tz.now()
    start_day_utc, end_day_utc, msk_now = msk_day_bounds(now)
    today = msk_now.date()
    today_str = today.strftime("%Y-%m-%d")

    smtp_cfg = GlobalMailAccount.load()
    quota = SmtpBzQuota.load()
    per_user_daily_limit = smtp_cfg.per_user_daily_limit or PER_USER_DAILY_LIMIT_DEFAULT

    # --- Данные для таба "Обзор" ---
    overview_data = None
    if active_tab == "overview":
        totals = SendLog.objects.filter(
            provider="smtp_global",
            created_at__gte=start_day_utc,
            created_at__lt=end_day_utc,
        ).aggregate(
            total_sent_today=Count("id", filter=Q(status="sent")),
            total_failed_today=Count("id", filter=Q(status="failed")),
        )
        campaigns_stats = Campaign.objects.aggregate(
            total=Count("id"),
            active=Count(
                "id", filter=Q(status__in=[Campaign.Status.READY, Campaign.Status.SENDING])
            ),
            paused=Count("id", filter=Q(status=Campaign.Status.PAUSED)),
            sent=Count("id", filter=Q(status=Campaign.Status.SENT)),
        )
        sent_last_hour = SendLog.objects.filter(
            provider="smtp_global", status="sent", created_at__gte=now - _tz.timedelta(hours=1)
        ).count()
        max_per_hour = (
            quota.max_per_hour or 100 if quota.last_synced_at and not quota.sync_error else 100
        )

        smtp_bz_stats = {}
        if smtp_cfg.smtp_bz_api_key:
            smtp_bz_stats = _smtp_bz_today_stats_cached(
                api_key=smtp_cfg.smtp_bz_api_key, today_str=today_str
            )

        overview_data = {
            "total_sent_today": int(totals.get("total_sent_today") or 0),
            "total_failed_today": int(totals.get("total_failed_today") or 0),
            "campaigns_stats": campaigns_stats,
            "sent_last_hour": sent_last_hour,
            "max_per_hour": max_per_hour,
            "quota": quota,
            "smtp_bz": smtp_bz_stats,
            "smtp_enabled": smtp_cfg.is_enabled,
        }

    # --- Данные для таба "Настройки SMTP" ---
    settings_form = None
    settings_data = None
    if active_tab == "settings":
        cfg = GlobalMailAccount.load()
        if request.method == "POST" and "update_smtp" in request.POST:
            enforce(
                user=request.user,
                resource_type="action",
                resource="ui:mail:settings:update",
                context={"path": request.path, "method": request.method},
            )
            form = GlobalMailAccountForm(request.POST, instance=cfg)
            if form.is_valid():
                password = (form.cleaned_data.get("smtp_password") or "").strip()
                if password:
                    from django.conf import settings

                    if not getattr(settings, "MAILER_FERNET_KEY", ""):
                        messages.error(
                            request, "MAILER_FERNET_KEY не задан. Нельзя сохранить пароль."
                        )
                        settings_form = form
                    else:
                        old_api_key = cfg.smtp_bz_api_key if cfg.pk else None
                        form.save()
                        cfg.refresh_from_db()
                        new_api_key = cfg.smtp_bz_api_key
                        if new_api_key and new_api_key != old_api_key:
                            from mailer.tasks import sync_smtp_bz_quota

                            try:
                                sync_smtp_bz_quota.delay()
                                messages.info(
                                    request, "API ключ сохранен. Запущена синхронизация квоты..."
                                )
                            except Exception as e:
                                logger.error(
                                    f"Ошибка при запуске синхронизации квоты: {e}", exc_info=True
                                )
                        messages.success(request, "Настройки SMTP сохранены.")
                        return redirect(f"{reverse('mail_admin')}?tab=settings")
                else:
                    old_api_key = cfg.smtp_bz_api_key if cfg.pk else None
                    form.save()
                    cfg.refresh_from_db()
                    new_api_key = cfg.smtp_bz_api_key
                    if new_api_key and new_api_key != old_api_key:
                        from mailer.tasks import sync_smtp_bz_quota

                        try:
                            sync_smtp_bz_quota.delay()
                            messages.info(
                                request, "API ключ сохранен. Запущена синхронизация квоты..."
                            )
                        except Exception as e:
                            logger.error(
                                f"Ошибка при запуске синхронизации квоты: {e}", exc_info=True
                            )
                    if "test_send" in request.POST:
                        cfg.refresh_from_db()
                        _dispatch_test_email(request, cfg, x_tag="test:mail_admin")
                    messages.success(request, "Настройки SMTP сохранены.")
                    return redirect(f"{reverse('mail_admin')}?tab=settings")
            else:
                settings_form = form
        elif (
            request.method == "POST"
            and "test_send" in request.POST
            and "update_smtp" not in request.POST
        ):
            enforce(
                user=request.user,
                resource_type="action",
                resource="ui:mail:settings:update",
                context={"path": request.path, "method": request.method},
            )
            cfg = GlobalMailAccount.load()
            _dispatch_test_email(request, cfg, x_tag="test:mail_admin")
            return redirect(f"{reverse('mail_admin')}?tab=settings")
        else:
            settings_form = GlobalMailAccountForm(instance=cfg)

        from django.conf import settings

        key_missing = not bool(getattr(settings, "MAILER_FERNET_KEY", "") or "")
        has_api_key = bool(cfg.smtp_bz_api_key)
        api_connected = bool(has_api_key and quota.last_synced_at and not quota.sync_error)
        api_pending = bool(has_api_key and not quota.last_synced_at and not quota.sync_error)

        settings_data = {
            "form": settings_form,
            "account": cfg,
            "key_missing": key_missing,
            "quota": quota,
            "api_connected": api_connected,
            "api_pending": api_pending,
            "has_api_key": has_api_key,
            "user_sender_email": (user.email or "").strip(),
        }

    # --- Данные для таба "Лимиты" ---
    limits_data = None
    if active_tab == "limits":
        limits_data = {
            "quota": quota,
            "per_user_daily_limit": per_user_daily_limit,
            "smtp_cfg": smtp_cfg,
        }
        if request.method == "POST" and "update_limits" in request.POST:
            enforce(
                user=request.user,
                resource_type="action",
                resource="ui:mail:settings:update",
                context={"path": request.path, "method": request.method},
            )
            new_limit = request.POST.get("per_user_daily_limit", "").strip()
            try:
                limit_val = int(new_limit) if new_limit else None
                if limit_val is not None and limit_val > 0:
                    smtp_cfg.per_user_daily_limit = limit_val
                    smtp_cfg.save(update_fields=["per_user_daily_limit"])
                    messages.success(
                        request, f"Дневной лимит пользователя установлен: {limit_val} писем."
                    )
                else:
                    smtp_cfg.per_user_daily_limit = None
                    smtp_cfg.save(update_fields=["per_user_daily_limit"])
                    messages.success(
                        request, "Дневной лимит пользователя сброшен (без ограничений)."
                    )
            except ValueError:
                messages.error(request, "Некорректное значение лимита.")
            return redirect(f"{reverse('mail_admin')}?tab=limits")

    # --- Данные для таба "Аналитика" ---
    analytics_data = None
    if active_tab == "analytics":
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
            sent_today = int(s.get("sent_today") or 0)
            failed_today = int(s.get("failed_today") or 0)
            campaigns_count = int(c.get("campaigns_count") or 0)
            active_campaigns = int(c.get("active_campaigns") or 0)
            remaining = max(0, per_user_daily_limit - sent_today) if per_user_daily_limit else None
            user_stats.append(
                {
                    "user": u,
                    "sent_today": sent_today,
                    "failed_today": failed_today,
                    "remaining": remaining,
                    "limit": per_user_daily_limit,
                    "campaigns_count": campaigns_count,
                    "active_campaigns": active_campaigns,
                    "is_limit_reached": per_user_daily_limit and sent_today >= per_user_daily_limit,
                }
            )
        user_stats.sort(key=lambda x: x["sent_today"], reverse=True)

        totals = SendLog.objects.filter(
            provider="smtp_global",
            created_at__gte=start_day_utc,
            created_at__lt=end_day_utc,
        ).aggregate(
            total_sent_today=Count("id", filter=Q(status="sent")),
            total_failed_today=Count("id", filter=Q(status="failed")),
        )

        smtp_bz_stats = {}
        if smtp_cfg.smtp_bz_api_key:
            smtp_bz_stats = _smtp_bz_today_stats_cached(
                api_key=smtp_cfg.smtp_bz_api_key, today_str=today_str
            )

        analytics_data = {
            "user_stats": user_stats,
            "total_sent_today": int(totals.get("total_sent_today") or 0),
            "total_failed_today": int(totals.get("total_failed_today") or 0),
            "smtp_bz": smtp_bz_stats,
        }

    # --- Данные для таба "Очередь" ---
    queue_data = None
    if active_tab == "queue":
        from mailer.tasks import _is_working_hours

        queue_entries_all = list(
            CampaignQueue.objects.filter(
                status__in=[CampaignQueue.Status.PENDING, CampaignQueue.Status.PROCESSING]
            )
            .select_related("campaign", "campaign__created_by")
            .order_by("-priority", "queued_at")
        )

        is_working = _is_working_hours(now)
        next_working_time = None
        if not is_working:
            if msk_now.hour >= 18:
                next_working_time = msk_now.replace(
                    hour=9, minute=0, second=0, microsecond=0
                ) + _tz.timedelta(days=1)
            else:
                next_working_time = msk_now.replace(hour=9, minute=0, second=0, microsecond=0)

        queue_list = []
        from mailer.constants import DEFER_REASONS

        defer_reason_map = dict(DEFER_REASONS)

        for idx, queue_entry in enumerate(queue_entries_all, 1):
            defer_reason_text = (
                defer_reason_map.get(queue_entry.defer_reason, queue_entry.defer_reason)
                if queue_entry.defer_reason
                else ""
            )
            queue_list.append(
                {
                    "position": idx,
                    "campaign": queue_entry.campaign,
                    "status": queue_entry.status,
                    "queued_at": queue_entry.queued_at,
                    "started_at": queue_entry.started_at,
                    "deferred_until": queue_entry.deferred_until,
                    "defer_reason": queue_entry.defer_reason,
                    "defer_reason_text": defer_reason_text,
                    "next_working_time": next_working_time,
                }
            )

        queue_data = {
            "queue_list": queue_list,
            "is_working_time": is_working,
            "current_time_msk": msk_now.strftime("%H:%M"),
        }

    from django.conf import settings as _dj_settings

    public_base_url_missing = not (getattr(_dj_settings, "PUBLIC_BASE_URL", "") or "").strip()

    return render(
        request,
        "ui/mail/admin.html",
        {
            "active_tab": active_tab,
            "overview_data": overview_data,
            "settings_data": settings_data,
            "limits_data": limits_data,
            "analytics_data": analytics_data,
            "queue_data": queue_data,
            "quota": quota,
            "public_base_url_missing": public_base_url_missing,
        },
    )


@login_required
def mail_quota_poll(request: HttpRequest) -> JsonResponse:
    """Лёгкий эндпоинт для автообновления блока квоты/тарифа на странице кампаний."""
    enforce(
        user=request.user,
        resource_type="action",
        resource="ui:mail:quota:poll",
        context={"path": request.path, "method": request.method},
    )
    quota = SmtpBzQuota.load()
    now = timezone.now()
    try:
        from zoneinfo import ZoneInfo

        msk_now = now.astimezone(ZoneInfo("Europe/Moscow"))
        server_time_msk = msk_now.isoformat()
    except Exception:
        server_time_msk = now.isoformat()

    return JsonResponse(
        {
            "tariff_name": quota.tariff_name or "",
            "tariff_renewal_date": (
                quota.tariff_renewal_date.isoformat() if quota.tariff_renewal_date else None
            ),
            "emails_available": int(quota.emails_available or 0),
            "emails_limit": int(quota.emails_limit or 0),
            "sent_per_hour": int(quota.sent_per_hour or 0),
            "max_per_hour": int(quota.max_per_hour or 100),
            "last_synced_at": quota.last_synced_at.isoformat() if quota.last_synced_at else None,
            "sync_error": quota.sync_error or "",
            "server_time_msk": server_time_msk,
        }
    )
