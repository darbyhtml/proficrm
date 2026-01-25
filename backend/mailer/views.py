from __future__ import annotations

import json
import logging
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.paginator import Paginator
from django.db.models import Count, Q, Exists, OuterRef
from django.http import HttpRequest, HttpResponse, Http404
from django.http import FileResponse
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from accounts.models import User
from audit.models import ActivityEvent
from audit.service import log_event
from companies.models import Company
from companies.permissions import get_users_for_lists
from accounts.models import Branch
from companies.models import CompanySphere, CompanyStatus, ContactEmail, Contact
from mailer.constants import COOLDOWN_DAYS_DEFAULT, PER_USER_DAILY_LIMIT_DEFAULT
from mailer.forms import CampaignForm, CampaignGenerateRecipientsForm, CampaignRecipientAddForm, MailAccountForm, GlobalMailAccountForm, EmailSignatureForm
from mailer.models import Campaign, CampaignRecipient, MailAccount, GlobalMailAccount, SendLog, Unsubscribe, UnsubscribeToken, EmailCooldown, SmtpBzQuota, CampaignQueue
from mailer.smtp_sender import build_message, send_via_smtp
from mailer.mail_content import apply_signature
from mailer.utils import html_to_text, msk_day_bounds
from crm.utils import require_admin
from policy.engine import enforce
from notifications.service import notify
from notifications.models import Notification

logger = logging.getLogger(__name__)

def _smtp_bz_extract_total(resp) -> int | None:
    """
    Унифицирует "сколько записей" из ответа smtp.bz /log/message.
    В Swagger у ответа может быть total/count либо просто список.
    """
    if resp is None:
        return None
    if isinstance(resp, dict):
        for k in ("total", "count", "Total", "Count"):
            v = resp.get(k)
            if isinstance(v, (int, float)):
                return int(v)
            if isinstance(v, str) and v.isdigit():
                return int(v)
        data = resp.get("data")
        if isinstance(data, list):
            return len(data)
        return 0
    if isinstance(resp, list):
        return len(resp)
    return None


def _smtp_bz_today_stats_cached(*, api_key: str, today_str: str) -> dict:
    """
    Лёгкая аналитика из smtp.bz API (кешируем, чтобы не дергать API на каждый рендер).
    today_str: YYYY-MM-DD (по МСК).
    """
    cache_key = f"smtp_bz:stats:{today_str}"
    cached = cache.get(cache_key)
    if isinstance(cached, dict):
        return cached

    try:
        from mailer.smtp_bz_api import get_message_logs

        # Используем limit=1, надеясь на total в ответе. Если total нет — fallback на len(data).
        bounce = _smtp_bz_extract_total(get_message_logs(api_key, status="bounce", limit=1, start_date=today_str, end_date=today_str))
        returned = _smtp_bz_extract_total(get_message_logs(api_key, status="return", limit=1, start_date=today_str, end_date=today_str))
        cancelled = _smtp_bz_extract_total(get_message_logs(api_key, status="cancel", limit=1, start_date=today_str, end_date=today_str))
        opened = _smtp_bz_extract_total(get_message_logs(api_key, is_open=True, limit=1, start_date=today_str, end_date=today_str))
        unsub = _smtp_bz_extract_total(get_message_logs(api_key, is_unsubscribe=True, limit=1, start_date=today_str, end_date=today_str))

        result = {
            "bounce": bounce,
            "return": returned,
            "cancel": cancelled,
            "opened": opened,
            "unsub": unsub,
        }
        cache.set(cache_key, result, timeout=60)  # 1 минута
        return result
    except Exception as e:
        logger.debug(f"Failed to fetch smtp.bz stats: {e}")
        return {}

def _can_manage_campaign(user: User, camp: Campaign) -> bool:
    # ТЗ:
    # - менеджер: только свои кампании
    # - директор филиала/РОП: кампании филиала создателя
    # - управляющий/админ: все кампании
    if not user or not user.is_authenticated or not user.is_active:
        return False
    if user.is_superuser or user.role in (User.Role.ADMIN, User.Role.GROUP_MANAGER):
        return True
    if user.role == User.Role.MANAGER:
        return bool(camp.created_by_id and camp.created_by_id == user.id)
    if user.role in (User.Role.BRANCH_DIRECTOR, User.Role.SALES_HEAD):
        # если у пользователя нет филиала — можем управлять только своими
        if not user.branch_id:
            return bool(camp.created_by_id and camp.created_by_id == user.id)
        # если у кампании нет создателя — запрещаем
        if not camp.created_by_id:
            return False
        # проверяем филиал создателя
        try:
            creator = getattr(camp, "created_by", None)
            creator_branch_id = getattr(creator, "branch_id", None) if creator else None
            if creator_branch_id is None:
                # если у создателя нет филиала — считаем, что это "не филиальная" кампания; директор/РОП управлять не должны
                return False
            return bool(creator_branch_id == user.branch_id)
        except Exception:
            return False
    return False

def _contains_links(value: str) -> bool:
    v = (value or "").lower()
    return any(x in v for x in ("<a ", "href=", "http://", "https://", "www."))


@login_required
def mail_signature(request: HttpRequest) -> HttpResponse:
    """
    Настройка подписи (персональная, для всех пользователей).
    """
    enforce(user=request.user, resource_type="page", resource="ui:mail:signature", context={"path": request.path})
    user: User = request.user
    if request.method == "POST":
        form = EmailSignatureForm(request.POST)
        if form.is_valid():
            html = (form.cleaned_data.get("signature_html") or "").strip()
            # Безопасность: подпись видит и меняет только сам пользователь (используется в исходящих письмах).
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
        form = EmailSignatureForm(initial={"signature_html": (getattr(user, "email_signature_html", "") or "")})
    return render(request, "ui/mail/signature.html", {"form": form})


@login_required
def mail_settings(request: HttpRequest) -> HttpResponse:
    """
    Настройки SMTP. Редактирует только администратор (глобально для всей CRM).
    """
    enforce(user=request.user, resource_type="page", resource="ui:mail:settings", context={"path": request.path})
    user: User = request.user
    is_admin = require_admin(user)
    cfg = GlobalMailAccount.load()

    if request.method == "POST":
        enforce(user=request.user, resource_type="action", resource="ui:mail:settings:update", context={"path": request.path, "method": request.method})
        if not is_admin:
            messages.error(request, "Доступ запрещён.")
            return redirect("mail_settings")
        form = GlobalMailAccountForm(request.POST, instance=cfg)
        if form.is_valid():
            # Проверка на наличие ключа шифрования, если вводят новый пароль
            password = (form.cleaned_data.get("smtp_password") or "").strip()
            if password:
                from django.conf import settings
                if not getattr(settings, "MAILER_FERNET_KEY", ""):
                    messages.error(request, "MAILER_FERNET_KEY не задан. Нельзя сохранить пароль.")
                    return redirect("mail_settings")
            # Сохраняем форму и проверяем, был ли изменен API ключ
            old_api_key = cfg.smtp_bz_api_key if cfg.pk else None
            form.save()
            # Обновляем объект из БД, чтобы получить новый API ключ
            cfg.refresh_from_db()
            new_api_key = cfg.smtp_bz_api_key
            
            # Если API ключ был добавлен или изменен, запускаем синхронизацию немедленно
            if new_api_key and new_api_key != old_api_key:
                from mailer.tasks import sync_smtp_bz_quota
                try:
                    # Запускаем синхронизацию синхронно для немедленной проверки
                    sync_smtp_bz_quota.delay()
                    messages.info(request, "API ключ сохранен. Запущена синхронизация квоты...")
                except Exception as e:
                    logger.error(f"Ошибка при запуске синхронизации квоты: {e}", exc_info=True)
                    messages.warning(request, "API ключ сохранен, но не удалось запустить синхронизацию. Попробуйте обновить страницу через несколько секунд.")
            
            if "test_send" in request.POST:
                # тестовое письмо администратору (на email профиля)
                to_email = (user.email or "").strip()
                if not to_email:
                    messages.error(request, "В вашем профиле не задан email — некуда отправить тест.")
                    return redirect("mail_settings")
                from django.conf import settings
                rel = reverse("mail_settings")
                base = (getattr(settings, "PUBLIC_BASE_URL", "") or "").strip().rstrip("/")
                test_url = (base + rel) if base else request.build_absolute_uri(rel)
                # From: по умолчанию используем SMTP логин (самый совместимый вариант), Reply-To = email админа
                # Добавляем ссылку отписки (чтобы тест был максимально похож на реальную отправку)
                try:
                    from mailer.mail_content import ensure_unsubscribe_tokens, build_unsubscribe_url, append_unsubscribe_footer
                    token = ensure_unsubscribe_tokens([to_email]).get(to_email.strip().lower(), "")
                    unsub_url = build_unsubscribe_url(token) if token else ""
                except Exception:
                    unsub_url = ""

                body_html = "<p>Тестовое письмо из CRM ПРОФИ.</p><p>Если вы это читаете — SMTP настроен.</p>"
                body_text = "Тестовое письмо из CRM ПРОФИ.\n\nЕсли вы это читаете — SMTP настроен.\n"
                if unsub_url:
                    body_html, body_text = append_unsubscribe_footer(body_html=body_html, body_text=body_text, unsubscribe_url=unsub_url)

                msg = build_message(
                    account=cfg,  # type: ignore[arg-type]
                    to_email=to_email,
                    subject="CRM ПРОФИ: тест отправки",
                    body_text=body_text,
                    body_html=body_html,
                    from_email=((cfg.from_email or "").strip() or (cfg.smtp_username or "").strip()),
                    from_name=(cfg.from_name or "CRM ПРОФИ").strip(),
                    reply_to=to_email,
                )
                if unsub_url:
                    msg["List-Unsubscribe"] = f"<{unsub_url}>"
                    msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
                    msg["X-Tag"] = "test:mail_settings"
                try:
                    send_via_smtp(cfg, msg)
                    messages.success(request, f"Тестовое письмо отправлено на {to_email}.")
                except Exception as ex:
                    messages.error(request, f"Ошибка отправки: {ex}")
                return redirect("mail_settings")
            messages.success(request, "Настройки SMTP сохранены.")
            return redirect("mail_settings")
    else:
        form = GlobalMailAccountForm(instance=cfg) if is_admin else None

    from django.conf import settings
    key_missing = not bool(getattr(settings, "MAILER_FERNET_KEY", "") or "")
    
    # Информация о квоте для проверки подключения
    quota = SmtpBzQuota.load()
    # Подключение считается установленным, если:
    # 1. API ключ есть
    # 2. Синхронизация прошла успешно (есть last_synced_at и нет ошибки)
    # ИЛИ синхронизация еще не выполнялась, но ключ есть (показываем как "ожидание")
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
def mail_quota_poll(request: HttpRequest) -> JsonResponse:
    """
    Лёгкий эндпоинт для автообновления блока квоты/тарифа на странице кампаний.
    Данные берём из БД (SmtpBzQuota), которую обновляет Celery задача sync_smtp_bz_quota.
    """
    enforce(user=request.user, resource_type="action", resource="ui:mail:quota:poll", context={"path": request.path, "method": request.method})
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
            "tariff_renewal_date": quota.tariff_renewal_date.isoformat() if quota.tariff_renewal_date else None,
            "emails_available": int(quota.emails_available or 0),
            "emails_limit": int(quota.emails_limit or 0),
            "sent_per_hour": int(quota.sent_per_hour or 0),
            "max_per_hour": int(quota.max_per_hour or 100),
            "last_synced_at": quota.last_synced_at.isoformat() if quota.last_synced_at else None,
            "sync_error": quota.sync_error or "",
            "server_time_msk": server_time_msk,
        }
    )


