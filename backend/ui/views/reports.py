from __future__ import annotations
from ui.views._base import (
    CallRequest,
    Company,
    CompanyPhone,
    Contact,
    ContactPhone,
    F,
    HttpRequest,
    HttpResponse,
    JsonResponse,
    Q,
    Task,
    User,
    _add_months,
    _can_view_cold_call_reports,
    _cold_call_confirm_q,
    _date,
    _month_label,
    _month_start,
    datetime,
    login_required,
    policy_required,
    render,
    timedelta,
    timezone,
)
import logging
logger = logging.getLogger(__name__)

def _can_view_cold_call_reports(user: User) -> bool:
    if not user or not user.is_authenticated or not user.is_active:
        return False
    return bool(user.is_superuser or user.role in (User.Role.ADMIN, User.Role.GROUP_MANAGER, User.Role.BRANCH_DIRECTOR, User.Role.SALES_HEAD, User.Role.MANAGER))


def _cold_call_confirm_q() -> Q:
    """
    Условие "подтвержденный холодный звонок" для отчётов по звонкам:
    - is_cold_call=True на CallRequest
    - и этот звонок записан как marked_call либо на компании, либо на контакте,
      либо на их телефонах (CompanyPhone/ContactPhone).
    Отметки, поставленные без звонка из CRM (cold_marked_call=None), в эти
    отчёты не попадают; на карточке компании они отображаются.
    """
    return Q(
        Q(company__primary_cold_marked_call_id=F("id"))
        | Q(contact__cold_marked_call_id=F("id"))
        | Q(company__phones__cold_marked_call_id=F("id"))
        | Q(contact__phones__cold_marked_call_id=F("id"))
    )


def _month_start(d: _date) -> _date:
    return d.replace(day=1)


def _add_months(d: _date, delta_months: int) -> _date:
    # Возвращает первое число месяца, сдвинутого на delta_months.
    y = d.year
    m = d.month + int(delta_months)
    while m <= 0:
        y -= 1
        m += 12
    while m > 12:
        y += 1
        m -= 12
    return _date(y, m, 1)


def _month_label(d: _date) -> str:
    months = {
        1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель", 5: "Май", 6: "Июнь",
        7: "Июль", 8: "Август", 9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь",
    }
    return f"{months.get(d.month, str(d.month))} {d.year}"


