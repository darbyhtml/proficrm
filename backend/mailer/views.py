from __future__ import annotations

import logging
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import HttpRequest, HttpResponse
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from accounts.models import User
from audit.models import ActivityEvent
from audit.service import log_event
from companies.models import Company
from companies.permissions import get_users_for_lists
from accounts.models import Branch
from companies.models import CompanySphere, CompanyStatus, ContactEmail, Contact
from mailer.forms import CampaignForm, CampaignGenerateRecipientsForm, CampaignRecipientAddForm, MailAccountForm, GlobalMailAccountForm, EmailSignatureForm
from mailer.models import Campaign, CampaignRecipient, MailAccount, GlobalMailAccount, SendLog, Unsubscribe, UnsubscribeToken, EmailCooldown, SmtpBzQuota, CampaignQueue
from mailer.smtp_sender import build_message, send_via_smtp
from mailer.utils import html_to_text
from crm.utils import require_admin

logger = logging.getLogger(__name__)

PER_USER_DAILY_LIMIT = 100  # значение по умолчанию; может быть переопределено в GlobalMailAccount.per_user_daily_limit
COOLDOWN_DAYS_DEFAULT = 3

def _can_manage_campaign(user: User, camp: Campaign) -> bool:
    # Админ — всегда
    if user.role == User.Role.ADMIN:
        return True
    # Менеджер — только свои кампании
    if user.role == User.Role.MANAGER:
        return camp.created_by_id == user.id
    # Остальные роли (директор филиала/роп/управляющий) — пока разрешаем, как и просмотр кампаний.
    return True

def _contains_links(value: str) -> bool:
    v = (value or "").lower()
    return any(x in v for x in ("<a ", "href=", "http://", "https://", "www."))


def _apply_signature(*, user: User, body_html: str, body_text: str) -> tuple[str, str]:
    sig_html = (getattr(user, "email_signature_html", "") or "").strip()
    if not sig_html:
        return body_html, body_text
    sig_text = (html_to_text(sig_html or "") or "").strip()
    new_html = (body_html or "")
    if new_html:
        new_html = new_html + "<br><br>" + sig_html
    else:
        new_html = sig_html
    new_text = (body_text or "")
    if sig_text:
        new_text = (new_text + "\n\n" + sig_text) if new_text else sig_text
    return new_html, new_text