@login_required
def mail_unsubscribes_list(request: HttpRequest) -> JsonResponse:
    """
    Список отписок (для админского модального окна в разделе "Почта").
    """
    user: User = request.user
    enforce(user=request.user, resource_type="action", resource="ui:mail:unsubscribes:list", context={"path": request.path, "method": request.method})
    if user.role != User.Role.ADMIN:
        return JsonResponse({"ok": False, "error": "forbidden"}, status=403)

    q = (request.GET.get("q") or "").strip()
    try:
        limit = int(request.GET.get("limit") or 200)
    except Exception:
        limit = 200
    try:
        offset = int(request.GET.get("offset") or 0)
    except Exception:
        offset = 0

    limit = max(1, min(500, limit))
    offset = max(0, offset)

    qs = Unsubscribe.objects.all()
    if q:
        qs = qs.filter(email__icontains=q)

    total = qs.count()
    rows = list(
        qs.order_by("-last_seen_at", "-created_at")
        .values("email", "source", "reason", "last_seen_at", "created_at")[offset : offset + limit]
    )

    def _dt_iso(v):
        try:
            return v.isoformat() if v else None
        except Exception:
            return None

    data = [
        {
            "email": (r.get("email") or ""),
            "source": (r.get("source") or ""),
            "reason": (r.get("reason") or ""),
            "last_seen_at": _dt_iso(r.get("last_seen_at")),
            "created_at": _dt_iso(r.get("created_at")),
        }
        for r in rows
    ]
    return JsonResponse({"ok": True, "total": total, "limit": limit, "offset": offset, "data": data})


@login_required
def mail_unsubscribes_delete(request: HttpRequest) -> JsonResponse:
    """
    Удаление выбранных email из списка отписок (админ).
    """
    user: User = request.user
    enforce(user=request.user, resource_type="action", resource="ui:mail:unsubscribes:delete", context={"path": request.path, "method": request.method})
    if user.role != User.Role.ADMIN:
        return JsonResponse({"ok": False, "error": "forbidden"}, status=403)
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "method_not_allowed"}, status=405)

    emails: list[str] = []
    try:
        if (request.content_type or "").lower().startswith("application/json"):
            payload = json.loads((request.body or b"{}").decode("utf-8") or "{}")
            raw = payload.get("emails") or []
            if isinstance(raw, list):
                emails = [str(x) for x in raw]
        else:
            emails = request.POST.getlist("emails")
    except Exception:
        emails = []

    emails_norm = [(e or "").strip().lower() for e in emails if (e or "").strip()]
    emails_norm = list(dict.fromkeys(emails_norm))
    if not emails_norm:
        return JsonResponse({"ok": False, "error": "no_emails"}, status=400)

    deleted, _ = Unsubscribe.objects.filter(email__in=emails_norm).delete()
    return JsonResponse({"ok": True, "deleted": int(deleted or 0)})


@login_required
def mail_unsubscribes_clear(request: HttpRequest) -> JsonResponse:
    """
    Полная очистка списка отписок (админ).
    """
    user: User = request.user
    enforce(user=request.user, resource_type="action", resource="ui:mail:unsubscribes:clear", context={"path": request.path, "method": request.method})
    if user.role != User.Role.ADMIN:
        return JsonResponse({"ok": False, "error": "forbidden"}, status=403)
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "method_not_allowed"}, status=405)

    deleted, _ = Unsubscribe.objects.all().delete()
    return JsonResponse({"ok": True, "deleted": int(deleted or 0)})


@login_required
def campaigns(request: HttpRequest) -> HttpResponse:
    enforce(user=request.user, resource_type="page", resource="ui:mail:campaigns", context={"path": request.path})
    user: User = request.user
    is_admin = (user.role == User.Role.ADMIN)
    is_group_manager = (user.role == User.Role.GROUP_MANAGER)
    is_branch_director = (user.role == User.Role.BRANCH_DIRECTOR)
    is_sales_head = (user.role == User.Role.SALES_HEAD)
    
    # Фильтрация кампаний по ролям:
    # - Менеджер: только свои
    # - Администратор и управляющий: все
    # - Директор филиала и РОП: все своего филиала
    # Важно для производительности:
    # - на списке кампаний не нужны большие поля body_html/body_text/filter_meta → defer
    # - в шаблоне часто используется created_by → select_related, чтобы избежать N+1
    qs = (
        Campaign.objects.select_related("created_by", "created_by__branch")
        .defer("body_html", "body_text", "filter_meta")
        .order_by("-created_at")
    )
    if user.role == User.Role.MANAGER:
        qs = qs.filter(created_by=user)
    elif is_branch_director or is_sales_head:
        # Директор филиала и РОП видят кампании всех пользователей своего филиала
        if user.branch:
            qs = qs.filter(created_by__branch=user.branch)
        else:
            # Если у пользователя нет филиала, показываем только свои
            qs = qs.filter(created_by=user)
    # Для админа и управляющего - все кампании (без фильтрации)
    
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
        
        # Статистика по пользователям без N+1: агрегируем одним запросом
        all_users = list(
            User.objects.filter(role__in=[User.Role.MANAGER, User.Role.ADMIN, User.Role.BRANCH_DIRECTOR, User.Role.GROUP_MANAGER]).select_related("branch")
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
                active_campaigns=Count("id", filter=Q(status__in=[Campaign.Status.READY, Campaign.Status.SENDING])),
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
        
        # Сортируем по количеству отправленных писем (по убыванию)
        user_stats.sort(key=lambda x: x["sent_today"], reverse=True)
        
        totals = (
            SendLog.objects.filter(
                provider="smtp_global",
                created_at__gte=start_day_utc,
                created_at__lt=end_day_utc,
            )
            .aggregate(
                total_sent_today=Count("id", filter=Q(status="sent")),
                total_failed_today=Count("id", filter=Q(status="failed")),
            )
        )
        total_sent_today = int(totals.get("total_sent_today") or 0)
        total_failed_today = int(totals.get("total_failed_today") or 0)
        
        # Статистика по кампаниям
        campaigns_stats = Campaign.objects.aggregate(
            total=Count("id"),
            active=Count("id", filter=Q(status__in=[Campaign.Status.READY, Campaign.Status.SENDING])),
            paused=Count("id", filter=Q(status=Campaign.Status.PAUSED)),
            sent=Count("id", filter=Q(status=Campaign.Status.SENT)),
        )
        
        # Получаем лимиты из API
        quota = SmtpBzQuota.load()
        if quota.last_synced_at and not quota.sync_error and quota.emails_limit > 0:
            global_limit = quota.emails_limit
            max_per_hour = quota.max_per_hour or 100
        else:
            global_limit = smtp_cfg.rate_per_day
            max_per_hour = 100
        
        # Получаем информацию об очереди
        from mailer.tasks import _is_working_hours
        queue_list = []
        queue_entries_all = list(CampaignQueue.objects.filter(
            status__in=[CampaignQueue.Status.PENDING, CampaignQueue.Status.PROCESSING]
        ).select_related("campaign", "campaign__created_by").order_by("-priority", "queued_at"))
        
        # Определяем время начала следующего рабочего дня, если сейчас не рабочее время
        is_working = _is_working_hours(now)
        next_working_time = None
        if not is_working:
            # Если сейчас не рабочее время, вычисляем когда начнется следующее рабочее время
            if msk_now.hour >= 18:  # После 18:00 - следующий день в 9:00
                next_working_time = msk_now.replace(hour=9, minute=0, second=0, microsecond=0) + _tz.timedelta(days=1)
            else:  # До 9:00 - сегодня в 9:00
                next_working_time = msk_now.replace(hour=9, minute=0, second=0, microsecond=0)
        
        for idx, queue_entry in enumerate(queue_entries_all, 1):
            queue_list.append({
                "position": idx,
                "campaign": queue_entry.campaign,
                "status": queue_entry.status,
                "queued_at": queue_entry.queued_at,
                "started_at": queue_entry.started_at,
                "next_working_time": next_working_time,
            })
        
        smtp_bz_stats = {}
        if smtp_cfg.smtp_bz_api_key:
            smtp_bz_stats = _smtp_bz_today_stats_cached(api_key=smtp_cfg.smtp_bz_api_key, today_str=today_str)

        analytics = {
            "user_stats": user_stats,
            "total_sent_today": total_sent_today,
            "total_failed_today": total_failed_today,
            "global_limit": global_limit,
            "per_user_limit": per_user_daily_limit,
            "campaigns_stats": campaigns_stats,
            "sent_last_hour": SendLog.objects.filter(
                provider="smtp_global",
                status="sent",
                created_at__gte=now - _tz.timedelta(hours=1)
            ).count(),
            "max_per_hour": max_per_hour,
            "queue_list": queue_list,
            "is_working_time": is_working,
            "current_time_msk": msk_now.strftime("%H:%M"),
            "smtp_bz": smtp_bz_stats,
        }
    
    # Информация о квоте smtp.bz (для всех пользователей)
    quota = SmtpBzQuota.load()
    
    # Информация о лимите пользователя (для всех)
    from django.utils import timezone as _tz
    now = _tz.now()
    start_day_utc, end_day_utc, now_msk = msk_day_bounds(now)
    today = now_msk.date()
    sent_today_user = SendLog.objects.filter(
        provider="smtp_global",
        status="sent",
        campaign__created_by=user,
        created_at__gte=start_day_utc,
        created_at__lt=end_day_utc,
    ).count()
    
    smtp_cfg = GlobalMailAccount.load()
    per_user_daily_limit = smtp_cfg.per_user_daily_limit or PER_USER_DAILY_LIMIT_DEFAULT
    
    # Количество кампаний пользователя
    user_campaigns_count = Campaign.objects.filter(created_by=user).count()
    user_active_campaigns = Campaign.objects.filter(
        created_by=user,
        status__in=[Campaign.Status.READY, Campaign.Status.SENDING]
    ).count()
    
    user_limit_info = {
        "sent_today": sent_today_user,
        "limit": per_user_daily_limit,
        "remaining": max(0, per_user_daily_limit - sent_today_user) if per_user_daily_limit else None,
        "is_limit_reached": per_user_daily_limit and sent_today_user >= per_user_daily_limit,
        "campaigns_count": user_campaigns_count,
        "active_campaigns": user_active_campaigns,
    }
    
    # Определяем, показывать ли колонку "Создатель" в таблице
    show_creator_column = (is_admin or is_group_manager or is_branch_director or is_sales_head)
    
    # Добавляем информацию о паузе и рабочем времени для каждой кампании
    from mailer.tasks import _is_working_hours
    campaigns_list = list(qs)
    is_working_time = _is_working_hours(now)
    msk_now = now_msk
    current_time_msk = msk_now.strftime("%H:%M")
    
    # Получаем лимиты для проверки паузы
    if quota and quota.last_synced_at and not quota.sync_error and quota.emails_limit > 0:
        global_limit = quota.emails_limit
        max_per_hour = quota.max_per_hour or 100
        emails_available = quota.emails_available or 0
    else:
        global_limit = smtp_cfg.rate_per_day
        max_per_hour = 100
        emails_available = global_limit
    
    sent_today = SendLog.objects.filter(provider="smtp_global", status="sent", created_at__gte=start_day_utc, created_at__lt=end_day_utc).count()
    sent_last_hour = SendLog.objects.filter(provider="smtp_global", status="sent", created_at__gte=now - _tz.timedelta(hours=1)).count()
    
    # Для каждой кампании определяем причины паузы и информацию об очереди
    # Оптимизация: предзагружаем queue_entry одним запросом
    campaign_ids = [c.id for c in campaigns_list]
    queue_entries = {str(q.campaign_id): q for q in CampaignQueue.objects.filter(campaign_id__in=campaign_ids).select_related("campaign")}

    # Оптимизация: сколько отправлено "сегодня" по каждому создателю (для pause reasons) одним запросом
    creator_ids = [c.created_by_id for c in campaigns_list if getattr(c, "created_by_id", None)]
    creator_ids = list(dict.fromkeys([cid for cid in creator_ids if cid]))  # unique
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
        
        # Проверяем только для активных кампаний
        if camp.status in (Campaign.Status.READY, Campaign.Status.SENDING):
            if not is_working_time:
                camp_pause_reasons.append(f"Вне рабочего времени (текущее время МСК: {current_time_msk}, рабочие часы: 9:00-18:00 МСК)")
            
            # Проверяем лимит пользователя (для создателя кампании)
            camp_sent_today = creator_sent_map.get(camp.created_by_id, 0) if camp.created_by_id else 0
            
            if per_user_daily_limit and camp_sent_today >= per_user_daily_limit:
                camp_pause_reasons.append(f"Достигнут лимит отправки для аккаунта: {camp_sent_today}/{per_user_daily_limit} писем в день")
            
            if emails_available <= 0:
                camp_pause_reasons.append(f"Квота исчерпана: доступно {emails_available} из {global_limit} писем")
            elif sent_today >= global_limit:
                camp_pause_reasons.append(f"Достигнут глобальный дневной лимит: {sent_today}/{global_limit} писем")
            
            if sent_last_hour >= max_per_hour:
                camp_pause_reasons.append(f"Достигнут лимит в час: {sent_last_hour}/{max_per_hour} писем")
            
            if not smtp_cfg.is_enabled:
                camp_pause_reasons.append("SMTP не включен администратором")
        
        camp.pause_reasons = camp_pause_reasons  # type: ignore[attr-defined]
        camp.is_working_time = is_working_time  # type: ignore[attr-defined]
        camp.current_time_msk = current_time_msk  # type: ignore[attr-defined]
        camp.queue_entry = queue_entries.get(str(camp.id))  # type: ignore[attr-defined]
    
    return render(
        request,
        "ui/mail/campaigns.html",
        {
            "campaigns": campaigns_list,
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
        }
    )


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
    if getattr(camp, "attachment", None):
        try:
            attachment_filename = (camp.attachment_original_name or (camp.attachment.name.split("/")[-1] if camp.attachment and camp.attachment.name else "")).strip()
            if attachment_filename and "." in attachment_filename:
                attachment_ext = attachment_filename.split(".")[-1].upper()
            else:
                attachment_ext = ""
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
        },
    )