@login_required
@policy_required(resource_type="page", resource="ui:dashboard")
def cold_calls_report_day(request: HttpRequest) -> HttpResponse:
    user: User = request.user
    if not _can_view_cold_call_reports(user):
        return JsonResponse({"ok": False, "detail": "forbidden"}, status=403)

    # Поддерживаем выбор дня через параметр ?date=YYYY-MM-DD, по умолчанию сегодня.
    date_str = (request.GET.get("date") or "").strip()
    try:
        if date_str:
            target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        else:
            target_date = timezone.localdate(timezone.now())
    except (ValueError, TypeError):
        target_date = timezone.localdate(timezone.now())

    day_start = timezone.make_aware(datetime.combine(target_date, datetime.min.time()))
    day_end = day_start + timedelta(days=1)
    day_label = target_date.strftime("%d.%m.%Y")

    # Холодные звонки: все звонки с is_cold_call=True + ручные отметки без звонка
    # 1. Все звонки с is_cold_call=True
    qs_base = (
        CallRequest.objects.filter(created_by=user, created_at__gte=day_start, created_at__lt=day_end, note="UI click")
        .exclude(status=CallRequest.Status.CANCELLED)
        .select_related("company", "contact")
    )
    qs = (
        qs_base.filter(is_cold_call=True)
        .order_by("created_at")
        .distinct()
    )
    
    # 2. Ручные отметки на компаниях (основной контакт)
    manual_companies = Company.objects.filter(
        responsible=user,
        primary_cold_marked_at__gte=day_start,
        primary_cold_marked_at__lt=day_end
    ).select_related("primary_cold_marked_by").only("id", "name", "contact_name", "phone", "primary_cold_marked_at", "primary_cold_marked_by")
    
    # 3. Ручные отметки на контактах
    manual_contacts = Contact.objects.filter(
        company__responsible=user,
        cold_marked_at__gte=day_start,
        cold_marked_at__lt=day_end
    ).select_related("company", "cold_marked_by").only("id", "first_name", "last_name", "company_id", "cold_marked_at", "cold_marked_by", "company__name")
    
    # 4. Ручные отметки на телефонах компаний
    manual_company_phones = CompanyPhone.objects.filter(
        company__responsible=user,
        cold_marked_at__gte=day_start,
        cold_marked_at__lt=day_end
    ).select_related("company", "cold_marked_by").only("id", "value", "company_id", "cold_marked_at", "cold_marked_by", "company__name")
    
    # 5. Ручные отметки на телефонах контактов
    manual_contact_phones = ContactPhone.objects.filter(
        contact__company__responsible=user,
        cold_marked_at__gte=day_start,
        cold_marked_at__lt=day_end
    ).select_related("contact", "contact__company", "cold_marked_by").only("id", "value", "contact_id", "cold_marked_at", "cold_marked_by", "contact__first_name", "contact__last_name", "contact__company_id", "contact__company__name")
    
    # Дополнительные метрики для ежедневного отчета менеджеров
    # 1. Общее количество входящих звонков
    incoming_calls_count = (
        CallRequest.objects.filter(
            created_by=user,
            created_at__gte=day_start,
            created_at__lt=day_end,
            direction=CallRequest.CallDirection.INCOMING
        )
        .exclude(status=CallRequest.Status.CANCELLED)
        .count()
    )
    
    # 2. Количество новых компаний
    new_companies_count = Company.objects.filter(
        created_by=user,
        created_at__gte=day_start,
        created_at__lt=day_end
    ).count()
    
    # 3. Количество новых контактов (в компаниях, где пользователь ответственный)
    new_contacts_count = Contact.objects.filter(
        company__responsible=user,
        created_at__gte=day_start,
        created_at__lt=day_end
    ).count()

    # 4. Выполненные задачи за день
    tasks_done_count = Task.objects.filter(
        assigned_to=user,
        status=Task.Status.DONE,
        updated_at__gte=day_start,
        updated_at__lt=day_end,
    ).count()

    # Считаем количество до итерации, чтобы ниже пройти qs через .iterator()
    # без повторного SQL-запроса на каждой итерации.
    qs_count = qs.count()

    items = []
    lines = [
        f"Отчёт: ежедневная статистика за {day_label}",
        "",
        "Холодные звонки:",
        f"  Всего: {qs_count + manual_companies.count() + manual_contacts.count() + manual_company_phones.count() + manual_contact_phones.count()}",
        "",
        "Общая статистика:",
        f"  Общее количество звонков (входящие), шт: {incoming_calls_count}",
        f"  Выполненных задач, шт: {tasks_done_count}",
        f"  Количество новых компаний, шт: {new_companies_count}",
        f"  Количество новых контактов, шт: {new_contacts_count}",
        "",
        "Детализация холодных звонков:",
        ""
    ]
    i = 0
    # Дедупликация: если пользователь несколько раз подряд кликает "позвонить" на один и тот же номер/контакт,
    # скрываем повторы в отчёте.
    dedupe_window_s = 60
    last_seen = {}  # (phone, company_id, contact_id) -> created_at

    # Звонки с подтверждённой отметкой — стримим из БД chunk'ами,
    # чтобы не грузить весь queryset в память при больших отчётах.
    for call in qs.iterator(chunk_size=500):
        key = (call.phone_raw or "", str(call.company_id or ""), str(call.contact_id or ""))
        prev = last_seen.get(key)
        if prev and (call.created_at - prev).total_seconds() < dedupe_window_s:
            continue
        last_seen[key] = call.created_at

        i += 1
        t = timezone.localtime(call.created_at).strftime("%H:%M")
        company_name = getattr(call.company, "name", "") if call.company_id else ""
        if call.contact_id and call.contact:
            contact_name = str(call.contact) or ""
        else:
            contact_name = (getattr(call.company, "contact_name", "") or "").strip() if call.company_id else ""
        who = contact_name or "Контакт не указан"
        who2 = f"{who} ({company_name})" if company_name else who
        phone = call.phone_raw or ""
        items.append({"time": t, "phone": phone, "contact": who, "company": company_name})
        lines.append(f"{i}) {t} — {who2} — {phone}")
    
    # Ручные отметки на компаниях
    for company in manual_companies:
        i += 1
        t = timezone.localtime(company.primary_cold_marked_at).strftime("%H:%M")
        company_name = company.name or ""
        contact_name = (company.contact_name or "").strip()
        who = contact_name or "Контакт не указан"
        who2 = f"{who} ({company_name})" if company_name else who
        phone = company.phone or ""
        items.append({"time": t, "phone": phone, "contact": who, "company": company_name})
        lines.append(f"{i}) {t} — {who2} — {phone} (ручная отметка)")
    
    # Ручные отметки на контактах
    for contact in manual_contacts:
        i += 1
        t = timezone.localtime(contact.cold_marked_at).strftime("%H:%M")
        company_name = contact.company.name if contact.company else ""
        contact_name = f"{contact.first_name} {contact.last_name}".strip() or "Контакт"
        who2 = f"{contact_name} ({company_name})" if company_name else contact_name
        phone = ""
        items.append({"time": t, "phone": phone, "contact": contact_name, "company": company_name})
        lines.append(f"{i}) {t} — {who2} — {phone} (ручная отметка)")
    
    # Ручные отметки на телефонах компаний
    for phone_obj in manual_company_phones:
        i += 1
        t = timezone.localtime(phone_obj.cold_marked_at).strftime("%H:%M")
        company_name = phone_obj.company.name if phone_obj.company else ""
        contact_name = ""
        who2 = company_name or "Компания"
        phone = phone_obj.value or ""
        items.append({"time": t, "phone": phone, "contact": contact_name, "company": company_name})
        lines.append(f"{i}) {t} — {who2} — {phone} (ручная отметка)")
    
    # Ручные отметки на телефонах контактов
    for phone_obj in manual_contact_phones:
        i += 1
        t = timezone.localtime(phone_obj.cold_marked_at).strftime("%H:%M")
        company_name = phone_obj.contact.company.name if phone_obj.contact and phone_obj.contact.company else ""
        contact_name = f"{phone_obj.contact.first_name} {phone_obj.contact.last_name}".strip() if phone_obj.contact else ""
        who2 = f"{contact_name} ({company_name})" if company_name else contact_name or "Контакт"
        phone = phone_obj.value or ""
        items.append({"time": t, "phone": phone, "contact": contact_name, "company": company_name})
        lines.append(f"{i}) {t} — {who2} — {phone} (ручная отметка)")

    stats = {
        "cold_calls": len(items),
        "incoming_calls": incoming_calls_count,
        "tasks_done": tasks_done_count,
        "new_companies": new_companies_count,
    }

    # AJAX → JSON (для модалки на дашборде), обычный → HTML
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({
            "ok": True,
            "range": "day",
            "date": day_label,
            "count": len(items),
            "items": items,
            "text": "\n".join(lines),
            "stats": stats,
        })

    prev_date = (target_date - timedelta(days=1)).strftime("%Y-%m-%d")
    next_date = (target_date + timedelta(days=1)).strftime("%Y-%m-%d") if target_date < timezone.localdate(timezone.now()) else None

    return render(request, "ui/reports/cold_calls_day.html", {
        "day_label": day_label,
        "prev_date": prev_date,
        "next_date": next_date,
        "items": items,
        "stats": stats,
    })


