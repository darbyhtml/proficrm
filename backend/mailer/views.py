from __future__ import annotations

from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
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
from crm.utils import require_admin

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
    if user.role == User.Role.MANAGER and camp.created_by_id != user.id:
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
    
    # Получаем всех получателей для группировки и пагинации
    all_recipients_qs = camp.recipients.order_by("company_id", "-updated_at")
    
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
            "generate_form": CampaignGenerateRecipientsForm(),
            "branches": Branch.objects.order_by("name"),
            "responsibles": User.objects.order_by("last_name", "first_name"),
            "statuses": CompanyStatus.objects.order_by("name"),
            "spheres": CompanySphere.objects.order_by("name"),
            "recipients_by_company": recipients_by_company,
            "page": page,
            "qs": qs_no_page,
            "per_page": per_page,
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