@login_required
def campaign_detail(request: HttpRequest, campaign_id) -> HttpResponse:
    enforce(user=request.user, resource_type="page", resource="ui:mail:campaigns:detail", context={"path": request.path})
    user: User = request.user
    camp = get_object_or_404(Campaign, id=campaign_id)
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
    # Альтернативный взгляд "кому реально уходило" — по логам SendLog (полезно, если статусы получателей сбрасывали).
    counts["sent_log"] = (
        SendLog.objects.filter(campaign=camp, provider="smtp_global", status="sent", recipient__isnull=False)
        .values("recipient_id")
        .distinct()
        .count()
    )
    
    # Пагинация с выбором per_page (как в company_list)
    per_page_param = request.GET.get("per_page", "").strip()
    if per_page_param:
        try:
            per_page = int(per_page_param)
            # Разрешенные значения: 25, 50, 100, 200
            if per_page in [25, 50, 100, 200]:
                request.session["campaign_recipients_per_page"] = per_page
            else:
                per_page = request.session.get("campaign_recipients_per_page", 50)
        except (ValueError, TypeError):
            per_page = request.session.get("campaign_recipients_per_page", 50)
    else:
        per_page = request.session.get("campaign_recipients_per_page", 50)
    
    # "Стопки" по статусу в UI
    view = (request.GET.get("view") or "").strip().lower()
    allowed_views = {"pending", "sent", "failed", "unsub", "sent_log", "all"}
    if view not in allowed_views:
        # По умолчанию показываем "pending", если есть, иначе "all"
        view = "pending" if counts["pending"] > 0 else "all"

    # Получаем всех получателей для группировки и пагинации
    # Важно: используем .all() для получения всех получателей, затем фильтруем
    all_recipients_qs = camp.recipients.all().order_by("company_id", "-updated_at")
    
    # Применяем фильтр по статусу (если не "all")
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
        all_recipients_qs = all_recipients_qs.annotate(_sent_log=Exists(sent_exists)).filter(_sent_log=True)
    # Если view == "all" - не применяем фильтр, показываем всех
    
    # Пагинация для таблицы
    paginator = Paginator(all_recipients_qs, per_page)
    page = paginator.get_page(request.GET.get("page"))
    
    # Получаем получателей текущей страницы
    page_recipients = list(page.object_list)
    
    # Загружаем компании одним запросом
    company_ids = [r.company_id for r in page_recipients if r.company_id]
    companies_map = {}
    if company_ids:
        companies_map = {str(c.id): c for c in Company.objects.filter(id__in=company_ids).only("id", "name", "contact_name", "contact_position")}
    
    # Загружаем контакты одним запросом
    contact_ids = [r.contact_id for r in page_recipients if r.contact_id]
    contacts_map = {}
    if contact_ids:
        contacts_map = {str(c.id): c for c in Contact.objects.filter(id__in=contact_ids).only("id", "first_name", "last_name", "position")}
    
    # Группируем получателей текущей страницы по компаниям
    recipients_by_company = {}
    for r in page_recipients:
        company_id = str(r.company_id) if r.company_id else "no_company"
        if company_id not in recipients_by_company:
            recipients_by_company[company_id] = {
                "company": companies_map.get(company_id) if company_id != "no_company" else None,
                "recipients": []
            }
        # Добавляем информацию о контакте, если есть
        if r.contact_id and str(r.contact_id) in contacts_map:
            r.contact = contacts_map[str(r.contact_id)]
        # Добавляем информацию о компании
        if r.company_id and str(r.company_id) in companies_map:
            r.company = companies_map[str(r.company_id)]
        recipients_by_company[company_id]["recipients"].append(r)
    
    # Формируем query string для пагинации (без page, но с per_page если отличается от дефолта)
    params = request.GET.copy()
    params.pop("page", None)
    if per_page != 50:
        params["per_page"] = str(per_page)
    qs_no_page = params.urlencode()
    
    # Для обратной совместимости (таблица)
    recent = page_recipients
    
    # Статистика лимитов и рабочего времени
    from django.utils import timezone as _tz
    from mailer.tasks import _is_working_hours
    from zoneinfo import ZoneInfo
    
    # Получаем лимиты из API (только для администраторов)
    is_admin = (user.role == User.Role.ADMIN)
    quota = SmtpBzQuota.load() if is_admin else None
    if is_admin and quota and quota.last_synced_at and not quota.sync_error and quota.emails_limit > 0:
        global_limit = quota.emails_limit
        max_per_hour = quota.max_per_hour or 100
        emails_available = quota.emails_available or 0
    else:
        global_limit = smtp_cfg.rate_per_day
        max_per_hour = 100
        emails_available = global_limit
    
    now = _tz.now()
    start_day_utc, end_day_utc, now_msk = msk_day_bounds(now)
    sent_last_hour = SendLog.objects.filter(provider="smtp_global", status="sent", created_at__gte=now - _tz.timedelta(hours=1)).count()
    sent_today = SendLog.objects.filter(provider="smtp_global", status="sent", created_at__gte=start_day_utc, created_at__lt=end_day_utc).count()
    sent_today_user = SendLog.objects.filter(provider="smtp_global", status="sent", campaign__created_by=user, created_at__gte=start_day_utc, created_at__lt=end_day_utc).count()
    
    per_user_daily_limit = smtp_cfg.per_user_daily_limit or PER_USER_DAILY_LIMIT_DEFAULT
    is_working_time = _is_working_hours(now)
    
    # Количество кампаний пользователя
    user_campaigns_count = Campaign.objects.filter(created_by=user).count()
    user_active_campaigns = Campaign.objects.filter(
        created_by=user,
        status__in=[Campaign.Status.READY, Campaign.Status.SENDING]
    ).count()
    
    # Текущее московское время для отображения
    msk_now = now_msk
    current_time_msk = msk_now.strftime("%H:%M")
    
    # Причины паузы/блокировки
    pause_reasons = []
    if not is_working_time:
        pause_reasons.append(f"Вне рабочего времени (текущее время МСК: {current_time_msk}, рабочие часы: 9:00-18:00 МСК)")
    if per_user_daily_limit and sent_today_user >= per_user_daily_limit:
        pause_reasons.append(f"Достигнут лимит отправки для вашего аккаунта: {sent_today_user}/{per_user_daily_limit} писем в день")
    if emails_available <= 0:
        pause_reasons.append(f"Квота исчерпана: доступно {emails_available} из {global_limit} писем")
    elif sent_today >= global_limit:
        pause_reasons.append(f"Достигнут глобальный дневной лимит: {sent_today}/{global_limit} писем")
    if sent_last_hour >= max_per_hour:
        pause_reasons.append(f"Достигнут лимит в час: {sent_last_hour}/{max_per_hour} писем")
    if not smtp_cfg.is_enabled:
        pause_reasons.append("SMTP не включен администратором")
    
    # Статистика отправки (из SendLog)
    send_stats = {
        "sent_today": sent_today,
        "sent_today_user": sent_today_user,
        "sent_last_hour": sent_last_hour,
        "failed_today": SendLog.objects.filter(provider="smtp_global", status="failed", created_at__gte=start_day_utc, created_at__lt=end_day_utc).count(),
        "failed_today_campaign": SendLog.objects.filter(campaign=camp, provider="smtp_global", status="failed", created_at__gte=start_day_utc, created_at__lt=end_day_utc).count(),
    }
    
    # Последние ошибки отправки для этой кампании (для отображения)
    recent_errors = SendLog.objects.filter(
        campaign=camp,
        provider="smtp_global",
        status="failed"
    ).select_related("recipient").order_by("-created_at")[:10]
    
    # Детальная статистика отправки для этой кампании (аналогично smtp.bz админке)
    # Группировка ошибок по типам
    error_types = {}
    for error_log in SendLog.objects.filter(campaign=camp, provider="smtp_global", status="failed").order_by("-created_at")[:100]:
        error_msg = (error_log.error or "").strip()
        if not error_msg:
            error_msg = "Неизвестная ошибка"
        # Упрощаем сообщение об ошибке для группировки
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
            error_types[error_key]["examples"].append({
                "email": error_log.recipient.email if error_log.recipient else "—",
                "error": error_msg[:200],
                "time": error_log.created_at,
            })

    # Подготовка HTML для предпросмотра с подписью
    preview_html = camp.body_html or ""
    if preview_html:
        auto_plain = html_to_text(preview_html)
        preview_html, _ = apply_signature(
            user=user,
            body_html=preview_html,
            body_text=auto_plain or camp.body_text or ""
        )

    return render(
        request,
        "ui/mail/campaign_detail.html",
        {
            "campaign": camp,
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
            "preview_html": preview_html,  # HTML с подписью для предпросмотра
            "emails_available": emails_available,
            "counts": counts,
            "recent": recent,
            "smtp_from_email": (smtp_cfg.from_email or smtp_cfg.smtp_username or "").strip(),
            "smtp_from_name_default": (smtp_cfg.from_name or "CRM ПРОФИ").strip(),
            "recipient_add_form": CampaignRecipientAddForm(),
            "generate_form": CampaignGenerateRecipientsForm(),
            "branches": Branch.objects.order_by("name") if (user.role == User.Role.ADMIN or user.role == User.Role.GROUP_MANAGER) else (Branch.objects.filter(id=user.branch_id) if user.branch_id else Branch.objects.none()),
            "responsibles": get_users_for_lists(request.user),
            "statuses": CompanyStatus.objects.order_by("name"),
            "spheres": CompanySphere.objects.order_by("name"),
            "user": user,
            "recipients_by_company": recipients_by_company,
            "page": page,
            "qs": qs_no_page,
            "per_page": per_page,
            "can_delete_campaign": _can_manage_campaign(user, camp),
            "is_admin": (user.role == User.Role.ADMIN),
            "view": view,
            "recent_errors": recent_errors,
            "error_types": error_types,
            "quota": quota,
            "queue_entry": (qe := getattr(camp, "queue_entry", None)),
            "deferred_until": (du := getattr(qe, "deferred_until", None) if qe else None),
            "defer_reason": (getattr(qe, "defer_reason", None) or "") if qe else "",
            "is_deferred_daily_limit": bool(qe and (getattr(qe, "defer_reason", None) or "") == "daily_limit" and du and du > timezone.now()),
            "emails_available": emails_available,
            "rate_per_hour": max_per_hour,
            "attachment_ext": (
                ((camp.attachment_original_name or camp.attachment.name).split(".")[-1].upper() if (camp.attachment_original_name or camp.attachment) and "." in (camp.attachment_original_name or camp.attachment.name) else "")
                if camp.attachment
                else ""
            ),
            "attachment_filename": (camp.attachment_original_name or (camp.attachment.name.split("/")[-1] if camp.attachment and camp.attachment.name else "")),
        },
    )