@login_required
@policy_required(resource_type="page", resource="ui:dashboard")
def cold_calls_report_month(request: HttpRequest) -> HttpResponse:
    user: User = request.user
    if not _can_view_cold_call_reports(user):
        return JsonResponse({"ok": False, "detail": "forbidden"}, status=403)

    today = timezone.localdate(timezone.now())
    base = _month_start(today)
    candidates = [_month_start(_add_months(base, -2)), _month_start(_add_months(base, -1)), base]

    available = []
    for ms in candidates:
        me = _add_months(ms, 1)
        exists = (
            CallRequest.objects.filter(created_by=user, created_at__date__gte=ms, created_at__date__lt=me, note="UI click")
            .exclude(status=CallRequest.Status.CANCELLED)
            .filter(is_cold_call=True)
            .filter(_cold_call_confirm_q())
            .exists()
        )
        if exists:
            available.append(ms)

    # Если вообще нет данных — показываем текущий месяц (пустой отчёт), чтобы кнопка не была "мертвой"
    if not available:
        available = [base]

    req_key = (request.GET.get("month") or "").strip()
    selected = available[-1]
    for ms in available:
        if req_key and req_key == ms.strftime("%Y-%m"):
            selected = ms
            break

    month_end = _add_months(selected, 1)
    month_start_aware = timezone.make_aware(datetime.combine(selected, datetime.min.time()))
    month_end_aware = timezone.make_aware(datetime.combine(month_end, datetime.min.time()))
    
    # Холодные звонки: все звонки с is_cold_call=True + ручные отметки без звонка
    # 1. Все звонки с is_cold_call=True
    qs_base = (
        CallRequest.objects.filter(created_by=user, created_at__date__gte=selected, created_at__date__lt=month_end, note="UI click")
        .exclude(status=CallRequest.Status.CANCELLED)
        .select_related("company", "contact")
    )
    qs = (
        qs_base.filter(is_cold_call=True)
        .order_by("created_at")
        .distinct()
    )
    
    # 2. Ручные отметки на компаниях (основной контакт)
    manual_companies = Company.objects.filter(
        responsible=user,
        primary_cold_marked_at__gte=month_start_aware,
        primary_cold_marked_at__lt=month_end_aware
    ).select_related("primary_cold_marked_by").only("id", "name", "contact_name", "phone", "primary_cold_marked_at", "primary_cold_marked_by")
    
    # 3. Ручные отметки на контактах
    manual_contacts = Contact.objects.filter(
        company__responsible=user,
        cold_marked_at__gte=month_start_aware,
        cold_marked_at__lt=month_end_aware
    ).select_related("company", "cold_marked_by").only("id", "first_name", "last_name", "company_id", "cold_marked_at", "cold_marked_by", "company__name")
    
    # 4. Ручные отметки на телефонах компаний
    manual_company_phones = CompanyPhone.objects.filter(
        company__responsible=user,
        cold_marked_at__gte=month_start_aware,
        cold_marked_at__lt=month_end_aware
    ).select_related("company", "cold_marked_by").only("id", "value", "company_id", "cold_marked_at", "cold_marked_by", "company__name")
    
    # 5. Ручные отметки на телефонах контактов
    manual_contact_phones = ContactPhone.objects.filter(
        contact__company__responsible=user,
        cold_marked_at__gte=month_start_aware,
        cold_marked_at__lt=month_end_aware
    ).select_related("contact", "contact__company", "cold_marked_by").only("id", "value", "contact_id", "cold_marked_at", "cold_marked_by", "contact__first_name", "contact__last_name", "contact__company_id", "contact__company__name")
    
    # Дополнительные метрики для месячного отчета менеджеров
    # 1. Общее количество входящих звонков
    incoming_calls_count = (
        CallRequest.objects.filter(
            created_by=user,
            created_at__gte=month_start_aware,
            created_at__lt=month_end_aware,
            direction=CallRequest.CallDirection.INCOMING
        )
        .exclude(status=CallRequest.Status.CANCELLED)
        .count()
    )
    
    # 2. Количество новых компаний
    new_companies_count = Company.objects.filter(
        created_by=user,
        created_at__gte=month_start_aware,
        created_at__lt=month_end_aware
    ).count()
    
    # 3. Количество новых контактов (в компаниях, где пользователь ответственный)
    new_contacts_count = Contact.objects.filter(
        company__responsible=user,
        created_at__gte=month_start_aware,
        created_at__lt=month_end_aware
    ).count()

    # 4. Выполненные задачи за месяц
    tasks_done_count = Task.objects.filter(
        assigned_to=user,
        status=Task.Status.DONE,
        updated_at__gte=month_start_aware,
        updated_at__lt=month_end_aware,
    ).count()

    # Сохраняем count до итерации — в цикле ниже qs проходится через .iterator().
    qs_count = qs.count()

    items = []
    total_cold = qs_count + manual_companies.count() + manual_contacts.count() + manual_company_phones.count() + manual_contact_phones.count()
    lines = [
        f"Отчёт: месячная статистика за {_month_label(selected)}",
        "",
        "Холодные звонки:",
        f"  Всего: {total_cold}",
        "",
        "Общая статистика:",
        f"  Общее количество звонков (входящие), шт: {incoming_calls_count}",
        f"  Выполненных задач, шт: {tasks_done_count}",
        f"  Количество новых компаний, шт: {new_companies_count}",
        f"  Количество новых контактов, шт: {new_contacts_count}",
        "",
        "Детализация холодных звонков:",
        ""
    ]
    i = 0
    dedupe_window_s = 60
    last_seen = {}  # (phone, company_id, contact_id) -> created_at
    
    # Звонки с подтверждённой отметкой — стримим chunk'ами.
    for call in qs.iterator(chunk_size=500):
        key = (call.phone_raw or "", str(call.company_id or ""), str(call.contact_id or ""))
        prev = last_seen.get(key)
        if prev and (call.created_at - prev).total_seconds() < dedupe_window_s:
            continue
        last_seen[key] = call.created_at

        i += 1
        dt = timezone.localtime(call.created_at)
        t = dt.strftime("%d.%m %H:%M")
        company_name = getattr(call.company, "name", "") if call.company_id else ""
        if call.contact_id and call.contact:
            contact_name = str(call.contact) or ""
        else:
            contact_name = (getattr(call.company, "contact_name", "") or "").strip() if call.company_id else ""
        who = contact_name or "Контакт не указан"
        who2 = f"{who} ({company_name})" if company_name else who
        phone = call.phone_raw or ""
        items.append({"time": t, "phone": phone, "contact": who, "company": company_name})
        lines.append(f"{i}) {t} — {who2} — {phone}")
    
    # Ручные отметки на компаниях
    for company in manual_companies:
        i += 1
        dt = timezone.localtime(company.primary_cold_marked_at)
        t = dt.strftime("%d.%m %H:%M")
        company_name = company.name or ""
        contact_name = (company.contact_name or "").strip()
        who = contact_name or "Контакт не указан"
        who2 = f"{who} ({company_name})" if company_name else who
        phone = company.phone or ""
        items.append({"time": t, "phone": phone, "contact": who, "company": company_name})
        lines.append(f"{i}) {t} — {who2} — {phone} (ручная отметка)")
    
    # Ручные отметки на контактах
    for contact in manual_contacts:
        i += 1
        dt = timezone.localtime(contact.cold_marked_at)
        t = dt.strftime("%d.%m %H:%M")
        company_name = contact.company.name if contact.company else ""
        contact_name = f"{contact.first_name} {contact.last_name}".strip() or "Контакт"
        who2 = f"{contact_name} ({company_name})" if company_name else contact_name
        phone = ""
        items.append({"time": t, "phone": phone, "contact": contact_name, "company": company_name})
        lines.append(f"{i}) {t} — {who2} — {phone} (ручная отметка)")
    
    # Ручные отметки на телефонах компаний
    for phone_obj in manual_company_phones:
        i += 1
        dt = timezone.localtime(phone_obj.cold_marked_at)
        t = dt.strftime("%d.%m %H:%M")
        company_name = phone_obj.company.name if phone_obj.company else ""
        contact_name = ""
        who2 = company_name or "Компания"
        phone = phone_obj.value or ""
        items.append({"time": t, "phone": phone, "contact": contact_name, "company": company_name})
        lines.append(f"{i}) {t} — {who2} — {phone} (ручная отметка)")
    
    # Ручные отметки на телефонах контактов
    for phone_obj in manual_contact_phones:
        i += 1
        dt = timezone.localtime(phone_obj.cold_marked_at)
        t = dt.strftime("%d.%m %H:%M")
        company_name = phone_obj.contact.company.name if phone_obj.contact and phone_obj.contact.company else ""
        contact_name = f"{phone_obj.contact.first_name} {phone_obj.contact.last_name}".strip() if phone_obj.contact else ""
        who2 = f"{contact_name} ({company_name})" if company_name else contact_name or "Контакт"
        phone = phone_obj.value or ""
        items.append({"time": t, "phone": phone, "contact": contact_name, "company": company_name})
        lines.append(f"{i}) {t} — {who2} — {phone} (ручная отметка)")

    month_options = [{"key": ms.strftime("%Y-%m"), "label": _month_label(ms)} for ms in available]
    stats = {
        "cold_calls": total_cold,
        "incoming_calls": incoming_calls_count,
        "tasks_done": tasks_done_count,
        "new_companies": new_companies_count,
    }

    # AJAX → JSON (для модалки на дашборде), обычный → HTML
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({
            "ok": True,
            "range": "month",
            "month": selected.strftime("%Y-%m"),
            "month_label": _month_label(selected),
            "available_months": month_options,
            "count": len(items),
            "items": items,
            "text": "\n".join(lines),
            "stats": stats,
        })

    prev_month = _add_months(selected, -1).strftime("%Y-%m") if selected > available[0] else None
    next_month = _add_months(selected, 1).strftime("%Y-%m") if selected < available[-1] else None

    return render(request, "ui/reports/cold_calls_month.html", {
        "month_label": _month_label(selected),
        "selected_month": selected.strftime("%Y-%m"),
        "prev_month": prev_month,
        "next_month": next_month,
        "month_options": month_options,
        "items": items,
        "stats": stats,
    })