@login_required
def mail_signature(request: HttpRequest) -> HttpResponse:
    """
    Настройка подписи (персональная, для всех пользователей).
    """
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
    user: User = request.user
    is_admin = require_admin(user)
    cfg = GlobalMailAccount.load()

    if request.method == "POST":
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
                msg = build_message(
                    account=cfg,  # type: ignore[arg-type]
                    to_email=to_email,
                    subject="CRM ПРОФИ: тест отправки",
                    body_text="Тестовое письмо из CRM ПРОФИ.\n\nЕсли вы это читаете — SMTP настроен.\n",
                    body_html="<p>Тестовое письмо из CRM ПРОФИ.</p><p>Если вы это читаете — SMTP настроен.</p>",
                    from_email=((cfg.from_email or "").strip() or (cfg.smtp_username or "").strip()),
                    from_name=(cfg.from_name or "CRM ПРОФИ").strip(),
                    reply_to=to_email,
                )
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
def campaigns(request: HttpRequest) -> HttpResponse:
    user: User = request.user
    is_admin = (user.role == User.Role.ADMIN)
    is_group_manager = (user.role == User.Role.GROUP_MANAGER)
    is_branch_director = (user.role == User.Role.BRANCH_DIRECTOR)
    is_sales_head = (user.role == User.Role.SALES_HEAD)
    
    # Фильтрация кампаний по ролям:
    # - Менеджер: только свои
    # - Администратор и управляющий: все
    # - Директор филиала и РОП: все своего филиала
    qs = Campaign.objects.all().order_by("-created_at")
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
        from django.db.models import Count, Q
        now = _tz.now()
        today = now.date()
        
        smtp_cfg = GlobalMailAccount.load()
        per_user_daily_limit = smtp_cfg.per_user_daily_limit or PER_USER_DAILY_LIMIT
        
        # Статистика по пользователям: кто сколько писем отправил сегодня
        user_stats = []
        all_users = User.objects.filter(role__in=[User.Role.MANAGER, User.Role.ADMIN, User.Role.BRANCH_DIRECTOR, User.Role.GROUP_MANAGER]).select_related("branch")
        
        for u in all_users:
            sent_today = SendLog.objects.filter(
                provider="smtp_global",
                status="sent",
                campaign__created_by=u,
                created_at__date=today
            ).count()
            
            # Количество активных кампаний пользователя
            active_campaigns = Campaign.objects.filter(
                created_by=u,
                status__in=[Campaign.Status.READY, Campaign.Status.SENDING]
            ).count()
            
            # Количество ошибок сегодня
            failed_today = SendLog.objects.filter(
                provider="smtp_global",
                status="failed",
                campaign__created_by=u,
                created_at__date=today
            ).count()
            
            # Количество кампаний пользователя
            campaigns_count = Campaign.objects.filter(created_by=u).count()
            
            # Остаток лимита
            remaining = max(0, per_user_daily_limit - sent_today) if per_user_daily_limit else None
            
            user_stats.append({
                "user": u,
                "sent_today": sent_today,
                "failed_today": failed_today,
                "remaining": remaining,
                "limit": per_user_daily_limit,
                "campaigns_count": campaigns_count,
                "active_campaigns": active_campaigns,
                "is_limit_reached": per_user_daily_limit and sent_today >= per_user_daily_limit,
            })
        
        # Сортируем по количеству отправленных писем (по убыванию)
        user_stats.sort(key=lambda x: x["sent_today"], reverse=True)
        
        # Общая статистика
        total_sent_today = SendLog.objects.filter(
            provider="smtp_global",
            status="sent",
            created_at__date=today
        ).count()
        
        total_failed_today = SendLog.objects.filter(
            provider="smtp_global",
            status="failed",
            created_at__date=today
        ).count()
        
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
        }
    
    # Информация о квоте smtp.bz (для всех пользователей)
    quota = SmtpBzQuota.load()
    
    # Информация о лимите пользователя (для всех)
    from django.utils import timezone as _tz
    now = _tz.now()
    today = now.date()
    sent_today_user = SendLog.objects.filter(
        provider="smtp_global",
        status="sent",
        campaign__created_by=user,
        created_at__date=today
    ).count()
    
    smtp_cfg = GlobalMailAccount.load()
    per_user_daily_limit = smtp_cfg.per_user_daily_limit or PER_USER_DAILY_LIMIT
    
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
    
    return render(
        request,
        "ui/mail/campaigns.html",
        {
            "campaigns": qs,
            "is_admin": is_admin,
            "is_group_manager": is_group_manager,
            "is_branch_director": is_branch_director,
            "is_sales_head": is_sales_head,
            "analytics": analytics,
            "quota": quota,
            "user_limit_info": user_limit_info,
            "show_creator_column": show_creator_column,
        }
    )


@login_required
def campaign_create(request: HttpRequest) -> HttpResponse:
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
        {"form": form, "mode": "create", "smtp_from_email": (smtp_cfg.from_email or smtp_cfg.smtp_username or "").strip()},
    )


@login_required
def campaign_edit(request: HttpRequest, campaign_id) -> HttpResponse:
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
    return render(
        request,
        "ui/mail/campaign_form.html",
        {"form": form, "mode": "edit", "campaign": camp, "smtp_from_email": (smtp_cfg.from_email or smtp_cfg.smtp_username or "").strip()},
    )


