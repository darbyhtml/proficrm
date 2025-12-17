from __future__ import annotations

from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from accounts.models import User
from accounts.scope import apply_company_scope
from audit.models import ActivityEvent
from audit.service import log_event
from companies.models import Company
from accounts.models import Branch
from companies.models import CompanySphere, CompanyStatus, ContactEmail, Contact
from mailer.forms import CampaignForm, CampaignGenerateRecipientsForm, MailAccountForm
from mailer.models import Campaign, CampaignRecipient, MailAccount, SendLog, Unsubscribe, UnsubscribeToken
from mailer.smtp_sender import build_message, send_via_smtp
from mailer.utils import html_to_text


def _require_admin(user: User) -> bool:
    return bool(user.is_authenticated and user.is_active and (user.is_superuser or user.role == User.Role.ADMIN))


@login_required
def mail_settings(request: HttpRequest) -> HttpResponse:
    """
    Настройки SMTP для текущего пользователя.
    """
    user: User = request.user
    account, _ = MailAccount.objects.get_or_create(user=user)

    if request.method == "POST":
        form = MailAccountForm(request.POST, instance=account)
        if form.is_valid():
            # Проверка на наличие ключа шифрования, если вводят пароль
            if (form.cleaned_data.get("smtp_password") or "").strip():
                from django.conf import settings
                if not getattr(settings, "MAILER_FERNET_KEY", ""):
                    messages.error(request, "MAILER_FERNET_KEY не задан. Нельзя сохранить пароль.")
                    return redirect("mail_settings")
            form.save()
            if "test_send" in request.POST:
                # тестовое письмо на себя
                to_email = account.from_email or account.smtp_username or user.email
                if not to_email:
                    messages.error(request, "Не указан email отправителя для теста.")
                    return redirect("mail_settings")
                from django.conf import settings
                rel = reverse("mail_settings")
                base = (getattr(settings, "PUBLIC_BASE_URL", "") or "").strip().rstrip("/")
                test_url = (base + rel) if base else request.build_absolute_uri(rel)
                msg = build_message(
                    account=account,
                    to_email=to_email,
                    subject="CRM: тест отправки",
                    body_text=f"Тестовое письмо из CRM.\n\nЕсли вы это читаете — SMTP настроен.\n\n{test_url}",
                    body_html=f"<p>Тестовое письмо из CRM.</p><p>Если вы это читаете — SMTP настроен.</p><p><a href='{test_url}'>{test_url}</a></p>",
                    unsubscribe_url=test_url,
                )
                try:
                    send_via_smtp(account, msg)
                    messages.success(request, f"Тестовое письмо отправлено на {to_email}.")
                except Exception as ex:
                    messages.error(request, f"Ошибка отправки: {ex}")
                return redirect("mail_settings")
            messages.success(request, "Настройки почты сохранены.")
            return redirect("mail_settings")
    else:
        form = MailAccountForm(instance=account)

    from django.conf import settings
    key_missing = not bool(getattr(settings, "MAILER_FERNET_KEY", "") or "")
    return render(request, "ui/mail/settings.html", {"form": form, "account": account, "key_missing": key_missing})


@login_required
def campaigns(request: HttpRequest) -> HttpResponse:
    user: User = request.user
    qs = Campaign.objects.all().order_by("-created_at")
    if user.role == User.Role.MANAGER:
        qs = qs.filter(created_by=user)
    return render(request, "ui/mail/campaigns.html", {"campaigns": qs})


@login_required
def campaign_create(request: HttpRequest) -> HttpResponse:
    user: User = request.user
    if request.method == "POST":
        form = CampaignForm(request.POST)
        if form.is_valid():
            camp: Campaign = form.save(commit=False)
            camp.created_by = user
            camp.status = Campaign.Status.DRAFT
            camp.save()
            messages.success(request, "Кампания создана.")
            return redirect("campaign_detail", campaign_id=camp.id)
    else:
        form = CampaignForm()
    return render(request, "ui/mail/campaign_form.html", {"form": form, "mode": "create"})


@login_required
def campaign_edit(request: HttpRequest, campaign_id) -> HttpResponse:
    user: User = request.user
    camp = get_object_or_404(Campaign, id=campaign_id)
    if user.role == User.Role.MANAGER and camp.created_by_id != user.id:
        messages.error(request, "Доступ запрещён.")
        return redirect("campaigns")

    if request.method == "POST":
        form = CampaignForm(request.POST, instance=camp)
        if form.is_valid():
            form.save()
            messages.success(request, "Кампания сохранена.")
            return redirect("campaign_detail", campaign_id=camp.id)
    else:
        form = CampaignForm(instance=camp)
    return render(request, "ui/mail/campaign_form.html", {"form": form, "mode": "edit", "campaign": camp})


