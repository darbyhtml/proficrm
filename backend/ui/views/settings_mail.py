"""F6 Round 1 + Round 2: SMTP onboarding wizard + Fernet re-save UI +
полноценное редактирование SMTP-конфига.

R2 (2026-04-18): добавлены endpoints для редактирования всех полей
GlobalMailAccount кроме пароля (host/port/username/from_email/from_name/
use_starttls + лимиты rate_per_minute/day/per_user), toggle is_enabled,
и UI-форма.

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


# ──────────────────────────────────────────────────────────────────
# F6 R2 (2026-04-18): расширенный onboarding — редактирование конфига.
# ──────────────────────────────────────────────────────────────────

# Целочисленные поля GlobalMailAccount с безопасными границами.
_INT_FIELDS = {
    "smtp_port": (1, 65535),
    "rate_per_minute": (1, 1000),
    "rate_per_day": (1, 1_000_000),
    "per_user_daily_limit": (1, 100_000),
}
# Текстовые поля (max_length проверяется моделью; валидатор просто strip).
_STR_FIELDS = ("smtp_host", "smtp_username", "from_email", "from_name")


@login_required
@require_POST
def settings_mail_save_config(request: HttpRequest) -> HttpResponse:
    """Сохранить host/port/username/from_email/from_name/STARTTLS и лимиты.

    Пароль не трогаем (для него — отдельный endpoint save_password).
    """
    if not require_admin(request.user):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")

    account = GlobalMailAccount.load()

    # Строковые поля: strip + присваивание.
    for field in _STR_FIELDS:
        val = (request.POST.get(field) or "").strip()
        setattr(account, field, val)

    # use_starttls: checkbox может отсутствовать в POST, если unchecked.
    account.use_starttls = request.POST.get("use_starttls") == "on"

    # Целочисленные: валидация диапазона.
    for field, (lo, hi) in _INT_FIELDS.items():
        raw = (request.POST.get(field) or "").strip()
        if not raw:
            continue
        try:
            val = int(raw)
        except ValueError:
            messages.error(request, f"Поле «{field}»: ожидается целое число.")
            return redirect("settings_mail_setup")
        if val < lo or val > hi:
            messages.error(
                request,
                f"Поле «{field}»: диапазон [{lo}, {hi}], получено {val}.",
            )
            return redirect("settings_mail_setup")
        setattr(account, field, val)

    # Валидация from_email через Django EmailField.
    try:
        account.full_clean(exclude=["smtp_password_enc", "smtp_bz_api_key_enc"])
    except Exception as exc:
        messages.error(request, f"Ошибка валидации: {exc}")
        return redirect("settings_mail_setup")

    try:
        account.save()
    except Exception as exc:
        logger.exception("Failed to save mail config")
        messages.error(request, f"Не удалось сохранить: {exc}")
        return redirect("settings_mail_setup")

    try:
        log_event(
            actor=request.user,
            verb=ActivityEvent.Verb.UPDATE,
            entity_type="global_mail_account",
            entity_id=str(account.id),
            message="SMTP-конфиг обновлён (host/port/username/from/лимиты)",
            meta={"user_ip": request.META.get("REMOTE_ADDR", "")},
        )
    except Exception:
        logger.exception("Failed to write audit event for config update")

    messages.success(request, "Настройки SMTP сохранены.")
    return redirect("settings_mail_setup")


@login_required
@require_POST
def settings_mail_toggle_enabled(request: HttpRequest) -> HttpResponse:
    """Включить/отключить массовую отправку через is_enabled."""
    if not require_admin(request.user):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")

    account = GlobalMailAccount.load()
    new_state = not account.is_enabled

    # Если пытаемся включить — обязаны иметь рабочий Fernet-пароль.
    if new_state:
        status = _fernet_status(account)
        if not status["valid"]:
            messages.error(
                request,
                "Нельзя включить отправку: SMTP-пароль не расшифровывается "
                "текущим MAILER_FERNET_KEY. Сначала сохраните пароль заново.",
            )
            return redirect("settings_mail_setup")

    account.is_enabled = new_state
    account.save(update_fields=["is_enabled", "updated_at"])

    verb = "включена" if new_state else "выключена"
    try:
        log_event(
            actor=request.user,
            verb=ActivityEvent.Verb.UPDATE,
            entity_type="global_mail_account",
            entity_id=str(account.id),
            message=f"Массовая отправка {verb}",
        )
    except Exception:
        logger.exception("Failed to write audit event for toggle_enabled")

    messages.success(request, f"Массовая отправка {verb}.")
    return redirect("settings_mail_setup")
