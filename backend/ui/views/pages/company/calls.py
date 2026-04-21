"""PhoneBridge call request (W1.2 refactor).

Extracted из `backend/ui/views/company_detail.py` в W1.2. Zero behavior change.

Endpoints:
- `phone_call_create` — POST /phone/call/

UI endpoint для создания "команды на звонок" для телефона текущего пользователя.
Android-приложение (APK) забирает команду через polling /api/phone/calls/pull/.
"""

from __future__ import annotations

import logging

from phonebridge.models import CallRequest, PhoneDevice
from ui.views._base import (
    ActivityEvent,
    HttpRequest,
    HttpResponse,
    JsonResponse,
    User,
    log_event,
    login_required,
    policy_required,
    timedelta,
    timezone,
)

logger = logging.getLogger(__name__)


@login_required
@policy_required(resource_type="action", resource="ui:companies:update")
def phone_call_create(request: HttpRequest) -> HttpResponse:
    """
    UI endpoint: создать "команду на звонок" для телефона текущего пользователя.
    Android-приложение (APK) забирает команду через polling /api/phone/calls/pull/.
    """
    if request.method != "POST":
        return JsonResponse({"ok": False, "detail": "method not allowed"}, status=405)

    user: User = request.user
    phone = (request.POST.get("phone") or "").strip()
    company_id = (request.POST.get("company_id") or "").strip()
    contact_id = (request.POST.get("contact_id") or "").strip()

    if not phone:
        return JsonResponse({"ok": False, "detail": "phone is required"}, status=400)

    # Нормализация номера телефона к формату +7XXXXXXXXXX
    # Убираем все пробелы, дефисы, скобки
    raw = phone.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")

    # Если номер уже в правильном формате +7XXXXXXXXXX (12 символов), оставляем как есть
    if raw.startswith("+7") and len(raw) == 12 and raw[2:].isdigit():
        normalized = raw
    else:
        # Извлекаем только цифры
        digits = "".join(ch for ch in raw if ch.isdigit())

        # Приводим к формату +7XXXXXXXXXX для российских номеров
        if digits.startswith("8") and len(digits) == 11:
            # 8XXXXXXXXXX => +7XXXXXXXXXX
            normalized = "+7" + digits[1:]
        elif digits.startswith("7") and len(digits) == 11:
            # 7XXXXXXXXXX => +7XXXXXXXXXX
            normalized = "+7" + digits[1:]
        elif len(digits) == 10:
            # XXXXXXXXXX => +7XXXXXXXXXX
            normalized = "+7" + digits
        elif digits.startswith("8") and len(digits) > 11:
            # 8XXXXXXXXXX... => +7XXXXXXXXXX (берем первые 11 цифр)
            normalized = "+7" + digits[1:11]
        elif digits.startswith("7") and len(digits) > 11:
            # 7XXXXXXXXXX... => +7XXXXXXXXXX (берем первые 11 цифр)
            normalized = "+7" + digits[1:11]
        elif len(digits) >= 10:
            # Берем последние 10 цифр
            normalized = "+7" + digits[-10:]
        else:
            # Если ничего не подошло, возвращаем как есть (но это ошибка)
            normalized = raw

    # Дедупликация на сервере: если пользователь несколько раз подряд нажимает "позвонить" на тот же номер/контакт,
    # не создаём новые записи (иначе отчёты раздуваются).
    # НО: если предыдущий запрос уже был получен телефоном (CONSUMED), создаём новый, чтобы можно было позвонить повторно.
    now = timezone.now()
    recent = now - timedelta(seconds=60)
    existing = CallRequest.objects.filter(
        created_by=user, phone_raw=normalized, created_at__gte=recent
    ).exclude(status=CallRequest.Status.CANCELLED)
    if company_id:
        existing = existing.filter(company_id=company_id)
    else:
        existing = existing.filter(company__isnull=True)
    if contact_id:
        existing = existing.filter(contact_id=contact_id)
    else:
        existing = existing.filter(contact__isnull=True)
    prev_call = existing.order_by("-created_at").first()
    # Если есть предыдущий запрос И он еще не был получен телефоном (PENDING) - возвращаем его
    # Если он уже CONSUMED - создаём новый, чтобы можно было позвонить повторно
    if prev_call and prev_call.status == CallRequest.Status.PENDING:
        return JsonResponse(
            {"ok": True, "id": str(prev_call.id), "phone": normalized, "dedup": True}
        )

    call = CallRequest.objects.create(
        user=user,
        created_by=user,
        company_id=company_id or None,
        contact_id=contact_id or None,
        phone_raw=normalized,
        note="UI click",
    )

    # Маскируем номер телефона для логов (защита от утечки персональных данных)
    from phonebridge.api import mask_phone, send_fcm_call_command_notification

    masked_phone = mask_phone(normalized) if normalized else "N/A"
    logger.info(
        "phone_call_create: created CallRequest %s for user %s, phone %s, device check: %s",
        call.id,
        user.id,
        masked_phone,
        PhoneDevice.objects.filter(user=user).exists(),
    )

    # FCM-ускоритель: отправляем data-push на все устройства пользователя с fcm_token.
    # Push только пробуждает pullCall на клиенте, сама команда всё равно будет доставлена через /calls/pull/.
    try:
        devices_with_fcm = PhoneDevice.objects.filter(user=user).exclude(fcm_token="")
        for device in devices_with_fcm:
            send_fcm_call_command_notification(device, reason="new_call")
    except Exception as e:
        logger.warning(
            "phone_call_create: failed to send FCM notifications for CallRequest %s: %s", call.id, e
        )

    log_event(
        actor=user,
        verb=ActivityEvent.Verb.CREATE,
        entity_type="call_request",
        entity_id=str(call.id),
        company_id=company_id or None,
        message="Запрос на звонок с телефона",
        meta={"phone": normalized, "contact_id": contact_id or None},
    )
    return JsonResponse({"ok": True, "id": str(call.id), "phone": normalized})