@login_required
def campaign_detail(request: HttpRequest, campaign_id) -> HttpResponse:
    user: User = request.user
    camp = get_object_or_404(Campaign, id=campaign_id)
    if not _can_manage_campaign(user, camp):
        messages.error(request, "Доступ запрещён.")
        return redirect("campaigns")

    smtp_cfg = GlobalMailAccount.load()
    counts = {
        "pending": camp.recipients.filter(status=CampaignRecipient.Status.PENDING).count(),
        "sent": camp.recipients.filter(status=CampaignRecipient.Status.SENT).count(),
        "failed": camp.recipients.filter(status=CampaignRecipient.Status.FAILED).count(),
        "unsub": camp.recipients.filter(status=CampaignRecipient.Status.UNSUBSCRIBED).count(),
        "total": camp.recipients.count(),
    }
    
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
    allowed_views = {"pending", "sent", "failed", "unsub", "all"}
    if view not in allowed_views:
        view = "pending" if counts["pending"] > 0 else "all"

    # Получаем всех получателей для группировки и пагинации
    all_recipients_qs = camp.recipients.order_by("company_id", "-updated_at")
    if view == "pending":
        all_recipients_qs = all_recipients_qs.filter(status=CampaignRecipient.Status.PENDING)
    elif view == "sent":
        all_recipients_qs = all_recipients_qs.filter(status=CampaignRecipient.Status.SENT)
    elif view == "failed":
        all_recipients_qs = all_recipients_qs.filter(status=CampaignRecipient.Status.FAILED)
    elif view == "unsub":
        all_recipients_qs = all_recipients_qs.filter(status=CampaignRecipient.Status.UNSUBSCRIBED)
    
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
    sent_last_hour = SendLog.objects.filter(provider="smtp_global", status="sent", created_at__gte=now - _tz.timedelta(hours=1)).count()
    sent_today = SendLog.objects.filter(provider="smtp_global", status="sent", created_at__date=now.date()).count()
    sent_today_user = SendLog.objects.filter(provider="smtp_global", status="sent", campaign__created_by=user, created_at__date=now.date()).count()
    
    per_user_daily_limit = smtp_cfg.per_user_daily_limit or PER_USER_DAILY_LIMIT
    is_working_time = _is_working_hours(now)
    
    # Количество кампаний пользователя
    user_campaigns_count = Campaign.objects.filter(created_by=user).count()
    user_active_campaigns = Campaign.objects.filter(
        created_by=user,
        status__in=[Campaign.Status.READY, Campaign.Status.SENDING]
    ).count()
    
    # Текущее московское время для отображения
    msk_tz = ZoneInfo("Europe/Moscow")
    msk_now = now.astimezone(msk_tz)
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
        "failed_today": SendLog.objects.filter(provider="smtp_global", status="failed", created_at__date=now.date()).count(),
        "failed_today_campaign": SendLog.objects.filter(campaign=camp, provider="smtp_global", status="failed", created_at__date=now.date()).count(),
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
            "emails_available": emails_available,
            "counts": counts,
            "recent": recent,
            "smtp_from_email": (smtp_cfg.from_email or smtp_cfg.smtp_username or "").strip(),
            "smtp_from_name_default": (smtp_cfg.from_name or "CRM ПРОФИ").strip(),
            "recipient_add_form": CampaignRecipientAddForm(),
            "generate_form": CampaignGenerateRecipientsForm(),
            "branches": Branch.objects.order_by("name"),
            "responsibles": get_users_for_lists(request.user),
            "statuses": CompanyStatus.objects.order_by("name"),
            "spheres": CompanySphere.objects.order_by("name"),
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
            "queue_entry": getattr(camp, "queue_entry", None),
            "emails_available": emails_available,
            "rate_per_hour": max_per_hour,
        },
    )


@login_required
def campaign_delete(request: HttpRequest, campaign_id) -> HttpResponse:
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

    # Берём ближайшую "активную" кампанию пользователя:
    # - SENDING — во время отправки
    # - SENT    — сразу после завершения (чтобы менеджер увидел результат, даже если писем было мало)
    qs = Campaign.objects.filter(created_by=user).order_by("-updated_at")
    active = (
        qs.filter(status__in=[Campaign.Status.SENDING, Campaign.Status.SENT]).first()
    )
    if not active:
        return JsonResponse({"ok": True, "active": None})

    pending = active.recipients.filter(status=CampaignRecipient.Status.PENDING).count()
    sent = active.recipients.filter(status=CampaignRecipient.Status.SENT).count()
    failed = active.recipients.filter(status=CampaignRecipient.Status.FAILED).count()
    total = active.recipients.count()
    done = sent + failed
    percent = 0
    if total > 0:
        percent = int(round((done / total) * 100))

    # Проверяем, не упёрлись ли в дневной лимит
    from django.utils import timezone as _tz

    now = _tz.now()
    smtp_cfg = GlobalMailAccount.load()
    sent_today = SendLog.objects.filter(
        provider="smtp_global", status="sent", created_at__date=now.date()
    ).count()
    sent_today_user = SendLog.objects.filter(
        provider="smtp_global",
        status="sent",
        campaign__created_by=user,
        created_at__date=now.date(),
    ).count()
    per_user_daily_limit = smtp_cfg.per_user_daily_limit or PER_USER_DAILY_LIMIT
    limit_reached = False
    if per_user_daily_limit and sent_today_user >= per_user_daily_limit and pending > 0:
        limit_reached = True

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
    qs = Campaign.objects.all().order_by("-created_at")
    if user.role == User.Role.MANAGER:
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
        r.status = CampaignRecipient.Status.PENDING
        r.last_error = ""
        if company_uuid and not r.company_id:
            r.company_id = company_uuid
        r.save(update_fields=["status", "last_error", "company_id", "updated_at"])
        return JsonResponse({"ok": True, "status": "pending", "message": "Email добавлен, статус сброшен в очередь."})

    return JsonResponse({"ok": True, "status": r.status, "message": "Email добавлен." if created else "Email уже был в кампании."})


