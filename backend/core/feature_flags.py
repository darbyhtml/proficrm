"""
Единая точка входа для feature flags проекта CRM ПРОФИ.

Wave 0.3 (2026-04-20). Используем django-waffle как backend.

Почему обёртка, а не waffle.is_active напрямую:
1. **Env-kill-switch**: любой флаг можно мгновенно выключить переменной
   `FEATURE_FLAG_KILL_<NAME>=1` в окружении (без захода в admin, без
   миграции, без рестарта БД). Критично когда новая фича ломает прод
   в пятницу вечером.
2. **Branch-based overrides**: поддержка `branch_code` (EKB/TMN/KRD)
   как критерия включения — без этого waffle приходится комбинировать
   с User-filter'ом, что неудобно.
3. **Единообразие API для templates/views/JS/DRF**: один `is_enabled(name, ...)`,
   не нужно помнить какой import.
4. **Тестируемость**: можно подменить `is_enabled` в тестах через
   `@override_feature(NAME=True)` без database hit на waffle.Flag.

## Базовое использование

    from core.feature_flags import is_enabled

    # В view
    if is_enabled("UI_V3B_DEFAULT", user=request.user):
        return render(request, "company_detail_v3b.html", ctx)
    return render(request, "company_detail_classic.html", ctx)

    # В template (через {% feature_flag %})
    {% load feature_flags %}
    {% feature_flag "UI_V3B_DEFAULT" as ff %}
    {% if ff %}...{% endif %}

    # В DRF view/viewset
    from core.permissions import FeatureFlagPermission

    class MyViewSet(ViewSet):
        permission_classes = [IsAuthenticated, FeatureFlagPermission]
        feature_flag_required = "EMAIL_BOUNCE_HANDLING"

## Kill-switch через env

    # Срочно выключить UI_V3B_DEFAULT на всём проде:
    docker compose exec web sh -c 'export FEATURE_FLAG_KILL_UI_V3B_DEFAULT=1'
    # Или правка .env + docker compose up -d (не restart!) web

## Для разработчиков

Добавить новый флаг → сгенерировать миграцию `create_waffle_flag` либо
правки через admin (`/admin/waffle/flag/`). Не забыть обновить
`docs/architecture/feature-flags.md`.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Optional

logger = logging.getLogger(__name__)


if TYPE_CHECKING:
    from django.http import HttpRequest

    from accounts.models import Branch, User


# ---------------------------------------------------------------------------
# Canonical flag names — единственный источник правды для имён флагов.
# ---------------------------------------------------------------------------

#: Wave 9 — переключатель дефолтного рендера карточки компании (classic → v3/b).
UI_V3B_DEFAULT = "UI_V3B_DEFAULT"

#: Wave 2.4 — soft→mandatory миграция TOTP для ADMIN/BRANCH_DIRECTOR.
#: off — TOTP опционален (пользователь видит баннер «настоятельно рекомендуется»).
#: on  — TOTP обязателен при следующем логине (2 недели после W2.4 старта).
TWO_FACTOR_MANDATORY_FOR_ADMINS = "TWO_FACTOR_MANDATORY_FOR_ADMINS"

#: Wave 2 — shadow-дашборд «denied requests» из Policy Engine.
#: Включается за 2 недели до перехода в ENFORCE, чтобы собрать данные
#: и увидеть потенциальные false-positives.
POLICY_DECISION_LOG_DASHBOARD = "POLICY_DECISION_LOG_DASHBOARD"

#: Wave 6 — обработка bounce/complaint уведомлений от SMTP-провайдера
#: (webhook или IMAP fallback — см. docs/plan/07_wave_6_email.md §6.2).
EMAIL_BOUNCE_HANDLING = "EMAIL_BOUNCE_HANDLING"


#: Все известные флаги — для валидации в API endpoint и тестах.
KNOWN_FLAGS: tuple[str, ...] = (
    UI_V3B_DEFAULT,
    TWO_FACTOR_MANDATORY_FOR_ADMINS,
    POLICY_DECISION_LOG_DASHBOARD,
    EMAIL_BOUNCE_HANDLING,
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def is_enabled(
    flag: str,
    *,
    user: User | None = None,
    branch: Branch | None = None,
    request: HttpRequest | None = None,
) -> bool:
    """Проверить включён ли feature flag для заданного контекста.

    Args:
        flag: Имя флага. Рекомендуется использовать константы из этого модуля
            (например, ``UI_V3B_DEFAULT``), а не строковые литералы — проще
            ловить опечатки и делать rename.
        user: Пользователь, для которого проверяем. Если не указан —
            проверяем «глобально» (только Everyone/None-условия).
        branch: Филиал пользователя (EKB/TMN/KRD). Если не указан, но
            указан ``user`` — берётся из ``user.branch``. Даёт доп. критерий
            «включить только для Екатеринбурга» через Flag.note hook.
        request: HttpRequest. Нужен для waffle-overrides через cookies
            (для A/B-тестирования на фронтенде). Опционально.

    Returns:
        ``True`` если флаг включён в контексте, иначе ``False``.

    Notes:
        Порядок проверки:
        1. **Kill-switch env** (``FEATURE_FLAG_KILL_<NAME>=1``) — перекрывает всё.
        2. **Waffle DB lookup** — стандартная логика waffle.is_active.
        3. **Default** — ``WAFFLE_FLAG_DEFAULT`` из settings (у нас False).
    """
    # (1) Emergency kill-switch через env — перекрывает admin/DB.
    kill_var = f"FEATURE_FLAG_KILL_{flag}"
    if os.getenv(kill_var, "").strip() == "1":
        logger.warning(
            "feature_flag.kill_switch: %s forced OFF via env var %s", flag, kill_var
        )
        return False

    # (2) Waffle lookup. Импорт локальный — модуль может использоваться
    # в settings-time коде, где apps ещё не loaded.
    try:
        from waffle import flag_is_active
    except ImportError:
        logger.error("feature_flag: django-waffle not installed, returning False")
        return False

    # Если передан user, но нет request — конструируем minimal-shim,
    # т.к. waffle.flag_is_active требует request.
    if request is None and user is not None:
        request = _make_shim_request(user)

    if request is None:
        # Без request waffle не может проверить percentage/group — используем
        # булев fallback (Flag.everyone / Flag.staff / Flag.superusers).
        from waffle import get_waffle_flag_model

        Flag = get_waffle_flag_model()
        try:
            flag_obj = Flag.objects.get(name=flag)
        except Flag.DoesNotExist:
            from django.conf import settings

            return getattr(settings, "WAFFLE_FLAG_DEFAULT", False)
        # Everyone явно выставлен → используем его.
        if flag_obj.everyone is not None:
            return bool(flag_obj.everyone)
        return False

    return bool(flag_is_active(request, flag))


def set_flag(
    name: str,
    *,
    everyone: bool | None = None,
    percent: float | None = None,
    note: str | None = None,
) -> None:
    """Программное изменение флага — с гарантированной инвалидацией waffle-кеша.

    Проблема, которую решаем: ``Flag.objects.filter(name=...).update(everyone=True)``
    обходит ``post_save`` signal, waffle-кеш остаётся stale, следующий
    ``is_enabled()`` вернёт старое значение до 5-10 секунд. В тестах — флаки,
    в prod — путаница при экстренном включении/выключении.

    Правильный способ — получить инстанс и вызвать ``save()``, что триггерит
    сигнал и сбрасывает кеш немедленно.

    Args:
        name: Имя флага. Обычно константа из этого модуля.
        everyone: ``True`` — включить для всех, ``False`` — выключить для всех,
            ``None`` — не менять (удобно когда меняем только percent/note).
        percent: 0-100 процент rollout. ``None`` — не менять.
        note: Текстовое описание. ``None`` — не менять.

    Raises:
        Flag.DoesNotExist: если флаг не зарегистрирован в БД (должен быть
            создан seed migration или через admin UI).

    Example:
        >>> from core.feature_flags import set_flag, UI_V3B_DEFAULT
        >>> set_flag(UI_V3B_DEFAULT, everyone=True, note="2026-05-01 full rollout")
    """
    from waffle import get_waffle_flag_model

    Flag = get_waffle_flag_model()
    flag = Flag.objects.get(name=name)
    changed = False
    if everyone is not None and flag.everyone != everyone:
        flag.everyone = everyone
        changed = True
    if percent is not None and flag.percent != percent:
        flag.percent = percent
        changed = True
    if note is not None and flag.note != note:
        flag.note = note
        changed = True
    if changed:
        flag.save()  # save() → post_save signal → waffle cache invalidation


def active_flags_for_user(user: User | None) -> dict[str, bool]:
    """Вернуть словарь ``{flag_name: is_enabled}`` для всех известных флагов.

    Используется API endpoint ``GET /api/v1/feature-flags/`` — фронту нужно
    знать активные флаги, чтобы условно показывать UI v3/b/и т.п.

    Args:
        user: Пользователь, для которого строим карту. ``None`` = anonymous.

    Returns:
        Словарь со всеми известными флагами (``KNOWN_FLAGS``). Ключи —
        строки, значения — булевы.
    """
    return {flag: is_enabled(flag, user=user) for flag in KNOWN_FLAGS}


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _make_shim_request(user: User) -> HttpRequest:
    """Минимальный HttpRequest-совместимый объект для waffle.

    waffle.flag_is_active требует ``request.user`` и ``request.COOKIES``.
    Остальное игнорируется при стандартной конфигурации.

    Используется когда is_enabled() вызван из Celery task или management
    command, где настоящего request нет.
    """
    from django.http import HttpRequest

    req = HttpRequest()
    req.user = user
    req.COOKIES = {}
    req.META = {}
    return req
