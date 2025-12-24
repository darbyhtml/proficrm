from __future__ import annotations

from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from accounts.models import User
from audit.models import ActivityEvent
from audit.service import log_event
from companies.models import Company
from accounts.models import Branch
from companies.models import CompanySphere, CompanyStatus, ContactEmail, Contact
from mailer.forms import CampaignForm, CampaignGenerateRecipientsForm, CampaignRecipientAddForm, MailAccountForm, GlobalMailAccountForm, EmailSignatureForm
from mailer.models import Campaign, CampaignRecipient, MailAccount, GlobalMailAccount, SendLog, Unsubscribe, UnsubscribeToken
from mailer.smtp_sender import build_message, send_via_smtp
from mailer.utils import html_to_text


def _require_admin(user: User) -> bool:
    return bool(user.is_authenticated and user.is_active and (user.is_superuser or user.role == User.Role.ADMIN))

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
    is_admin = _require_admin(user)
    cfg = GlobalMailAccount.load()

    if request.method == "POST":
        if not is_admin:
            messages.error(request, "Доступ запрещён.")
            return redirect("mail_settings")
        form = GlobalMailAccountForm(request.POST, instance=cfg)
        if form.is_valid():
            # Проверка на наличие ключа шифрования, если вводят пароль
            if (form.cleaned_data.get("smtp_password") or "").strip():
                from django.conf import settings
                if not getattr(settings, "MAILER_FERNET_KEY", ""):
                    messages.error(request, "MAILER_FERNET_KEY не задан. Нельзя сохранить пароль.")
                    return redirect("mail_settings")
            form.save()
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
    return render(
        request,
        "ui/mail/settings.html",
        {
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
    qs = Campaign.objects.all().order_by("-created_at")
    if user.role == User.Role.MANAGER:
        qs = qs.filter(created_by=user)
    return render(request, "ui/mail/campaigns.html", {"campaigns": qs})


@login_required
def campaign_create(request: HttpRequest) -> HttpResponse:
    user: User = request.user
    smtp_cfg = GlobalMailAccount.load()
    if request.method == "POST":
        form = CampaignForm(request.POST)
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
    if user.role == User.Role.MANAGER and camp.created_by_id != user.id:
        messages.error(request, "Доступ запрещён.")
        return redirect("campaigns")
    smtp_cfg = GlobalMailAccount.load()

    if request.method == "POST":
        form = CampaignForm(request.POST, instance=camp)
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
    if user.role == User.Role.MANAGER and camp.created_by_id != user.id:
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
    recent = camp.recipients.order_by("-updated_at")[:30]

    return render(
        request,
        "ui/mail/campaign_detail.html",
        {
            "campaign": camp,
            "counts": counts,
            "recent": recent,
            "smtp_from_email": (smtp_cfg.from_email or smtp_cfg.smtp_username or "").strip(),
            "smtp_from_name_default": (smtp_cfg.from_name or "CRM ПРОФИ").strip(),
            "recipient_add_form": CampaignRecipientAddForm(),
            "branches": Branch.objects.order_by("name"),
            "responsibles": User.objects.order_by("last_name", "first_name"),
            "statuses": CompanyStatus.objects.order_by("name"),
            "spheres": CompanySphere.objects.order_by("name"),
        },
    )


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

    if Unsubscribe.objects.filter(email__iexact=email).exists():
        CampaignRecipient.objects.get_or_create(
            campaign=camp,
            email=email,
            defaults={"status": CampaignRecipient.Status.UNSUBSCRIBED},
        )
        messages.warning(request, f"{email} в списке отписавшихся — добавлен как 'Отписался'.")
        return redirect("campaign_detail", campaign_id=camp.id)

    _, created = CampaignRecipient.objects.get_or_create(campaign=camp, email=email)
    if created:
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
    company_ids = company_qs.order_by().values_list("id", flat=True).distinct()

    # 1) Берём email контактов, связанных с компаниями
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

    # 2) Добавляем основной email компании (если он заполнен)
    if created < limit:
        for c in Company.objects.filter(id__in=company_ids).only("id", "email").iterator():
            email = (getattr(c, "email", "") or "").strip().lower()
            if not email:
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
    camp.save(update_fields=["status", "filter_meta", "updated_at"])

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

    smtp_cfg = GlobalMailAccount.load()
    if not smtp_cfg.is_enabled:
        messages.error(request, "SMTP не настроен администратором (Почта → Настройки).")
        return redirect("campaign_detail", campaign_id=camp.id)

    # Rate limit (MVP): глобальные лимиты
    from django.utils import timezone as _tz
    now = _tz.now()
    sent_last_min = SendLog.objects.filter(provider="smtp_global", status="sent", created_at__gte=now - _tz.timedelta(minutes=1)).count()
    sent_today = SendLog.objects.filter(provider="smtp_global", status="sent", created_at__date=now.date()).count()
    if sent_today >= smtp_cfg.rate_per_day:
        messages.error(request, "Достигнут дневной лимит отправки.")
        return redirect("campaign_detail", campaign_id=camp.id)
    if sent_last_min >= smtp_cfg.rate_per_minute:
        messages.error(request, "Слишком часто. Подожди минуту (лимит в минуту).")
        return redirect("campaign_detail", campaign_id=camp.id)

    # 50 писем за шаг — безопасный MVP, но с учетом лимитов
    allowed = max(1, min(50, smtp_cfg.rate_per_minute - sent_last_min, smtp_cfg.rate_per_day - sent_today))
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
def campaign_test_send(request: HttpRequest, campaign_id) -> HttpResponse:
    """
    Отправка тестового письма кампании на себя (на from_email/smtp_username).
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
    )
    try:
        send_via_smtp(smtp_cfg, msg)
        SendLog.objects.create(campaign=camp, recipient=None, account=None, provider="smtp_global", status="sent", message_id=str(msg["Message-ID"]))
        messages.success(request, f"Тестовое письмо отправлено на {to_email}.")
    except Exception as ex:
        SendLog.objects.create(campaign=camp, recipient=None, account=None, provider="smtp_global", status="failed", error=str(ex))
        messages.error(request, f"Ошибка тестовой отправки: {ex}")
    return redirect("campaign_detail", campaign_id=camp.id)