@login_required
def campaign_recipient_add(request: HttpRequest, campaign_id) -> HttpResponse:
    user: User = request.user
    camp = get_object_or_404(Campaign, id=campaign_id)
    if user.role == User.Role.MANAGER and camp.created_by_id != user.id:
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

    # ВАЖНО: При добавлении получателя всегда создаем/обновляем со статусом PENDING
    # даже если получатель уже существовал (например, был удален и добавлен снова)
    recipient, created = CampaignRecipient.objects.get_or_create(
        campaign=camp,
        email=email,
        defaults={"status": CampaignRecipient.Status.PENDING}
    )
    # Если получатель уже существовал, но его статус не PENDING - сбрасываем на PENDING
    if not created and recipient.status != CampaignRecipient.Status.PENDING:
        recipient.status = CampaignRecipient.Status.PENDING
        recipient.last_error = ""
        recipient.save(update_fields=["status", "last_error", "updated_at"])
        messages.success(request, f"Получатель добавлен заново: {email} (статус сброшен на 'В очереди')")
    elif created:
        messages.success(request, f"Добавлен получатель: {email}")
    else:
        messages.info(request, f"Получатель уже есть: {email}")
    return redirect("campaign_detail", campaign_id=camp.id)


@login_required
def campaign_recipient_delete(request: HttpRequest, campaign_id, recipient_id) -> HttpResponse:
    user: User = request.user
    camp = get_object_or_404(Campaign, id=campaign_id)
    if user.role == User.Role.MANAGER and camp.created_by_id != user.id:
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
    user: User = request.user
    camp = get_object_or_404(Campaign, id=campaign_id)
    if user.role == User.Role.MANAGER and camp.created_by_id != user.id:
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
    user: User = request.user
    camp = get_object_or_404(Campaign, id=campaign_id)
    if user.role == User.Role.MANAGER and camp.created_by_id != user.id:
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
    branch = (request.POST.get("branch") or "").strip()
    responsible = (request.POST.get("responsible") or "").strip()
    status = (request.POST.get("status") or "").strip()
    sphere = (request.POST.get("sphere") or "").strip()
    if branch:
        company_qs = company_qs.filter(branch_id=branch)
    if responsible:
        company_qs = company_qs.filter(responsible_id=responsible)
    if status:
        company_qs = company_qs.filter(status_id=status)
    if sphere:
        company_qs = company_qs.filter(spheres__id=sphere)
    # Важно: при фильтрации по m2m (spheres) будут дубли без distinct()
    # Преобразуем QuerySet в список для использования в __in
    company_ids = list(company_qs.order_by().values_list("id", flat=True).distinct())

    created = 0
    skipped_cooldown = 0

    from django.utils import timezone as _tz
    now = _tz.now()
    cooldown_emails = set(
        EmailCooldown.objects.filter(created_by=user, until_at__gt=now).values_list("email", flat=True)
    )
    
    # 1) Берём email контактов, связанных с компаниями (если включено)
    if include_contact_emails:
        # Если нет компаний после фильтрации, пропускаем
        if not company_ids:
            messages.info(request, "Нет компаний, соответствующих фильтрам.")
        else:
            emails_qs = (
                ContactEmail.objects.filter(contact__company_id__in=company_ids)
                .select_related("contact", "contact__company")
            )
            # Фильтруем по типам email'ов, если указаны
            if contact_email_types:
                emails_qs = emails_qs.filter(type__in=contact_email_types)
            emails_qs = emails_qs.order_by("value")

            for e in emails_qs.iterator():
                if created >= limit:
                    break
                email = (e.value or "").strip().lower()
                if not email:
                    continue
                if email in cooldown_emails:
                    skipped_cooldown += 1
                    continue
                # Проверяем, что контакт существует и привязан к компании
                if not e.contact or not e.contact.company_id:
                    continue
                if Unsubscribe.objects.filter(email__iexact=email).exists():
                    CampaignRecipient.objects.get_or_create(
                        campaign=camp,
                        email=email,
                        defaults={"status": CampaignRecipient.Status.UNSUBSCRIBED, "contact_id": e.contact_id, "company_id": e.contact.company_id},
                    )
                    continue
                _, was_created = CampaignRecipient.objects.get_or_create(
                    campaign=camp,
                    email=email,
                    defaults={"contact_id": e.contact_id, "company_id": e.contact.company_id},
                )
                if was_created:
                    created += 1

    # 2) Добавляем основной email компании (если включено и лимит не достигнут)
    if include_company_email and created < limit and company_ids:
        for c in Company.objects.filter(id__in=company_ids).only("id", "email").iterator():
            if created >= limit:
                break
            email = (getattr(c, "email", "") or "").strip().lower()
            if not email:
                continue
            if email in cooldown_emails:
                skipped_cooldown += 1
                continue
            # Пропускаем, если этот email уже добавлен из контактов
            if include_contact_emails:
                # Проверяем, не был ли уже добавлен этот email из контактов
                if CampaignRecipient.objects.filter(campaign=camp, email__iexact=email).exists():
                    continue
            if Unsubscribe.objects.filter(email__iexact=email).exists():
                CampaignRecipient.objects.get_or_create(
                    campaign=camp,
                    email=email,
                    defaults={"status": CampaignRecipient.Status.UNSUBSCRIBED, "contact_id": None, "company_id": c.id},
                )
                continue
            _, was_created = CampaignRecipient.objects.get_or_create(
                campaign=camp,
                email=email,
                defaults={"contact_id": None, "company_id": c.id},
            )
            if was_created:
                created += 1
    
    # 3) Добавляем дополнительные email адреса компании из CompanyEmail (если включено и лимит не достигнут)
    if include_company_email and created < limit and company_ids:
        from companies.models import CompanyEmail
        company_emails_qs = (
            CompanyEmail.objects.filter(company_id__in=company_ids)
            .select_related("company")
            .order_by("company_id", "order", "value")
        )
        
        for ce in company_emails_qs.iterator():
            if created >= limit:
                break
            email = (ce.value or "").strip().lower()
            if not email:
                continue
            if email in cooldown_emails:
                skipped_cooldown += 1
                continue
            # Пропускаем, если этот email уже добавлен из контактов или основного email компании
            if CampaignRecipient.objects.filter(campaign=camp, email__iexact=email).exists():
                continue
            if Unsubscribe.objects.filter(email__iexact=email).exists():
                CampaignRecipient.objects.get_or_create(
                    campaign=camp,
                    email=email,
                    defaults={"status": CampaignRecipient.Status.UNSUBSCRIBED, "contact_id": None, "company_id": ce.company_id},
                )
                continue
            _, was_created = CampaignRecipient.objects.get_or_create(
                campaign=camp,
                email=email,
                defaults={"contact_id": None, "company_id": ce.company_id},
            )
            if was_created:
                created += 1

    camp.status = Campaign.Status.READY
    camp.filter_meta = {
        "branch": branch,
        "responsible": responsible,
        "status": status,
        "sphere": sphere,
        "limit": limit,
        "include_company_email": include_company_email,
        "include_contact_emails": include_contact_emails,
        "contact_email_types": contact_email_types,
    }
    camp.save(update_fields=["status", "filter_meta", "updated_at"])

    msg = f"Получатели сгенерированы: +{created}"
    if skipped_cooldown:
        msg += f" (пропущено из-за паузы: {skipped_cooldown})"
    messages.success(request, msg)
    log_event(actor=user, verb=ActivityEvent.Verb.UPDATE, entity_type="campaign", entity_id=camp.id, message="Сгенерированы получатели", meta={"added": created})
    return redirect("campaign_detail", campaign_id=camp.id)