@login_required
def campaign_attachment_download(request: HttpRequest, campaign_id) -> HttpResponse:
    """
    Скачивание вложения кампании с оригинальным именем файла (без переименования).
    """
    enforce(user=request.user, resource_type="action", resource="ui:mail:campaigns:attachment:download", context={"path": request.path, "method": request.method})
    user: User = request.user
    camp = get_object_or_404(Campaign, id=campaign_id)
    if not _can_manage_campaign(user, camp):
        raise Http404()
    if not camp.attachment:
        raise Http404()
    fname = (camp.attachment_original_name or "").strip() or (camp.attachment.name.split("/")[-1] if camp.attachment.name else "attachment")
    # Django FileResponse умеет ставить Content-Disposition с filename
    try:
        f = camp.attachment.open("rb")
    except Exception:
        f = camp.attachment.open()
    return FileResponse(f, as_attachment=True, filename=fname)


@login_required
def campaign_attachment_delete(request: HttpRequest, campaign_id) -> JsonResponse:
    """
    AJAX удаление вложения кампании.
    """
    if request.method != "POST":
        return JsonResponse({"success": False, "error": "Метод не разрешен"}, status=405)
    
    enforce(user=request.user, resource_type="action", resource="ui:mail:campaigns:edit", context={"path": request.path, "method": request.method})
    user: User = request.user
    camp = get_object_or_404(Campaign, id=campaign_id)
    if not _can_manage_campaign(user, camp):
        return JsonResponse({"success": False, "error": "Доступ запрещён"}, status=403)
    
    if not camp.attachment:
        return JsonResponse({"success": False, "error": "Вложение не найдено"}, status=404)
    
    try:
        # Удаляем файл со стораджа
        camp.attachment.delete(save=False)
        camp.attachment = None
        camp.attachment_original_name = ""
        camp.save(update_fields=["attachment", "attachment_original_name", "updated_at"])
        return JsonResponse({"success": True})
    except Exception as e:
        logger.error(f"Ошибка при удалении вложения кампании {camp.id}: {e}")
        return JsonResponse({"success": False, "error": "Ошибка при удалении файла"}, status=500)


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
    camp.delete()
    messages.success(request, f"Кампания «{camp_name}» удалена.")
    log_event(actor=user, verb=ActivityEvent.Verb.DELETE, entity_type="campaign", entity_id=camp_id_str, message="Удалена рассылочная кампания")
    return redirect("campaigns")


@login_required
def mail_progress_poll(request: HttpRequest) -> JsonResponse:
    """
    Лёгкий polling для глобального виджета прогресса рассылки.
    Возвращает активную кампанию пользователя (если есть) и процент.
    """
    user: User = request.user
    enforce(user=request.user, resource_type="action", resource="ui:mail:progress:poll", context={"path": request.path, "method": request.method})

    # Берём ближайшую "активную" кампанию пользователя:
    # - SENDING — во время отправки
    # - PAUSED  — пауза из-за лимитов/времени/вложений и т.п. (показываем, чтобы было понятно почему "не идёт")
    # - SENT    — сразу после завершения (чтобы менеджер увидел результат, даже если писем было мало)
    qs = Campaign.objects.filter(created_by=user).order_by("-updated_at")
    active = qs.filter(status__in=[Campaign.Status.SENDING, Campaign.Status.PAUSED, Campaign.Status.SENT]).first()
    if not active:
        return JsonResponse({"ok": True, "active": None})

    agg = active.recipients.aggregate(
        pending=Count("id", filter=Q(status=CampaignRecipient.Status.PENDING)),
        sent=Count("id", filter=Q(status=CampaignRecipient.Status.SENT)),
        failed=Count("id", filter=Q(status=CampaignRecipient.Status.FAILED)),
        total=Count("id"),
    )
    pending = int(agg.get("pending") or 0)
    sent = int(agg.get("sent") or 0)
    failed = int(agg.get("failed") or 0)
    total = int(agg.get("total") or 0)
    done = sent + failed
    percent = 0
    if total > 0:
        percent = int(round((done / total) * 100))

    # Мини-пояснение "почему не идёт": дневной лимит / вне времени / SMTP выключен
    from django.utils import timezone as _tz
    from mailer.tasks import _is_working_hours

    now = _tz.now()
    start_day_utc, end_day_utc, _now_msk = msk_day_bounds(now)
    smtp_cfg = GlobalMailAccount.load()
    sent_today_user = SendLog.objects.filter(
        provider="smtp_global",
        status="sent",
        campaign__created_by=user,
        created_at__gte=start_day_utc,
        created_at__lt=end_day_utc,
    ).count()
    per_user_daily_limit = smtp_cfg.per_user_daily_limit or PER_USER_DAILY_LIMIT_DEFAULT
    limit_reached = False
    if per_user_daily_limit and sent_today_user >= per_user_daily_limit and pending > 0:
        limit_reached = True

    reason_code = None
    reason_text = ""
    if not getattr(smtp_cfg, "is_enabled", True):
        reason_code = "smtp_disabled"
        reason_text = "SMTP отключен администратором"
    elif not _is_working_hours(now) and pending > 0:
        reason_code = "outside_working_hours"
        reason_text = "Вне рабочего времени (МСК)"
    elif limit_reached:
        reason_code = "user_daily_limit"
        reason_text = "Дневной лимит исчерпан"

    q = getattr(active, "queue_entry", None)
    queue_status = (getattr(q, "status", None) if q else None)
    deferred_until = getattr(q, "deferred_until", None)
    defer_reason = (getattr(q, "defer_reason", None) or "") if q else ""

    return JsonResponse(
        {
            "ok": True,
            "active": {
                "id": str(active.id),
                "name": active.name,
                "status": active.status,
                "pending": pending,
                "sent": sent,
                "failed": failed,
                "total": total,
                "percent": max(0, min(100, percent)),
                "url": f"/mail/campaigns/{active.id}/",
                "limit_reached": limit_reached,
                "per_user_daily_limit": per_user_daily_limit,
                "sent_today_user": sent_today_user,
                "reason_code": reason_code,
                "reason_text": reason_text,
                "queue_status": queue_status,
                "deferred_until": deferred_until.isoformat() if deferred_until else None,
                "defer_reason": defer_reason,
                "next_run_at": deferred_until.isoformat() if deferred_until else None,
            },
        }
    )