@login_required
def campaign_detail(request: HttpRequest, campaign_id) -> HttpResponse:
    user: User = request.user
    camp = get_object_or_404(Campaign, id=campaign_id)
    if user.role == User.Role.MANAGER and camp.created_by_id != user.id:
        messages.error(request, "Доступ запрещён.")
        return redirect("campaigns")

    counts = {
        "pending": camp.recipients.filter(status=CampaignRecipient.Status.PENDING).count(),
        "sent": camp.recipients.filter(status=CampaignRecipient.Status.SENT).count(),
        "failed": camp.recipients.filter(status=CampaignRecipient.Status.FAILED).count(),
        "unsub": camp.recipients.filter(status=CampaignRecipient.Status.UNSUBSCRIBED).count(),
        "total": camp.recipients.count(),
    }
    recent = camp.recipients.order_by("-updated_at")[:30]

    return render(
        request,
        "ui/mail/campaign_detail.html",
        {
            "campaign": camp,
            "counts": counts,
            "recent": recent,
            "branches": Branch.objects.order_by("name"),
            "responsibles": User.objects.order_by("last_name", "first_name"),
            "statuses": CompanyStatus.objects.order_by("name"),
            "spheres": CompanySphere.objects.order_by("name"),
        },
    )


@login_required
def campaign_generate_recipients(request: HttpRequest, campaign_id) -> HttpResponse:
    """
    MVP: генерируем получателей из email контактов по доступным компаниям (scope пользователя).
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

    # Компании, видимые пользователю + простая сегментация (MVP)
    company_qs = apply_company_scope(Company.objects.all(), user)
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
    company_ids = company_qs.values_list("id", flat=True)

    # Берём email контактов, связанных с компаниями
    emails_qs = (
        ContactEmail.objects.filter(contact__company_id__in=company_ids)
        .select_related("contact", "contact__company")
        .order_by("value")
    )

    created = 0
    for e in emails_qs.iterator():
        email = (e.value or "").strip().lower()
        if not email:
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
        if created >= limit:
            break

    camp.status = Campaign.Status.READY
    camp.filter_meta = {
        "branch": branch,
        "responsible": responsible,
        "status": status,
        "sphere": sphere,
        "limit": limit,
    }
    camp.save(update_fields=["status", "updated_at"])

    messages.success(request, f"Получатели сгенерированы: +{created}")
    log_event(actor=user, verb=ActivityEvent.Verb.UPDATE, entity_type="campaign", entity_id=camp.id, message="Сгенерированы получатели", meta={"added": created})
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
    user: User = request.user
    camp = get_object_or_404(Campaign, id=campaign_id)
    if user.role == User.Role.MANAGER and camp.created_by_id != user.id:
        messages.error(request, "Доступ запрещён.")
        return redirect("campaigns")

    account = getattr(user, "mail_account", None)
    if not account or not account.is_enabled:
        messages.error(request, "Настройте почту в разделе Почта → Настройки.")
        return redirect("campaign_detail", campaign_id=camp.id)

    # Rate limit (MVP): дневной/минутный по аккаунту
    from django.utils import timezone as _tz
    now = _tz.now()
    sent_last_min = SendLog.objects.filter(account=account, status="sent", created_at__gte=now - _tz.timedelta(minutes=1)).count()
    sent_today = SendLog.objects.filter(account=account, status="sent", created_at__date=now.date()).count()
    if sent_today >= account.rate_per_day:
        messages.error(request, "Достигнут дневной лимит отправки.")
        return redirect("campaign_detail", campaign_id=camp.id)
    if sent_last_min >= account.rate_per_minute:
        messages.error(request, "Слишком часто. Подожди минуту (лимит в минуту).")
        return redirect("campaign_detail", campaign_id=camp.id)

    # 50 писем за шаг — безопасный MVP, но с учетом лимитов
    allowed = max(1, min(50, account.rate_per_minute - sent_last_min, account.rate_per_day - sent_today))
    batch = list(camp.recipients.filter(status=CampaignRecipient.Status.PENDING)[:allowed])
    if not batch:
        messages.info(request, "Очередь пуста.")
        return redirect("campaign_detail", campaign_id=camp.id)

    sent = 0
    failed = 0
    for r in batch:
        if Unsubscribe.objects.filter(email__iexact=r.email).exists():
            r.status = CampaignRecipient.Status.UNSUBSCRIBED
            r.save(update_fields=["status", "updated_at"])
            continue

        # token for unsubscribe
        import secrets
        tok_obj = UnsubscribeToken.objects.filter(email__iexact=r.email).first()
        if not tok_obj:
            tok_obj = UnsubscribeToken.objects.create(email=r.email, token=secrets.token_urlsafe(32)[:64])
        from django.conf import settings
        rel = reverse("unsubscribe", kwargs={"token": tok_obj.token})
        base = (getattr(settings, "PUBLIC_BASE_URL", "") or "").strip().rstrip("/")
        unsubscribe_url = (base + rel) if base else request.build_absolute_uri(rel)
        footer = f"\n\nОтписаться: {unsubscribe_url}\n"
        auto_plain = html_to_text(camp.body_html or "")
        msg = build_message(
            account=account,
            to_email=r.email,
            subject=camp.subject,
            body_text=(auto_plain or camp.body_text or "") + footer,
            body_html=(camp.body_html or "") + f'<hr><p style="font-size:12px;color:#666">Отписаться: <a href="{unsubscribe_url}">{unsubscribe_url}</a></p>',
            unsubscribe_url=unsubscribe_url,
        )

        try:
            send_via_smtp(account, msg)
            r.status = CampaignRecipient.Status.SENT
            r.last_error = ""
            r.save(update_fields=["status", "last_error", "updated_at"])
            SendLog.objects.create(campaign=camp, recipient=r, account=account, status="sent", message_id=str(msg["Message-ID"]))
            sent += 1
        except Exception as ex:
            r.status = CampaignRecipient.Status.FAILED
            r.last_error = str(ex)[:255]
            r.save(update_fields=["status", "last_error", "updated_at"])
            SendLog.objects.create(campaign=camp, recipient=r, account=account, status="failed", error=str(ex))
            failed += 1

    messages.success(request, f"Отправлено: {sent}, ошибок: {failed}.")
    log_event(actor=user, verb=ActivityEvent.Verb.UPDATE, entity_type="campaign", entity_id=camp.id, message="Отправка батча", meta={"sent": sent, "failed": failed})
    return redirect("campaign_detail", campaign_id=camp.id)


@login_required
def campaign_test_send(request: HttpRequest, campaign_id) -> HttpResponse:
    """
    Отправка тестового письма кампании на себя (на from_email/smtp_username).
    """
    user: User = request.user
    camp = get_object_or_404(Campaign, id=campaign_id)
    if user.role == User.Role.MANAGER and camp.created_by_id != user.id:
        messages.error(request, "Доступ запрещён.")
        return redirect("campaigns")

    account = getattr(user, "mail_account", None)
    if not account or not account.is_enabled:
        messages.error(request, "Настройте почту в разделе Почта → Настройки.")
        return redirect("campaign_detail", campaign_id=camp.id)

    to_email = account.from_email or account.smtp_username or user.email
    if not to_email:
        messages.error(request, "Некуда отправить тест (не задан email).")
        return redirect("campaign_detail", campaign_id=camp.id)

    from django.conf import settings
    rel = reverse("campaign_detail", kwargs={"campaign_id": camp.id})
    base = (getattr(settings, "PUBLIC_BASE_URL", "") or "").strip().rstrip("/")
    link = (base + rel) if base else request.build_absolute_uri(rel)

    msg = build_message(
        account=account,
        to_email=to_email,
        subject=f"[ТЕСТ] {camp.subject}",
        body_text=(html_to_text(camp.body_html or "") or camp.body_text or "") + f"\n\n(Тест) Кампания: {link}\n",
        body_html=(camp.body_html or "") + f'<hr><p style="font-size:12px;color:#666">(Тест) Кампания: <a href="{link}">{link}</a></p>',
        unsubscribe_url=link,
    )
    try:
        send_via_smtp(account, msg)
        SendLog.objects.create(campaign=camp, recipient=None, account=account, status="sent", message_id=str(msg["Message-ID"]))
        messages.success(request, f"Тестовое письмо отправлено на {to_email}.")
    except Exception as ex:
        SendLog.objects.create(campaign=camp, recipient=None, account=account, status="failed", error=str(ex))
        messages.error(request, f"Ошибка тестовой отправки: {ex}")
    return redirect("campaign_detail", campaign_id=camp.id)

from django.shortcuts import render

# Create your views here.