@login_required
def campaign_recipients_reset(request: HttpRequest, campaign_id) -> HttpResponse:
    """
    Вернуть получателей для повторной рассылки: SENT/FAILED → PENDING (UNSUBSCRIBED не трогаем).
    """
    user: User = request.user
    camp = get_object_or_404(Campaign, id=campaign_id)
    if not _can_manage_campaign(user, camp):
        messages.error(request, "Доступ запрещён.")
        return redirect("campaigns")
    if request.method != "POST":
        return redirect("campaign_detail", campaign_id=camp.id)

    qs = camp.recipients.filter(status__in=[CampaignRecipient.Status.SENT, CampaignRecipient.Status.FAILED])
    updated = qs.update(status=CampaignRecipient.Status.PENDING, last_error="")
    messages.success(request, f"Возвращено в очередь: {updated}.")
    log_event(actor=user, verb=ActivityEvent.Verb.UPDATE, entity_type="campaign", entity_id=camp.id, message="Сброс статусов получателей", meta={"reset": updated})
    # Кампания снова готова к отправке
    camp.status = Campaign.Status.READY
    camp.save(update_fields=["status", "updated_at"])
    return redirect("campaign_detail", campaign_id=camp.id)


@login_required
def campaign_clear(request: HttpRequest, campaign_id) -> HttpResponse:
    """
    Очистить кампанию от получателей. Перед очисткой ставим cooldown на email (по умолчанию 3 дня).
    """
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
    messages.success(request, f"Кампания очищена. Удалено получателей: {removed}. Повторно эти email можно использовать через {COOLDOWN_DAYS_DEFAULT} дн.")
    log_event(actor=user, verb=ActivityEvent.Verb.UPDATE, entity_type="campaign", entity_id=camp.id, message="Очищена кампания", meta={"removed": removed, "cooldown_days": COOLDOWN_DAYS_DEFAULT})
    return redirect("campaign_detail", campaign_id=camp.id)