@login_required
def campaign_pick(request: HttpRequest) -> JsonResponse:
    """
    Список кампаний, доступных пользователю для добавления email из карточки компании.
    Менеджер видит только свои, админ — все.
    """
    user: User = request.user
    enforce(user=request.user, resource_type="action", resource="ui:mail:campaigns:pick", context={"path": request.path, "method": request.method})
    qs = Campaign.objects.all().order_by("-created_at")
    if user.role == User.Role.MANAGER:
        qs = qs.filter(created_by=user)
    elif user.role in (User.Role.BRANCH_DIRECTOR, User.Role.SALES_HEAD) and user.branch_id:
        qs = qs.filter(created_by__branch_id=user.branch_id)
    elif user.role not in (User.Role.ADMIN, User.Role.GROUP_MANAGER) and not user.is_superuser:
        # на всякий случай: если роль неожиданная — только свои
        qs = qs.filter(created_by=user)

    items = []
    for c in qs[:200]:
        items.append({"id": str(c.id), "name": c.name, "status": c.status})
    return JsonResponse({"ok": True, "campaigns": items})


@login_required
def campaign_add_email(request: HttpRequest) -> JsonResponse:
    """
    Добавить email в выбранную кампанию (AJAX).
    """
    user: User = request.user
    enforce(user=request.user, resource_type="action", resource="ui:mail:campaigns:add_email", context={"path": request.path, "method": request.method})
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

    from django.utils import timezone as _tz
    now = _tz.now()
    cd = EmailCooldown.objects.filter(created_by=user, email__iexact=email, until_at__gt=now).first()
    if cd:
        return JsonResponse({"ok": False, "error": f"Этот email временно нельзя использовать (до {cd.until_at:%d.%m.%Y %H:%M})."}, status=400)

    if Unsubscribe.objects.filter(email__iexact=email).exists():
        CampaignRecipient.objects.get_or_create(
            campaign=camp,
            email=email,
            defaults={"status": CampaignRecipient.Status.UNSUBSCRIBED},
        )
        return JsonResponse({"ok": True, "status": "unsubscribed", "message": "Email в отписках — добавлен как «Отписался»."})

    # company_id (если валидный UUID) — сохраняем для контекста, но не падаем при мусоре
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
        # Безопасное поведение: не переотправляем тем, кому уже отправлено.
        # Повторяем автоматически только FAILED -> PENDING.
        if r.status == CampaignRecipient.Status.FAILED:
            r.status = CampaignRecipient.Status.PENDING
            r.last_error = ""
            if company_uuid and not r.company_id:
                r.company_id = company_uuid
            r.save(update_fields=["status", "last_error", "company_id", "updated_at"])
            return JsonResponse({"ok": True, "status": "pending", "message": "Email был с ошибкой — возвращён в очередь."})
        return JsonResponse({"ok": True, "status": r.status, "message": "Email уже есть в кампании. Повторная отправка не выполняется автоматически."})

    return JsonResponse({"ok": True, "status": r.status, "message": "Email добавлен." if created else "Email уже был в кампании."})


@login_required
def campaign_recipient_add(request: HttpRequest, campaign_id) -> HttpResponse:
    enforce(user=request.user, resource_type="action", resource="ui:mail:campaigns:recipients:add", context={"path": request.path, "method": request.method})
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

    # Cooldown: защита от повторного использования email после очистки кампании
    from django.utils import timezone as _tz
    now = _tz.now()
    cd = EmailCooldown.objects.filter(created_by=user, email__iexact=email, until_at__gt=now).first()
    if cd:
        messages.error(request, f"Этот email временно нельзя использовать для рассылки (до {cd.until_at:%d.%m.%Y %H:%M}).")
        return redirect("campaign_detail", campaign_id=camp.id)

    if Unsubscribe.objects.filter(email__iexact=email).exists():
        CampaignRecipient.objects.get_or_create(
            campaign=camp,
            email=email,
            defaults={"status": CampaignRecipient.Status.UNSUBSCRIBED},
        )
        messages.warning(request, f"{email} в списке отписавшихся — добавлен как 'Отписался'.")
        return redirect("campaign_detail", campaign_id=camp.id)

    # ВАЖНО: Безопасное поведение — не сбрасываем SENT обратно в PENDING автоматически.
    # Автоматически возвращаем в очередь только FAILED -> PENDING.
    recipient, created = CampaignRecipient.objects.get_or_create(
        campaign=camp,
        email=email,
        defaults={"status": CampaignRecipient.Status.PENDING}
    )
    # Если получатель уже существовал, но его статус не PENDING - сбрасываем на PENDING
    if not created and recipient.status != CampaignRecipient.Status.PENDING:
        if recipient.status == CampaignRecipient.Status.FAILED:
            recipient.status = CampaignRecipient.Status.PENDING
            recipient.last_error = ""
            recipient.save(update_fields=["status", "last_error", "updated_at"])
            messages.success(request, f"Получатель был с ошибкой: {email} (возвращён в очередь)")
        else:
            messages.info(request, f"Получатель уже есть: {email} (статус: {recipient.get_status_display()}). Повторная отправка не включена.")
    elif created:
        messages.success(request, f"Добавлен получатель: {email}")
    else:
        messages.info(request, f"Получатель уже есть: {email}")
    return redirect("campaign_detail", campaign_id=camp.id)


@login_required
def campaign_recipient_delete(request: HttpRequest, campaign_id, recipient_id) -> HttpResponse:
    enforce(user=request.user, resource_type="action", resource="ui:mail:campaigns:recipients:delete", context={"path": request.path, "method": request.method})
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
def campaign_recipients_bulk_delete(request: HttpRequest, campaign_id) -> HttpResponse:
    """Массовое удаление получателей."""
    enforce(user=request.user, resource_type="action", resource="ui:mail:campaigns:recipients:bulk_delete", context={"path": request.path, "method": request.method})
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

    # Валидация: проверяем формат UUID
    try:
        from uuid import UUID
        valid_ids = [UUID(rid) for rid in recipient_ids]
    except (ValueError, TypeError):
        messages.error(request, "Некорректные ID получателей.")
        return redirect("campaign_detail", campaign_id=camp.id)

    # Проверяем, что все ID принадлежат этой кампании
    recipients = CampaignRecipient.objects.filter(id__in=valid_ids, campaign=camp)
    count = recipients.count()
    if count == 0:
        messages.warning(request, "Не найдено получателей для удаления.")
        return redirect("campaign_detail", campaign_id=camp.id)
    
    # Ограничение на количество удаляемых за раз (защита от случайного массового удаления)
    if count > 500:
        messages.error(request, "За раз можно удалить не более 500 получателей.")
        return redirect("campaign_detail", campaign_id=camp.id)

    recipients.delete()
    messages.success(request, f"Удалено получателей: {count}")
    log_event(actor=user, verb=ActivityEvent.Verb.DELETE, entity_type="campaign", entity_id=camp.id, message=f"Массовое удаление получателей: {count}")
    return redirect("campaign_detail", campaign_id=camp.id)


