"""F6 Round 1: SMTP onboarding wizard + Fernet re-save UI.

Закрывает P0 из mailer-audit-2026-04-17.md: на проде `MAILER_FERNET_KEY`
в `.env` не совпадает с ключом, которым зашифрован SMTP-пароль в
`GlobalMailAccount.smtp_password_enc`. Раньше единственный путь фикса —
зайти в `/django-admin/` (что требует знания Django). Теперь — из
кастомной Админки CRM.

Страница `/admin/mail/setup/` показывает:
- Статус Fernet (валидный/невалидный) — через get_password() try/except
- Текущие SMTP-настройки (host/port/user/from_email — БЕЗ пароля)
- Форму пересохранения пароля (запишется текущим MAILER_FERNET_KEY)
- Кнопку «Отправить тест-письмо» (проверка end-to-end)

Доступ — только ADMIN/superuser.
"""
from __future__ import annotations

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from accounts.permissions import require_admin
from audit.models import ActivityEvent
from audit.service import log_event
from mailer.models import GlobalMailAccount

logger = logging.getLogger(__name__)


def _fernet_status(account: GlobalMailAccount) -> dict:
    """Проверяет, валиден ли Fernet-ключ для расшифровки текущего пароля.

    Возвращает dict: {valid: bool, has_password: bool, error: str | None}.
    """
    has_password = bool(account.smtp_password_enc)
    if not has_password:
        return {"valid": False, "has_password": False, "error": None}
    try:
        pw = account.get_password()
        # Пароль расшифровался — длина больше 0 означает успех
        return {"valid": bool(pw), "has_password": True, "error": None}
    except Exception as exc:
        return {
            "valid": False,
            "has_password": True,
            "error": f"{type(exc).__name__}: {exc}",
        }


@login_required
def settings_mail_setup(request: HttpRequest) -> HttpResponse:
    """Страница настройки SMTP — состояние + форма пересохранения пароля."""
    if not require_admin(request.user):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")

    account = GlobalMailAccount.load()
    status = _fernet_status(account)

    ctx = {
        "account": account,
        "fernet_status": status,
    }
    return render(request, "ui/settings/mail_setup.html", ctx)


@login_required
@require_POST
def settings_mail_save_password(request: HttpRequest) -> HttpResponse:
    """Пересохранить SMTP-пароль (перешифровать текущим MAILER_FERNET_KEY).

    Ключевая операция для разрешения Fernet InvalidToken на проде
    (см. mailer-audit P0, decisions.md 2026-04-17).
    """
    if not require_admin(request.user):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")

    new_password = (request.POST.get("new_password") or "").strip()
    if not new_password:
        messages.error(request, "Пароль не может быть пустым.")
        return redirect("settings_mail_setup")

    account = GlobalMailAccount.load()
    try:
        account.set_password(new_password)
        account.save(update_fields=["smtp_password_enc", "updated_at"])
    except Exception as exc:
        logger.exception("Failed to save SMTP password")
        messages.error(request, f"Не удалось сохранить: {exc}")
        return redirect("settings_mail_setup")

    # Проверка: расшифровка работает
    try:
        account.refresh_from_db()
        pw = account.get_password()
        if pw != new_password:
            messages.error(
                request,
                "Пароль сохранён, но обратная расшифровка дала другой результат. "
                "Проверьте MAILER_FERNET_KEY в .env.",
            )
            return redirect("settings_mail_setup")
    except Exception as exc:
        messages.error(
            request,
            f"Пароль сохранён, но расшифровка не работает: {exc}. "
            "Проверьте MAILER_FERNET_KEY в .env.",
        )
        return redirect("settings_mail_setup")

    # AUDIT: смена SMTP-пароля — должно быть в журнале
    try:
        log_event(
            actor=request.user,
            verb=ActivityEvent.Verb.UPDATE,
            entity_type="global_mail_account",
            entity_id=str(account.id),
            message="SMTP-пароль пересохранён через /admin/mail/setup/",
            meta={"user_ip": request.META.get("REMOTE_ADDR", "")},
        )
    except Exception:
        logger.exception("Failed to write audit event for SMTP password change")

    messages.success(request, "Пароль SMTP успешно сохранён. Расшифровка прошла проверку.")
    return redirect("settings_mail_setup")


@login_required
@require_POST
def settings_mail_test_send(request: HttpRequest) -> HttpResponse:
    """Отправить тест-письмо через текущие SMTP-настройки.

    Проверка end-to-end: расшифровка пароля → TLS handshake → отправка.
    Получатель — email текущего пользователя (из User.email).
    """
    if not require_admin(request.user):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")

    user = request.user
    if not user.email:
        messages.error(
            request,
            "У вашего аккаунта не задан email — некуда отправлять тест. "
            "Установите email в /settings/ (Профиль).",
        )
        return redirect("settings_mail_setup")

    account = GlobalMailAccount.load()
    if not account.is_enabled:
        messages.error(
            request,
            "Отправка отключена: установите флаг «Включено» в настройках SMTP.",
        )
        return redirect("settings_mail_setup")

    # Используем существующий smtp_sender, если он доступен
    try:
        from mailer.smtp_sender import open_smtp_connection, build_message, send_via_smtp
    except Exception:
        messages.error(request, "Модуль отправки недоступен. Свяжитесь с разработчиком.")
        return redirect("settings_mail_setup")

    try:
        conn = open_smtp_connection(account)
        msg = build_message(
            from_email=account.from_email or "no-reply@groupprofi.ru",
            from_name=account.from_name or "CRM ПРОФИ",
            to_email=user.email,
            subject="Тестовое письмо — CRM ПРОФИ",
            body_html="<p>Это тестовое сообщение из настроек SMTP CRM ПРОФИ.</p>"
                      "<p>Если вы получили его — отправка работает корректно.</p>",
            body_text="Это тестовое сообщение из настроек SMTP CRM ПРОФИ.",
            reply_to="",
        )
        send_via_smtp(conn, msg, to_email=user.email)
        try:
            conn.quit()
        except Exception:
            pass
    except Exception as exc:
        logger.exception("Test email send failed")
        messages.error(
            request,
            f"Ошибка при отправке: {type(exc).__name__}: {exc}. "
            "Проверьте SMTP-пароль и настройки.",
        )
        return redirect("settings_mail_setup")

    messages.success(
        request,
        f"Тест-письмо отправлено на {user.email}. Проверьте входящие "
        "(может прийти через 10–60 секунд).",
    )
    try:
        log_event(
            actor=user,
            verb=ActivityEvent.Verb.UPDATE,
            entity_type="global_mail_account",
            entity_id=str(account.id),
            message=f"Тест-письмо отправлено на {user.email}",
        )
    except Exception:
        logger.exception("Failed to write audit for test send")
    return redirect("settings_mail_setup")