@login_required
def unsubscribe(request: HttpRequest, token: str) -> HttpResponse:
    """
    Отписка по токену.
    """
    token = (token or "").strip()
    t = UnsubscribeToken.objects.filter(token=token).first()
    email = (t.email if t else "").strip().lower()
    if email:
        Unsubscribe.objects.get_or_create(email=email)
    return render(request, "ui/mail/unsubscribe.html", {"email": email})


@login_required
def campaign_send_step(request: HttpRequest, campaign_id) -> HttpResponse:
    """
    Отправка батчами, чтобы не зависать.
    """
    if request.method != "POST":
        return redirect("campaign_detail", campaign_id=campaign_id)

    user: User = request.user
    camp = get_object_or_404(Campaign, id=campaign_id)
    if user.role == User.Role.MANAGER and camp.created_by_id != user.id:
        messages.error(request, "Доступ запрещён.")
        return redirect("campaigns")

    smtp_cfg = GlobalMailAccount.load()
    if not smtp_cfg.is_enabled:
        messages.error(request, "SMTP не настроен администратором (Почта → Настройки).")
        return redirect("campaign_detail", campaign_id=camp.id)

    # Защита от случайной массовой отправки:
    # - Требуем явный флаг подтверждения из формы (UI ставит его автоматически),
    #   но это защитит от случайных POST из другого места.
    if (request.POST.get("confirm_send") or "").strip() != "1":
        messages.error(request, "Подтвердите отправку (кнопка отправки должна быть нажата осознанно).")
        return redirect("campaign_detail", campaign_id=camp.id)

    # Получаем лимиты из API
    quota = SmtpBzQuota.load()
    if quota.last_synced_at and not quota.sync_error and quota.emails_limit > 0:
        max_per_hour = quota.max_per_hour or 100
        emails_available = quota.emails_available or 0
        emails_limit = quota.emails_limit or 15000
    else:
        max_per_hour = 100
        emails_available = 15000
        emails_limit = 15000
    
    # Rate limit: глобальные лимиты из API
    from django.utils import timezone as _tz
    now = _tz.now()
    sent_last_hour = SendLog.objects.filter(provider="smtp_global", status="sent", created_at__gte=now - _tz.timedelta(hours=1)).count()
    sent_today = SendLog.objects.filter(provider="smtp_global", status="sent", created_at__date=now.date()).count()
    sent_today_user = SendLog.objects.filter(provider="smtp_global", status="sent", campaign__created_by=user, created_at__date=now.date()).count()

    per_user_daily_limit = smtp_cfg.per_user_daily_limit or PER_USER_DAILY_LIMIT
    if per_user_daily_limit and sent_today_user >= per_user_daily_limit:
        messages.error(request, f"Достигнут лимит отправки {per_user_daily_limit} писем в день для вашего аккаунта.")
        return redirect("campaign_detail", campaign_id=camp.id)
    if emails_available <= 0:
        messages.error(request, f"Квота исчерпана: доступно {emails_available} из {emails_limit} писем.")
        return redirect("campaign_detail", campaign_id=camp.id)
    if sent_last_hour >= max_per_hour:
        messages.error(request, f"Слишком часто. Подожди час (лимит в час: {max_per_hour}).")
        return redirect("campaign_detail", campaign_id=camp.id)

    # 50 писем за шаг — безопасный MVP, но с учетом лимитов
    allowed = max(
        1,
        min(
            50,
            max_per_hour - sent_last_hour,
            emails_available,
            (per_user_daily_limit - sent_today_user) if per_user_daily_limit else 50,
        ),
    )
    batch = list(camp.recipients.filter(status=CampaignRecipient.Status.PENDING)[:allowed])
    if not batch:
        messages.info(request, "Очередь пуста.")
        # Если все отправлено, обновляем статус кампании
        if camp.recipients.filter(status=CampaignRecipient.Status.PENDING).count() == 0:
            if camp.status == Campaign.Status.SENDING:
                camp.status = Campaign.Status.SENT
                camp.save(update_fields=["status", "updated_at"])
        return redirect("campaign_detail", campaign_id=camp.id)

    # Обновляем статус кампании на SENDING при начале отправки
    if camp.status == Campaign.Status.READY:
        camp.status = Campaign.Status.SENDING
        camp.save(update_fields=["status", "updated_at"])

    sent = 0
    failed = 0
    for r in batch:
        if Unsubscribe.objects.filter(email__iexact=r.email).exists():
            r.status = CampaignRecipient.Status.UNSUBSCRIBED
            r.save(update_fields=["status", "updated_at"])
            continue

        auto_plain = html_to_text(camp.body_html or "")
        base_html, base_text = _apply_signature(user=user, body_html=(camp.body_html or ""), body_text=(auto_plain or camp.body_text or ""))
        msg = build_message(
            account=MailAccount.objects.get_or_create(user=user)[0],  # fallback fields for build_message
            to_email=r.email,
            subject=camp.subject,
            body_text=(base_text or ""),
            body_html=(base_html or ""),
            from_email=((smtp_cfg.from_email or "").strip() or (smtp_cfg.smtp_username or "").strip()),
            from_name=((camp.sender_name or "").strip() or (smtp_cfg.from_name or "CRM ПРОФИ").strip()),
            reply_to=(user.email or "").strip(),
            attachment=camp.attachment if camp.attachment else None,
        )

        try:
            send_via_smtp(smtp_cfg, msg)
            r.status = CampaignRecipient.Status.SENT
            r.last_error = ""
            r.save(update_fields=["status", "last_error", "updated_at"])
            SendLog.objects.create(campaign=camp, recipient=r, account=None, provider="smtp_global", status="sent", message_id=str(msg["Message-ID"]))
            sent += 1
        except Exception as ex:
            r.status = CampaignRecipient.Status.FAILED
            r.last_error = str(ex)[:255]
            r.save(update_fields=["status", "last_error", "updated_at"])
            SendLog.objects.create(campaign=camp, recipient=r, account=None, provider="smtp_global", status="failed", error=str(ex))
            failed += 1

    messages.success(request, f"Отправлено: {sent}, ошибок: {failed}.")
    log_event(actor=user, verb=ActivityEvent.Verb.UPDATE, entity_type="campaign", entity_id=camp.id, message="Отправка батча", meta={"sent": sent, "failed": failed})
    
    # Если все отправлено, обновляем статус кампании на SENT
    pending_count = camp.recipients.filter(status=CampaignRecipient.Status.PENDING).count()
    if pending_count == 0 and camp.status == Campaign.Status.SENDING:
        camp.status = Campaign.Status.SENT
        camp.save(update_fields=["status", "updated_at"])

    return redirect("campaign_detail", campaign_id=camp.id)