@login_required
def campaign_generate_recipients(request: HttpRequest, campaign_id) -> HttpResponse:
    """
    MVP: генерируем получателей из email контактов + основного email компании (вся база видна всем пользователям).
    """
    enforce(user=request.user, resource_type="action", resource="ui:mail:campaigns:recipients:generate", context={"path": request.path, "method": request.method})
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
    
    # Чекбоксы: если не отмечены, в POST их нет, поэтому используем get с default
    include_company_email = bool(request.POST.get("include_company_email"))
    include_contact_emails = bool(request.POST.get("include_contact_emails"))
    contact_email_types = form.cleaned_data.get("contact_email_types", [])
    
    # Проверка: хотя бы один источник email должен быть выбран
    if not include_company_email and not include_contact_emails:
        messages.error(request, "Выберите хотя бы один источник email адресов.")
        return redirect("campaign_detail", campaign_id=camp.id)
    
    # Если включены email'ы контактов, но не выбраны типы - берем все
    if include_contact_emails and not contact_email_types:
        contact_email_types = [choice[0] for choice in ContactEmail.EmailType.choices]

    # Компании (вся база видна всем пользователям) + простая сегментация (MVP)
    company_qs = Company.objects.all()
    
    # Филиал: по умолчанию филиал пользователя, если не указан
    # Для менеджеров/РОП/директора филиала - только их филиал
    branch = (request.POST.get("branch") or "").strip()
    if not branch and user.branch_id:
        branch = str(user.branch_id)
    
    # Проверка прав: менеджеры/РОП/директора филиала могут выбирать только свой филиал
    if user.role in (User.Role.MANAGER, User.Role.SALES_HEAD, User.Role.BRANCH_DIRECTOR) and user.branch_id:
        if branch and branch != str(user.branch_id):
            messages.error(request, "Вы можете выбрать только свой филиал.")
            return redirect("campaign_detail", campaign_id=camp.id)
        branch = str(user.branch_id)
    
    # Ответственный: по умолчанию текущий пользователь, если не указан
    responsible = (request.POST.get("responsible") or "").strip()
    if not responsible:
        responsible = str(user.id)
    
    # Статус: множественный выбор
    statuses = request.POST.getlist("status")
    
    # Сфера: множественный выбор
    spheres = request.POST.getlist("sphere")
    # Преобразуем в список целых чисел, фильтруя пустые значения
    sphere_ids = []
    for s in spheres:
        if s and s.strip():
            try:
                sphere_ids.append(int(s.strip()))
            except (ValueError, TypeError):
                pass
    
    # Применяем фильтры
    if branch:
        company_qs = company_qs.filter(branch_id=branch)
    if responsible:
        company_qs = company_qs.filter(responsible_id=responsible)
    if statuses:
        company_qs = company_qs.filter(status_id__in=statuses)
    if sphere_ids:
        # Фильтруем по сферам: компания попадает, если имеет хотя бы одну из выбранных сфер (OR-логика)
        # Это означает, что компании с несколькими сферами будут обработаны, если хотя бы одна из их сфер выбрана
        # Используем distinct() для исключения дублей при фильтрации по M2M
        company_qs = company_qs.filter(spheres__id__in=sphere_ids).distinct()
    
    # Преобразуем QuerySet в список для использования в __in
    # distinct() уже применен выше, если были сферы
    company_ids = list(company_qs.order_by().values_list("id", flat=True).distinct())
    
    # "Защита от дурака": перепроверяем, что итоговый набор компаний соответствует ВСЕМ выбранным фильтрам.
    # Это страхует от неожиданных эффектов (M2M join, типы параметров, ручные правки в БД и т.п.)
    if company_ids and (branch or responsible or statuses or sphere_ids):
        valid_qs = Company.objects.filter(id__in=company_ids)
        if branch:
            valid_qs = valid_qs.filter(branch_id=branch)
        if responsible:
            valid_qs = valid_qs.filter(responsible_id=responsible)
        if statuses:
            valid_qs = valid_qs.filter(status_id__in=statuses)
        if sphere_ids:
            valid_qs = valid_qs.filter(spheres__id__in=sphere_ids)
        valid_company_ids = list(valid_qs.values_list("id", flat=True).distinct())
        if len(valid_company_ids) != len(company_ids):
            logger.warning(
                f"Campaign {camp.id}: Filter re-check removed {len(company_ids) - len(valid_company_ids)} companies "
                f"(branch={branch!r}, responsible={responsible!r}, statuses={statuses}, spheres={sphere_ids})."
            )
            company_ids = valid_company_ids

    if sphere_ids:
        if company_ids:
            logger.info(f"Campaign {camp.id}: Filtered by spheres {sphere_ids}, found {len(company_ids)} companies")
        else:
            logger.info(f"Campaign {camp.id}: No companies found matching sphere filter {sphere_ids}")

    # Явное предупреждение: если выбран "Любой" филиал, в рассылку могут попасть компании из других регионов.
    if not branch and user.role in (User.Role.ADMIN, User.Role.GROUP_MANAGER):
        messages.warning(request, "Филиал: «Любой». В рассылку могут попасть компании из других регионов.")

    created = 0
    skipped_cooldown = 0

    from django.utils import timezone as _tz
    now = _tz.now()
    cooldown_emails = {
        (e or "").strip().lower()
        for e in EmailCooldown.objects.filter(created_by=user, until_at__gt=now).values_list("email", flat=True)
    }

    # Собираем кандидатов (email -> (contact_id, company_id)) с приоритетом контактных email
    candidates: dict[str, tuple[str | None, str | None]] = {}

    if include_contact_emails and company_ids:
        emails_qs = ContactEmail.objects.filter(contact__company_id__in=company_ids)
        if contact_email_types:
            emails_qs = emails_qs.filter(type__in=contact_email_types)
        for value, contact_id, company_id in emails_qs.values_list("value", "contact_id", "contact__company_id").iterator():
            email = (value or "").strip().lower()
            if not email or email in candidates:
                continue
            candidates[email] = (str(contact_id) if contact_id else None, str(company_id) if company_id else None)
            if len(candidates) >= (limit * 3):
                break

    if include_company_email and company_ids:
        # Основной email компании
        for email_value, company_id in Company.objects.filter(id__in=company_ids).values_list("email", "id").iterator():
            email = (email_value or "").strip().lower()
            if not email or email in candidates:
                continue
            candidates[email] = (None, str(company_id))
            if len(candidates) >= (limit * 3):
                break

        # Дополнительные email компании (CompanyEmail)
        from companies.models import CompanyEmail

        for email_value, company_id in CompanyEmail.objects.filter(company_id__in=company_ids).values_list("value", "company_id").iterator():
            email = (email_value or "").strip().lower()
            if not email or email in candidates:
                continue
            candidates[email] = (None, str(company_id))
            if len(candidates) >= (limit * 3):
                break

    if not candidates:
        messages.info(request, "Email адреса не найдены по выбранным источникам/фильтрам.")
    else:
        # cooldown
        for e in list(candidates.keys()):
            if e in cooldown_emails:
                skipped_cooldown += 1
                candidates.pop(e, None)

        # Отписки — одним запросом
        unsub_set = set(Unsubscribe.objects.filter(email__in=list(candidates.keys())).values_list("email", flat=True))
        unsub_set = {(e or "").strip().lower() for e in unsub_set if (e or "").strip()}

        # Уже существующие получатели кампании — одним запросом
        existing_set = set(
            CampaignRecipient.objects.filter(campaign=camp, email__in=list(candidates.keys())).values_list("email", flat=True)
        )
        existing_set = {(e or "").strip().lower() for e in existing_set if (e or "").strip()}

        to_create = []
        for email, (contact_id, company_id) in candidates.items():
            if created >= limit:
                break
            if email in existing_set:
                continue
            status = CampaignRecipient.Status.UNSUBSCRIBED if email in unsub_set else CampaignRecipient.Status.PENDING
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

        # Если email уже был в кампании, но теперь он в Unsubscribe — помечаем как UNSUBSCRIBED
        if unsub_set:
            CampaignRecipient.objects.filter(campaign=camp, email__in=list(unsub_set)).exclude(
                status=CampaignRecipient.Status.UNSUBSCRIBED
            ).update(status=CampaignRecipient.Status.UNSUBSCRIBED, updated_at=now)

    # НЕ меняем статус автоматически - пользователь должен нажать "Старт" вручную
    # camp.status остается DRAFT (или текущий статус)
    # Убеждаемся, что sphere_ids - это список целых чисел (не строки, не одно число)
    normalized_sphere_ids = []
    if sphere_ids:
        for sid in sphere_ids:
            try:
                normalized_sphere_ids.append(int(sid))
            except (ValueError, TypeError):
                # Пропускаем невалидные значения
                pass
    
    camp.filter_meta = {
        "branch": branch,
        "responsible": responsible,
        "status": statuses,
        "sphere": normalized_sphere_ids,  # Сохраняем список целых чисел
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
    log_event(actor=user, verb=ActivityEvent.Verb.UPDATE, entity_type="campaign", entity_id=camp.id, message="Сгенерированы получатели", meta={"added": created})
    return redirect("campaign_detail", campaign_id=camp.id)


@login_required
def campaign_recipients_reset(request: HttpRequest, campaign_id) -> HttpResponse:
    """
    Вернуть получателей для повторной рассылки.
    По умолчанию: FAILED → PENDING (без повторной отправки тем, кому уже отправлено).
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
        enforce(user=request.user, resource_type="action", resource="ui:mail:campaigns:recipients:reset_failed", context={"path": request.path, "method": request.method})
        qs = camp.recipients.filter(status=CampaignRecipient.Status.FAILED)
        updated = qs.update(status=CampaignRecipient.Status.PENDING, last_error="")
        messages.success(request, f"Возвращено в очередь (только ошибки): {updated}. Нажмите «Старт», чтобы снова запустить рассылку.")
        log_event(
            actor=user,
            verb=ActivityEvent.Verb.UPDATE,
            entity_type="campaign",
            entity_id=camp.id,
            message="Сброс статусов получателей (только ошибки)",
            meta={"reset": updated, "scope": "failed"},
        )
    else:
        # scope == "all"
        enforce(user=request.user, resource_type="action", resource="ui:mail:campaigns:recipients:reset_all", context={"path": request.path, "method": request.method})
        if user.role != User.Role.ADMIN and not user.is_superuser:
            messages.error(request, "Недостаточно прав для повторной отправки всем.")
            return redirect("campaign_detail", campaign_id=camp.id)
        qs = camp.recipients.filter(status__in=[CampaignRecipient.Status.SENT, CampaignRecipient.Status.FAILED])
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

    # ВАЖНО: «Вернуть в очередь» не должно автоматически стартовать отправку.
    # Переводим кампанию в DRAFT и отменяем запись в очереди (если была).
    camp.status = Campaign.Status.DRAFT
    camp.save(update_fields=["status", "updated_at"])
    queue_entry = getattr(camp, "queue_entry", None)
    if queue_entry and queue_entry.status in (CampaignQueue.Status.PENDING, CampaignQueue.Status.PROCESSING):
        queue_entry.status = CampaignQueue.Status.CANCELLED
        queue_entry.completed_at = timezone.now()
        queue_entry.save(update_fields=["status", "completed_at"])
    return redirect("campaign_detail", campaign_id=camp.id)


@login_required
def campaign_clear(request: HttpRequest, campaign_id) -> HttpResponse:
    """
    Очистить кампанию от получателей. Перед очисткой ставим cooldown на email (по умолчанию 3 дня).
    """
    enforce(user=request.user, resource_type="action", resource="ui:mail:campaigns:clear", context={"path": request.path, "method": request.method})
    user: User = request.user
    camp = get_object_or_404(Campaign, id=campaign_id)
    if not _can_manage_campaign(user, camp):
        messages.error(request, "Доступ запрещён.")
        return redirect("campaigns")
    if request.method != "POST":
        return redirect("campaign_detail", campaign_id=camp.id)

    from django.utils import timezone as _tz
    now = _tz.now()
    until = now + _tz.timedelta(days=COOLDOWN_DAYS_DEFAULT)

    emails = list(camp.recipients.values_list("email", flat=True))
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

    # Если кампания была в очереди — отменяем
    queue_entry = getattr(camp, "queue_entry", None)
    if queue_entry and queue_entry.status in (CampaignQueue.Status.PENDING, CampaignQueue.Status.PROCESSING):
        queue_entry.status = CampaignQueue.Status.CANCELLED
        queue_entry.completed_at = timezone.now()
        queue_entry.save(update_fields=["status", "completed_at"])
    messages.success(request, f"Кампания очищена. Удалено получателей: {removed}. Повторно эти email можно использовать через {COOLDOWN_DAYS_DEFAULT} дн.")
    log_event(actor=user, verb=ActivityEvent.Verb.UPDATE, entity_type="campaign", entity_id=camp.id, message="Очищена кампания", meta={"removed": removed, "cooldown_days": COOLDOWN_DAYS_DEFAULT})
    return redirect("campaign_detail", campaign_id=camp.id)


@csrf_exempt
def unsubscribe(request: HttpRequest, token: str) -> HttpResponse:
    """
    Отписка по токену.
    """
    token = (token or "").strip()
    t = UnsubscribeToken.objects.filter(token=token).first()
    email = (t.email if t else "").strip().lower()
    if email:
        # One-click отписка может приходить POST'ом без CSRF (List-Unsubscribe-Post)
        reason = "unsubscribe" if request.method == "POST" else "user"
        Unsubscribe.objects.update_or_create(
            email=email,
            defaults={"source": "token", "reason": reason, "last_seen_at": timezone.now()},
        )
    return render(request, "ui/mail/unsubscribe.html", {"email": email})


@login_required
def campaign_send_step(request: HttpRequest, campaign_id) -> HttpResponse:
    """
    Отправка батчами, чтобы не зависать.
    """
    if request.method != "POST":
        return redirect("campaign_detail", campaign_id=campaign_id)

    user: User = request.user
    enforce(user=request.user, resource_type="action", resource="ui:mail:campaigns:send_step", context={"path": request.path, "method": request.method})
    camp = get_object_or_404(Campaign, id=campaign_id)
    if not _can_manage_campaign(user, camp):
        messages.error(request, "Доступ запрещён.")
        return redirect("campaigns")

    smtp_cfg = GlobalMailAccount.load()
    if not smtp_cfg.is_enabled:
        messages.error(request, "SMTP не настроен администратором (Почта → Настройки).")
        return redirect("campaign_detail", campaign_id=camp.id)

    # Отправка идет от создателя кампании (Reply-To/подпись). Управлять кампанией могут директор/РОП/админ,
    # поэтому проверяем email создателя кампании, а не текущего пользователя.
    creator = getattr(camp, "created_by", None)
    creator_email = ((creator.email if creator else "") or "").strip()
    if not creator_email:
        messages.error(
            request,
            "У создателя кампании не задан email (Reply-To). Укажите email в профиле создателя кампании и повторите попытку.",
        )
        return redirect("campaign_detail", campaign_id=camp.id)

    # Ограничение по рабочему времени (9:00-18:00 МСК) — чтобы письма не уходили ночью
    from mailer.tasks import _is_working_hours
    from zoneinfo import ZoneInfo
    now = timezone.now()
    if not _is_working_hours(now):
        msk_now = now.astimezone(ZoneInfo("Europe/Moscow"))
        messages.error(
            request,
            f"Сейчас вне рабочего времени. Текущее время МСК: {msk_now.strftime('%H:%M')}. Отправка возможна с 09:00 до 18:00 МСК.",
        )
        return redirect("campaign_detail", campaign_id=camp.id)

    # Защита от случайной массовой отправки:
    # - Требуем явный флаг подтверждения из формы (UI ставит его автоматически),
    #   но это защитит от случайных POST из другого места.
    if (request.POST.get("confirm_send") or "").strip() != "1":
        messages.error(request, "Подтвердите отправку (кнопка отправки должна быть нажата осознанно).")
        return redirect("campaign_detail", campaign_id=camp.id)

    pending_count = camp.recipients.filter(status=CampaignRecipient.Status.PENDING).count()
    if pending_count == 0:
        messages.info(request, "Очередь пуста.")
        # Закрываем запись в очереди, если она есть
        queue_entry = getattr(camp, "queue_entry", None)
        if queue_entry and queue_entry.status in (CampaignQueue.Status.PROCESSING, CampaignQueue.Status.PENDING):
            queue_entry.status = CampaignQueue.Status.COMPLETED
            queue_entry.completed_at = timezone.now()
            queue_entry.save(update_fields=["status", "completed_at"])
        # Если кампания была в процессе — считаем завершенной
        if camp.status == Campaign.Status.SENDING:
            camp.status = Campaign.Status.SENT
            camp.save(update_fields=["status", "updated_at"])
        return redirect("campaign_detail", campaign_id=camp.id)

    # Celery-only: ручной шаг не отправляет SMTP напрямую — только ставит в очередь и триггерит обработку.
    if camp.status in (Campaign.Status.DRAFT, Campaign.Status.STOPPED, Campaign.Status.PAUSED):
        camp.status = Campaign.Status.READY
        camp.save(update_fields=["status", "updated_at"])

    queue_entry, _ = CampaignQueue.objects.get_or_create(
        campaign=camp,
        defaults={"status": CampaignQueue.Status.PENDING, "priority": 0},
    )
    if queue_entry.status == CampaignQueue.Status.CANCELLED:
        queue_entry.status = CampaignQueue.Status.PENDING
        queue_entry.queued_at = timezone.now()
        queue_entry.save(update_fields=["status", "queued_at"])

    try:
        from mailer.tasks import send_pending_emails
        send_pending_emails.delay()
    except Exception:
        # не критично: beat подхватит
        pass

    messages.success(request, "Отправка запущена. Кампания в очереди, письма будут отправляться в рабочее время и с учетом лимитов.")
    log_event(actor=user, verb=ActivityEvent.Verb.UPDATE, entity_type="campaign", entity_id=camp.id, message="Ручной запуск отправки (celery)")
    return redirect("campaign_detail", campaign_id=camp.id)


@login_required
def campaign_start(request: HttpRequest, campaign_id) -> HttpResponse:
    """
    Запуск автоматической рассылки кампании.
    """
    if request.method != "POST":
        return redirect("campaign_detail", campaign_id=campaign_id)
    
    user: User = request.user
    enforce(user=request.user, resource_type="action", resource="ui:mail:campaigns:start", context={"path": request.path, "method": request.method})
    camp = get_object_or_404(Campaign, id=campaign_id)
    if not _can_manage_campaign(user, camp):
        messages.error(request, "Доступ запрещён.")
        return redirect("campaigns")
    
    smtp_cfg = GlobalMailAccount.load()
    if not smtp_cfg.is_enabled:
        messages.error(request, "SMTP не настроен администратором (Почта → Настройки).")
        return redirect("campaign_detail", campaign_id=camp.id)

    if not ((camp.created_by.email if camp.created_by else "") or "").strip():
        messages.error(request, "У создателя кампании не задан email (Reply-To). Укажите email в профиле создателя кампании.")
        return redirect("campaign_detail", campaign_id=camp.id)
    
    pending_count = camp.recipients.filter(status=CampaignRecipient.Status.PENDING).count()
    if pending_count == 0:
        messages.error(request, "Нет писем в очереди для отправки.")
        return redirect("campaign_detail", campaign_id=camp.id)
    
    # Устанавливаем статус READY
    if camp.status in (Campaign.Status.DRAFT, Campaign.Status.PAUSED, Campaign.Status.STOPPED):
        camp.status = Campaign.Status.READY
        camp.save(update_fields=["status", "updated_at"])
        
        # Проверяем, можно ли начать сразу (нет других активных кампаний)
        from mailer.tasks import _is_working_hours
        is_working = _is_working_hours()
        
        # Проверяем, есть ли другие кампании в обработке или в очереди
        active_queues = CampaignQueue.objects.filter(
            status__in=(CampaignQueue.Status.PENDING, CampaignQueue.Status.PROCESSING),
            campaign__status__in=(Campaign.Status.READY, Campaign.Status.SENDING)
        ).exclude(campaign=camp).count()
        
        # Создаем или обновляем запись в очереди
        queue_entry, created = CampaignQueue.objects.get_or_create(
            campaign=camp,
            defaults={
                "status": CampaignQueue.Status.PENDING,
                "priority": 0,
            }
        )
        if not created:
            if queue_entry.status != CampaignQueue.Status.PENDING:
                queue_entry.status = CampaignQueue.Status.PENDING
                queue_entry.queued_at = timezone.now()
                queue_entry.started_at = None
                queue_entry.completed_at = None
                queue_entry.save(update_fields=["status", "queued_at", "started_at", "completed_at"])
        
        # Определяем позицию в очереди
        queue_position = None
        if queue_entry.status == CampaignQueue.Status.PENDING:
            queue_list = list(CampaignQueue.objects.filter(
                status=CampaignQueue.Status.PENDING,
                campaign__status__in=(Campaign.Status.READY, Campaign.Status.SENDING)
            ).order_by("-priority", "queued_at").values_list("campaign_id", flat=True))
            if camp.id in queue_list:
                queue_position = queue_list.index(camp.id) + 1
        
        if active_queues == 0 and is_working:
            # Не обещаем "началась" синхронно: фактический старт делает воркер (и он же шлёт уведомление).
            messages.success(request, "Рассылка поставлена в очередь и начнётся в ближайшее время.")
        else:
            # Ставим в очередь
            if queue_position:
                messages.success(request, f"Рассылка поставлена в очередь. Ваша позиция: {queue_position}. Вы получите уведомление, когда начнется отправка.")
            else:
                messages.success(request, "Рассылка поставлена в очередь. Вы получите уведомление, когда начнется отправка.")
            notify(
                user=user,
                kind=Notification.Kind.SYSTEM,
                title="Рассылка в очереди",
                body=f"Кампания '{camp.name}' поставлена в очередь" + (f" (позиция: {queue_position})" if queue_position else "") + ". Вы получите уведомление, когда начнется отправка.",
                url=f"/mail/campaigns/{camp.id}/",
                dedupe_seconds=900,
            )
        
        log_event(actor=user, verb=ActivityEvent.Verb.UPDATE, entity_type="campaign", entity_id=camp.id, message="Запущена автоматическая рассылка")
    
    return redirect("campaign_detail", campaign_id=camp.id)


@login_required
def campaign_pause(request: HttpRequest, campaign_id) -> HttpResponse:
    """
    Постановка кампании на паузу.
    """
    if request.method != "POST":
        return redirect("campaign_detail", campaign_id=campaign_id)
    
    user: User = request.user
    enforce(user=request.user, resource_type="action", resource="ui:mail:campaigns:pause", context={"path": request.path, "method": request.method})
    camp = get_object_or_404(Campaign, id=campaign_id)
    if not _can_manage_campaign(user, camp):
        messages.error(request, "Доступ запрещён.")
        return redirect("campaigns")
    
    if camp.status in (Campaign.Status.SENDING, Campaign.Status.READY):
        camp.status = Campaign.Status.PAUSED
        camp.save(update_fields=["status", "updated_at"])
        
        # Отменяем запись в очереди только при РУЧНОЙ паузе (когда пользователь нажал кнопку)
        # Это позволяет очереди перейти на следующего
        queue_entry = getattr(camp, "queue_entry", None)
        if queue_entry:
            if queue_entry.status == CampaignQueue.Status.PROCESSING:
                # Если обрабатывается, отменяем и очередь перейдет на следующего
                queue_entry.status = CampaignQueue.Status.CANCELLED
                queue_entry.completed_at = timezone.now()
                queue_entry.save(update_fields=["status", "completed_at"])
            elif queue_entry.status == CampaignQueue.Status.PENDING:
                # Если в очереди, просто отменяем
                queue_entry.status = CampaignQueue.Status.CANCELLED
                queue_entry.completed_at = timezone.now()
                queue_entry.save(update_fields=["status", "completed_at"])
        
        messages.success(request, "Рассылка поставлена на паузу. Очередь перешла на следующую кампанию.")
        log_event(actor=user, verb=ActivityEvent.Verb.UPDATE, entity_type="campaign", entity_id=camp.id, message="Рассылка поставлена на паузу вручную")
    
    return redirect("campaign_detail", campaign_id=camp.id)


@login_required
def campaign_resume(request: HttpRequest, campaign_id) -> HttpResponse:
    """
    Продолжение рассылки кампании после паузы.
    """
    if request.method != "POST":
        return redirect("campaign_detail", campaign_id=campaign_id)
    
    user: User = request.user
    enforce(user=request.user, resource_type="action", resource="ui:mail:campaigns:resume", context={"path": request.path, "method": request.method})
    camp = get_object_or_404(Campaign, id=campaign_id)
    if not _can_manage_campaign(user, camp):
        messages.error(request, "Доступ запрещён.")
        return redirect("campaigns")
    
    if camp.status == Campaign.Status.PAUSED:
        pending_count = camp.recipients.filter(status=CampaignRecipient.Status.PENDING).count()
        if pending_count > 0:
            if not ((camp.created_by.email if camp.created_by else "") or "").strip():
                messages.error(request, "У создателя кампании не задан email (Reply-To). Укажите email в профиле создателя кампании.")
                return redirect("campaign_detail", campaign_id=camp.id)

            # Если дневной лимит уже исчерпан сегодня — не стартуем сейчас, ставим deferred_until на завтра.
            smtp_cfg = GlobalMailAccount.load()
            per_user_daily_limit = smtp_cfg.per_user_daily_limit or PER_USER_DAILY_LIMIT_DEFAULT
            start_day_utc, end_day_utc, _ = msk_day_bounds(timezone.now())
            sent_today_user = SendLog.objects.filter(
                provider="smtp_global",
                status="sent",
                campaign__created_by=camp.created_by,
                created_at__gte=start_day_utc,
                created_at__lt=end_day_utc,
            ).count()
            if per_user_daily_limit and sent_today_user >= per_user_daily_limit:
                from mailer.utils import get_next_send_window_start
                from mailer.constants import DEFER_REASON_DAILY_LIMIT
                next_run = get_next_send_window_start(always_tomorrow=True)
                next_run_str = next_run.strftime("%H:%M")
                next_run_date = next_run.strftime("%d.%m")
                queue_entry, _ = CampaignQueue.objects.get_or_create(
                    campaign=camp,
                    defaults={"status": CampaignQueue.Status.PENDING, "priority": 0},
                )
                queue_entry.status = CampaignQueue.Status.PENDING
                queue_entry.started_at = None
                queue_entry.completed_at = None
                queue_entry.queued_at = timezone.now()
                queue_entry.deferred_until = next_run
                queue_entry.defer_reason = DEFER_REASON_DAILY_LIMIT
                queue_entry.save(update_fields=["status", "started_at", "completed_at", "queued_at", "deferred_until", "defer_reason"])
                camp.status = Campaign.Status.READY
                camp.save(update_fields=["status", "updated_at"])
                messages.info(
                    request,
                    f"Сегодня лимит исчерпан ({sent_today_user}/{per_user_daily_limit}). "
                    f"Продолжим завтра в {next_run_str} ({next_run_date}).",
                )
                log_event(actor=user, verb=ActivityEvent.Verb.UPDATE, entity_type="campaign", entity_id=camp.id, message="Resume: лимит исчерпан, отложено на завтра")
                return redirect("campaign_detail", campaign_id=camp.id)

            camp.status = Campaign.Status.READY
            camp.save(update_fields=["status", "updated_at"])
            
            # Проверяем, можно ли начать сразу
            from mailer.tasks import _is_working_hours
            is_working = _is_working_hours()
            
            # Проверяем, есть ли другие кампании в обработке или в очереди
            active_queues = CampaignQueue.objects.filter(
                status__in=(CampaignQueue.Status.PENDING, CampaignQueue.Status.PROCESSING),
                campaign__status__in=(Campaign.Status.READY, Campaign.Status.SENDING)
            ).exclude(campaign=camp).count()
            
            # Создаем или обновляем запись в очереди
            queue_entry, created = CampaignQueue.objects.get_or_create(
                campaign=camp,
                defaults={
                    "status": CampaignQueue.Status.PENDING,
                    "priority": 0,
                }
            )
            if not created:
                if queue_entry.status != CampaignQueue.Status.PENDING:
                    queue_entry.status = CampaignQueue.Status.PENDING
                    queue_entry.queued_at = timezone.now()
                    queue_entry.started_at = None
                    queue_entry.completed_at = None
                    queue_entry.save(update_fields=["status", "queued_at", "started_at", "completed_at"])
            
            # Определяем позицию в очереди
            queue_position = None
            if queue_entry.status == CampaignQueue.Status.PENDING:
                queue_list = list(CampaignQueue.objects.filter(
                    status=CampaignQueue.Status.PENDING,
                    campaign__status__in=(Campaign.Status.READY, Campaign.Status.SENDING)
                ).order_by("-priority", "queued_at").values_list("campaign_id", flat=True))
                if camp.id in queue_list:
                    queue_position = queue_list.index(camp.id) + 1
            
            if active_queues == 0 and is_working:
                # Фактический старт делает воркер (и он же шлёт уведомление о старте).
                messages.success(request, "Рассылка возобновлена и поставлена в очередь. Старт — в ближайшее время.")
            else:
                # Ставим в очередь
                if queue_position:
                    messages.success(request, f"Рассылка поставлена в очередь. Ваша позиция: {queue_position}. Вы получите уведомление, когда начнется отправка.")
                else:
                    messages.success(request, "Рассылка поставлена в очередь. Вы получите уведомление, когда начнется отправка.")
                notify(
                    user=user,
                    kind=Notification.Kind.SYSTEM,
                    title="Рассылка в очереди",
                    body=f"Кампания '{camp.name}' поставлена в очередь" + (f" (позиция: {queue_position})" if queue_position else "") + ". Вы получите уведомление, когда начнется отправка.",
                    url=f"/mail/campaigns/{camp.id}/",
                    dedupe_seconds=900,
                )
            
            log_event(actor=user, verb=ActivityEvent.Verb.UPDATE, entity_type="campaign", entity_id=camp.id, message="Рассылка возобновлена")
        else:
            camp.status = Campaign.Status.SENT
            camp.save(update_fields=["status", "updated_at"])
            messages.info(request, "Нет писем в очереди. Кампания завершена.")
    
    return redirect("campaign_detail", campaign_id=camp.id)


@login_required
def campaign_test_send(request: HttpRequest, campaign_id) -> HttpResponse:
    """
    Отправка тестового письма кампании на себя (на from_email/smtp_username).
    
    ВАЖНО: Тестовое письмо НЕ должно менять статус получателей (CampaignRecipient.status).
    Тестовое письмо отправляется только для проверки содержимого и не создает запись CampaignRecipient.
    """
    user: User = request.user
    enforce(user=request.user, resource_type="action", resource="ui:mail:campaigns:test_send", context={"path": request.path, "method": request.method})
    camp = get_object_or_404(Campaign, id=campaign_id)
    if not _can_manage_campaign(user, camp):
        messages.error(request, "Доступ запрещён.")
        return redirect("campaigns")

    smtp_cfg = GlobalMailAccount.load()
    if not smtp_cfg.is_enabled:
        messages.error(request, "SMTP не настроен администратором (Почта → Настройки).")
        return redirect("campaign_detail", campaign_id=camp.id)

    creator = getattr(camp, "created_by", None)
    creator_email = ((creator.email if creator else "") or "").strip()
    if not creator or not creator_email:
        messages.error(
            request,
            "У создателя кампании не задан email (Reply-To) или не найден создатель. Укажите email в профиле создателя и повторите попытку.",
        )
        return redirect("campaign_detail", campaign_id=camp.id)

    to_email = (user.email or "").strip()
    if not to_email:
        messages.error(request, "Некуда отправить тест (не задан email).")
        return redirect("campaign_detail", campaign_id=camp.id)

    # ВАЖНО: Сохраняем текущие статусы получателей, чтобы убедиться, что они не изменятся
    # (хотя в этой функции мы их не трогаем, это дополнительная защита)
    recipients_before = list(camp.recipients.values_list("id", "status"))

    # Тест должен максимально совпадать с реальной рассылкой: подпись/Reply-To от создателя кампании,
    # при этом письмо отправляем на текущего пользователя.
    base_html, base_text = apply_signature(
        user=creator,
        body_html=(camp.body_html or ""),
        body_text=(html_to_text(camp.body_html or "") or camp.body_text or ""),
    )

    # Добавляем отписку и заголовки как в реальной рассылке (для корректного теста)
    from mailer.mail_content import ensure_unsubscribe_tokens, build_unsubscribe_url, append_unsubscribe_footer
    token = ensure_unsubscribe_tokens([to_email]).get(to_email.strip().lower(), "")
    unsub_url = build_unsubscribe_url(token) if token else ""
    if unsub_url:
        base_html, base_text = append_unsubscribe_footer(body_html=base_html, body_text=base_text, unsubscribe_url=unsub_url)

    msg = build_message(
        account=MailAccount.objects.get_or_create(user=creator)[0],
        to_email=to_email,
        subject=f"[ТЕСТ] {camp.subject}",
        body_text=(base_text or ""),
        body_html=(base_html or ""),
        from_email=((smtp_cfg.from_email or "").strip() or (smtp_cfg.smtp_username or "").strip()),
        from_name=((camp.sender_name or "").strip() or (smtp_cfg.from_name or "CRM ПРОФИ").strip()),
        reply_to=creator_email,
        attachment=camp.attachment if camp.attachment else None,
    )
    if unsub_url:
        msg["List-Unsubscribe"] = f"<{unsub_url}>"
        msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    msg["X-Tag"] = f"test:campaign:{camp.id}"
    try:
        send_via_smtp(smtp_cfg, msg)
        # ВАЖНО: Создаем SendLog БЕЗ recipient, чтобы не было связи с получателями
        SendLog.objects.create(campaign=camp, recipient=None, account=None, provider="smtp_global", status="sent", message_id=str(msg["Message-ID"]))
        messages.success(request, f"Тестовое письмо отправлено на {to_email}.")
    except Exception as ex:
        SendLog.objects.create(campaign=camp, recipient=None, account=None, provider="smtp_global", status="failed", error=str(ex))
        messages.error(request, f"Ошибка тестовой отправки: {ex}")
    
    # ВАЖНО: Проверяем, что статусы получателей не изменились (дополнительная защита)
    recipients_after = list(camp.recipients.values_list("id", "status"))
    if recipients_before != recipients_after:
        # Если статусы изменились - это ошибка, логируем и восстанавливаем
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"КРИТИЧЕСКАЯ ОШИБКА: Статусы получателей изменились при отправке тестового письма! Campaign: {camp.id}")
        # Восстанавливаем статусы
        for r_id, old_status in recipients_before:
            CampaignRecipient.objects.filter(id=r_id).update(status=old_status)
    
    return redirect("campaign_detail", campaign_id=camp.id)