@login_required
@policy_required(resource_type="page", resource="ui:dashboard")
def cold_calls_report_last_7_days(request: HttpRequest) -> JsonResponse:
    """
    Сводка по холодным звонкам за последние 7 дней (включая сегодня) для текущего пользователя:
    список дней с количеством, чтобы UI мог дать выбор даты.
    """
    user: User = request.user
    if not _can_view_cold_call_reports(user):
        return JsonResponse({"ok": False, "detail": "forbidden"}, status=403)

    today = timezone.localdate(timezone.now())
    start_date = today - timedelta(days=6)
    days = []
    total = 0
    for i in range(7):
        d = start_date + timedelta(days=i)
        day_start = timezone.make_aware(datetime.combine(d, datetime.min.time()))
        day_end = day_start + timedelta(days=1)
        # Все звонки с is_cold_call=True
        qs_base = (
            CallRequest.objects.filter(created_by=user, created_at__gte=day_start, created_at__lt=day_end, note="UI click")
            .exclude(status=CallRequest.Status.CANCELLED)
        )
        cnt_calls = (
            qs_base.filter(is_cold_call=True)
            .distinct()
            .count()
        )
        # Ручные отметки
        cnt_manual = (
            Company.objects.filter(responsible=user, primary_cold_marked_at__gte=day_start, primary_cold_marked_at__lt=day_end).count() +
            Contact.objects.filter(company__responsible=user, cold_marked_at__gte=day_start, cold_marked_at__lt=day_end).count() +
            CompanyPhone.objects.filter(company__responsible=user, cold_marked_at__gte=day_start, cold_marked_at__lt=day_end).count() +
            ContactPhone.objects.filter(contact__company__responsible=user, cold_marked_at__gte=day_start, cold_marked_at__lt=day_end).count()
        )
        cnt = cnt_calls + cnt_manual
        total += cnt
        days.append(
            {
                "date": d.strftime("%Y-%m-%d"),
                "label": d.strftime("%d.%m.%Y"),
                "count": cnt,
            }
        )

    period_label = f"{start_date.strftime('%d.%m.%Y')} — {today.strftime('%d.%m.%Y')}"
    return JsonResponse({"ok": True, "range": "last_7_days", "period": period_label, "total": total, "days": days})