@login_required
def campaign_start(request: HttpRequest, campaign_id) -> HttpResponse:
    """
    Запуск автоматической рассылки кампании.
    """
    if request.method != "POST":
        return redirect("campaign_detail", campaign_id=campaign_id)
    
    user: User = request.user
    camp = get_object_or_404(Campaign, id=campaign_id)
    if not _can_manage_campaign(user, camp):
        messages.error(request, "Доступ запрещён.")
        return redirect("campaigns")
    
    smtp_cfg = GlobalMailAccount.load()
    if not smtp_cfg.is_enabled:
        messages.error(request, "SMTP не настроен администратором (Почта → Настройки).")
        return redirect("campaign_detail", campaign_id=camp.id)
    
    pending_count = camp.recipients.filter(status=CampaignRecipient.Status.PENDING).count()
    if pending_count == 0:
        messages.error(request, "Нет писем в очереди для отправки.")
        return redirect("campaign_detail", campaign_id=camp.id)
    
    # Проверка рабочего времени
    from mailer.tasks import _is_working_hours
    if not _is_working_hours():
        messages.warning(request, "Рассылка возможна только в рабочее время (9:00-18:00 МСК). Кампания будет запущена, но отправка начнется автоматически в рабочее время.")
    
    # Устанавливаем статус READY или SENDING в зависимости от наличия pending
    if camp.status in (Campaign.Status.DRAFT, Campaign.Status.PAUSED, Campaign.Status.STOPPED):
        camp.status = Campaign.Status.READY if pending_count > 0 else Campaign.Status.SENT
        camp.save(update_fields=["status", "updated_at"])
        
        # Создаем или обновляем запись в очереди
        queue_entry, created = CampaignQueue.objects.get_or_create(
            campaign=camp,
            defaults={
                "status": CampaignQueue.Status.PENDING,
                "priority": 0,
            }
        )
        if not created and queue_entry.status == CampaignQueue.Status.CANCELLED:
            queue_entry.status = CampaignQueue.Status.PENDING
            queue_entry.queued_at = timezone.now()
            queue_entry.save(update_fields=["status", "queued_at"])
        
        messages.success(request, "Рассылка запущена. Отправка будет происходить автоматически в рабочее время (9:00-18:00 МСК).")
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
    camp = get_object_or_404(Campaign, id=campaign_id)
    if not _can_manage_campaign(user, camp):
        messages.error(request, "Доступ запрещён.")
        return redirect("campaigns")
    
    if camp.status in (Campaign.Status.SENDING, Campaign.Status.READY):
        camp.status = Campaign.Status.PAUSED
        camp.save(update_fields=["status", "updated_at"])
        messages.success(request, "Рассылка поставлена на паузу.")
        log_event(actor=user, verb=ActivityEvent.Verb.UPDATE, entity_type="campaign", entity_id=camp.id, message="Рассылка поставлена на паузу")
    
    return redirect("campaign_detail", campaign_id=camp.id)


@login_required
def campaign_resume(request: HttpRequest, campaign_id) -> HttpResponse:
    """
    Продолжение рассылки кампании после паузы.
    """
    if request.method != "POST":
        return redirect("campaign_detail", campaign_id=campaign_id)
    
    user: User = request.user
    camp = get_object_or_404(Campaign, id=campaign_id)
    if not _can_manage_campaign(user, camp):
        messages.error(request, "Доступ запрещён.")
        return redirect("campaigns")
    
    if camp.status == Campaign.Status.PAUSED:
        pending_count = camp.recipients.filter(status=CampaignRecipient.Status.PENDING).count()
        if pending_count > 0:
            camp.status = Campaign.Status.READY
            camp.save(update_fields=["status", "updated_at"])
            messages.success(request, "Рассылка возобновлена. Отправка будет происходить автоматически в рабочее время (9:00-18:00 МСК).")
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
    camp = get_object_or_404(Campaign, id=campaign_id)
    if user.role == User.Role.MANAGER and camp.created_by_id != user.id:
        messages.error(request, "Доступ запрещён.")
        return redirect("campaigns")

    smtp_cfg = GlobalMailAccount.load()
    if not smtp_cfg.is_enabled:
        messages.error(request, "SMTP не настроен администратором (Почта → Настройки).")
        return redirect("campaign_detail", campaign_id=camp.id)

    to_email = (user.email or "").strip()
    if not to_email:
        messages.error(request, "Некуда отправить тест (не задан email).")
        return redirect("campaign_detail", campaign_id=camp.id)

    # ВАЖНО: Сохраняем текущие статусы получателей, чтобы убедиться, что они не изменятся
    # (хотя в этой функции мы их не трогаем, это дополнительная защита)
    recipients_before = list(camp.recipients.values_list("id", "status"))

    base_html, base_text = _apply_signature(user=user, body_html=(camp.body_html or ""), body_text=(html_to_text(camp.body_html or "") or camp.body_text or ""))

    msg = build_message(
        account=MailAccount.objects.get_or_create(user=user)[0],
        to_email=to_email,
        subject=f"[ТЕСТ] {camp.subject}",
        body_text=(base_text or ""),
        body_html=(base_html or ""),
        from_email=((smtp_cfg.from_email or "").strip() or (smtp_cfg.smtp_username or "").strip()),
        from_name=((camp.sender_name or "").strip() or (smtp_cfg.from_name or "CRM ПРОФИ").strip()),
        reply_to=(user.email or "").strip(),
        attachment=camp.attachment if camp.attachment else None,
    )
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
