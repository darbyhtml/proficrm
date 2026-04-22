from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.core.exceptions import PermissionDenied

from accounts.models import User
from companies.policy import can_view_company_id
from core.input_cleaners import clean_int_id
from tasksapp.policy import can_view_task_id

from .models import PolicyConfig, PolicyRule
from .resources import RESOURCE_INDEX, PolicyResource


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    mode: str
    matched_rule_id: int | None = None
    matched_effect: str | None = None
    default_allowed: bool = True
    resource: str = ""
    resource_type: str = ""


TASK_DETAIL_RESOURCE = "ui:tasks:detail"
COMPANY_DETAIL_RESOURCE = "ui:companies:detail"
_OVERRIDE_ROLES = (
    User.Role.ADMIN,
    User.Role.GROUP_MANAGER,
    User.Role.BRANCH_DIRECTOR,
    User.Role.SALES_HEAD,
)


def _has_override_role(user: User) -> bool:
    if not user or not user.is_authenticated or not user.is_active:
        return False
    # Суперпользователь обрабатывается отдельно выше в decide()
    return getattr(user, "role", None) in _OVERRIDE_ROLES


def _default_allowed_for_task_detail(*, user: User, context: dict[str, Any] | None) -> bool:
    """
    Умный дефолт для ui:tasks:detail:
    - разрешаем, если задача видна через visible_tasks_qs (can_view_task_id)
    - разрешаем override-ролям (директора/управляющие/админы)
    - иначе запрещаем.
    """
    if _has_override_role(user):
        return True
    ctx = context or {}
    task_id = clean_int_id(ctx.get("task_id"))
    if not task_id:
        # Без task_id безопаснее запретить доступ к detail.
        return False
    return can_view_task_id(user, task_id)


def _default_allowed_for_company_detail(*, user: User, context: dict[str, Any] | None) -> bool:
    """
    Умный дефолт для ui:companies:detail:
    - разрешаем, если компания видна через visible_companies_qs (can_view_company_id)
    - разрешаем override-ролям (директора/управляющие/админы)
    - иначе запрещаем.
    """
    if _has_override_role(user):
        return True
    ctx = context or {}
    company_id = clean_int_id(ctx.get("company_id"))
    if not company_id:
        # Без company_id безопаснее запретить доступ к карточке.
        return False
    return can_view_company_id(user, company_id)


def baseline_allowed_for_role(
    *, role: str, resource_type: str, resource_key: str, is_superuser: bool = False
) -> bool:
    """
    То же, что и _baseline_allowed, но без объекта User.

    Нужен для:
    - генерации "восстановленных" дефолтных правил по ролям (UI-кнопка),
    - предсказуемых проверок без создания фиктивного пользователя.
    """
    role = (role or "").strip()

    # Суперпользователь — всегда разрешаем (как и в decide()).
    if is_superuser:
        return True

    # --- Тендерист: read-only участник тендерного отдела ---
    # См. docs/decisions.md [2026-04-15] и docs/roles-access-matrix.md §7.2.
    # Обрабатываем отдельным блоком, т.к. правила сильно отличаются от остальных ролей.
    if role == User.Role.TENDERIST:
        if resource_type == PolicyRule.ResourceType.PAGE:
            allowed_pages = {
                "ui:dashboard",
                "ui:companies:list",
                "ui:companies:detail",
                "ui:tasks:list",
                "ui:tasks:detail",
                "ui:help",
                "ui:preferences",
                "ui:notifications",
                "ui:notifications:all",
                "ui:notifications:reminders",
            }
            return resource_key in allowed_pages
        if resource_type == PolicyRule.ResourceType.ACTION:
            # Задачи — как менеджер, только свои (детальная проверка в коде)
            if resource_key in (
                "ui:tasks:create",
                "ui:tasks:update",
                "ui:tasks:delete",
                "ui:tasks:status",
                "ui:tasks:bulk_reschedule",
            ):
                return True
            # Уведомления
            if resource_key in (
                "ui:notifications:poll",
                "ui:notifications:mark_read",
                "ui:notifications:mark_all_read",
            ):
                return True
            # Поиск/автокомплит компаний разрешаем (нужно для работы)
            if resource_key == "ui:companies:autocomplete":
                return True
            # Всё остальное: компании write, рассылки, настройки, мессенджер — запрещено
            return False
        # API/phone/прочее — запрещено тендеристу по умолчанию
        if resource_key.startswith("api:") or resource_key.startswith("phone:"):
            return False
        return False

    # Pages
    if resource_type == PolicyRule.ResourceType.PAGE:
        if resource_key in (
            "ui:dashboard",
            "ui:companies:list",
            "ui:companies:detail",
            "ui:tasks:list",
            "ui:tasks:detail",
            "ui:help",
            "ui:preferences",
            "ui:mail",
            "ui:notifications",
            "ui:mail:settings",
            "ui:mail:signature",
            "ui:mail:campaigns",
            "ui:mail:campaigns:detail",
            "ui:notifications:all",
            "ui:notifications:reminders",
        ):
            return True
        if resource_key == "ui:analytics":
            return bool(
                role
                in (
                    User.Role.ADMIN,
                    User.Role.GROUP_MANAGER,
                    User.Role.BRANCH_DIRECTOR,
                    User.Role.SALES_HEAD,
                )
            )
        if resource_key == "ui:settings":
            return bool(role == User.Role.ADMIN)

    # Actions (coarse RBAC gate; object/branch checks stay in code)
    if resource_type == PolicyRule.ResourceType.ACTION:
        # --- Settings / admin tools ---
        if resource_key in ("ui:settings:view_as:update", "ui:settings:view_as:reset"):
            return bool(role == User.Role.ADMIN)

        # --- Notifications: allowed for any authenticated user ---
        if resource_key in (
            "ui:notifications:poll",
            "ui:notifications:mark_read",
            "ui:notifications:mark_all_read",
        ):
            return True

        # --- Companies ---
        if resource_key in (
            "ui:companies:create",
            "ui:companies:update",
            "ui:companies:contract:update",
            "ui:companies:transfer",
            "ui:companies:cold_call:toggle",
            "ui:companies:autocomplete",
            "ui:companies:duplicates",
        ):
            return bool(
                role
                in (
                    User.Role.MANAGER,
                    User.Role.SALES_HEAD,
                    User.Role.BRANCH_DIRECTOR,
                    User.Role.GROUP_MANAGER,
                    User.Role.ADMIN,
                )
            )
        if resource_key in (
            "ui:companies:bulk_transfer",
            "ui:companies:delete_request:cancel",
            "ui:companies:delete_request:approve",
            "ui:companies:delete",
        ):
            # Менеджерам запрещаем на уровне policy (в коде тоже запрещено).
            return bool(
                role
                in (
                    User.Role.SALES_HEAD,
                    User.Role.BRANCH_DIRECTOR,
                    User.Role.GROUP_MANAGER,
                    User.Role.ADMIN,
                )
            )
        if resource_key == "ui:companies:delete_request:create":
            return bool(role == User.Role.MANAGER)
        if resource_key in ("ui:companies:cold_call:reset", "ui:companies:export"):
            return bool(role == User.Role.ADMIN)

        # --- Tasks ---
        if resource_key in (
            "ui:tasks:create",
            "ui:tasks:update",
            "ui:tasks:delete",
            "ui:tasks:status",
            "ui:tasks:bulk_reschedule",
        ):
            # Детальная проверка "своё/филиал/ответственный" остаётся в UI коде.
            return bool(
                role
                in (
                    User.Role.MANAGER,
                    User.Role.SALES_HEAD,
                    User.Role.BRANCH_DIRECTOR,
                    User.Role.GROUP_MANAGER,
                    User.Role.ADMIN,
                )
            )
        if resource_key == "ui:tasks:bulk_reassign":
            return bool(role == User.Role.ADMIN)

        # --- Mail ---
        if resource_key in ("ui:mail:smtp_settings", "ui:mail:settings:update"):
            return bool(role == User.Role.ADMIN)
        if resource_key in ("ui:mail:quota:poll", "ui:mail:progress:poll"):
            return True
        if resource_key.startswith("ui:mail:unsubscribes:"):
            return bool(role == User.Role.ADMIN)
        if resource_key in (
            "ui:mail:campaigns:create",
            "ui:mail:campaigns:edit",
            "ui:mail:campaigns:pick",
            "ui:mail:campaigns:add_email",
            "ui:mail:campaigns:recipients:add",
            "ui:mail:campaigns:recipients:delete",
            "ui:mail:campaigns:recipients:bulk_delete",
            "ui:mail:campaigns:recipients:generate",
            "ui:mail:campaigns:recipients:reset_failed",
            "ui:mail:campaigns:clear",
            "ui:mail:campaigns:send_step",
            "ui:mail:campaigns:start",
            "ui:mail:campaigns:pause",
            "ui:mail:campaigns:resume",
            "ui:mail:campaigns:test_send",
            "ui:mail:campaigns:delete",
            "ui:mail:campaigns:manage",
        ):
            return bool(
                role
                in (
                    User.Role.MANAGER,
                    User.Role.SALES_HEAD,
                    User.Role.BRANCH_DIRECTOR,
                    User.Role.GROUP_MANAGER,
                    User.Role.ADMIN,
                )
            )
        if resource_key == "ui:mail:campaigns:recipients:reset_all":
            return bool(role == User.Role.ADMIN)

    # API / phone endpoints: по умолчанию разрешаем аутентифицированным,
    # конкретные ограничения делают queryset/per-object проверки.
    if resource_key.startswith("api:") or resource_key.startswith("phone:"):
        return True

    # Fallback: safe default based on registry
    r: PolicyResource | None = RESOURCE_INDEX.get(resource_key)
    if r and r.sensitive:
        return False
    return True


def _baseline_allowed(*, user: User, resource_type: str, resource_key: str) -> bool:
    """
    Базовые правила по умолчанию (до настроек в админке), чтобы поведение
    было предсказуемым и соответствовало текущей логике проекта.

    Важно: эти дефолты можно расширять/перекрывать PolicyRule'ами.
    """
    return baseline_allowed_for_role(
        role=(getattr(user, "role", "") or ""),
        resource_type=resource_type,
        resource_key=resource_key,
        is_superuser=bool(getattr(user, "is_superuser", False)),
    )


def decide(
    *, user: User, resource_type: str, resource: str, context: dict[str, Any] | None = None
) -> PolicyDecision:
    """
    Возвращает решение политики (без побочных эффектов).
    """
    cfg = PolicyConfig.load()
    mode = cfg.mode

    # Неавторизованный — запрещаем (но снаружи это обычно уже отфильтровано @login_required / IsAuthenticated)
    if not user or not user.is_authenticated or not user.is_active:
        return PolicyDecision(
            allowed=False,
            mode=mode,
            matched_rule_id=None,
            matched_effect=None,
            default_allowed=False,
            resource=resource,
            resource_type=resource_type,
        )

    # Суперпользователь — всегда разрешаем
    if getattr(user, "is_superuser", False):
        return PolicyDecision(
            allowed=True,
            mode=mode,
            matched_rule_id=None,
            matched_effect="superuser_allow",
            default_allowed=True,
            resource=resource,
            resource_type=resource_type,
        )

    qs = PolicyRule.objects.filter(
        enabled=True, resource_type=resource_type, resource=resource
    ).order_by("priority", "id")

    # Сначала user-specific, затем role
    user_rules = qs.filter(subject_type=PolicyRule.SubjectType.USER, user_id=user.id)
    role_rules = qs.filter(
        subject_type=PolicyRule.SubjectType.ROLE, role=(getattr(user, "role", "") or "")
    )

    for rule in list(user_rules) + list(role_rules):
        if rule.effect == PolicyRule.Effect.ALLOW:
            return PolicyDecision(
                allowed=True,
                mode=mode,
                matched_rule_id=rule.id,
                matched_effect=rule.effect,
                default_allowed=_baseline_allowed(
                    user=user, resource_type=resource_type, resource_key=resource
                ),
                resource=resource,
                resource_type=resource_type,
            )
        if rule.effect == PolicyRule.Effect.DENY:
            return PolicyDecision(
                allowed=False,
                mode=mode,
                matched_rule_id=rule.id,
                matched_effect=rule.effect,
                default_allowed=_baseline_allowed(
                    user=user, resource_type=resource_type, resource_key=resource
                ),
                resource=resource,
                resource_type=resource_type,
            )

    # Нет правил — применяем дефолт по ресурсу
    default_allowed = _baseline_allowed(
        user=user, resource_type=resource_type, resource_key=resource
    )

    # Для ui:tasks:detail и ui:companies:detail используем "умный" дефолт,
    # основанный на видимости задач/компаний и override-ролях.
    if resource_type == PolicyRule.ResourceType.PAGE:
        if resource == TASK_DETAIL_RESOURCE:
            default_allowed = _default_allowed_for_task_detail(user=user, context=context)
        elif resource == COMPANY_DETAIL_RESOURCE:
            default_allowed = _default_allowed_for_company_detail(user=user, context=context)
    return PolicyDecision(
        allowed=default_allowed,
        mode=mode,
        matched_rule_id=None,
        matched_effect=None,
        default_allowed=default_allowed,
        resource=resource,
        resource_type=resource_type,
    )


def _log_decision(
    *, user: User, decision: PolicyDecision, context: dict[str, Any] | None = None
) -> None:
    """
    Логируем решение в audit (чтобы видеть расхождения и понимать реальное использование).

    Q17 decision 2026-04-22 (W2.1.3a): **DENY-ONLY logging**.
    - allowed=True decisions пропускаются (early return) — 99%+ трафика, low signal.
    - allowed=False (denies) логируются — actionable для debugging user workflows.
    - Retention: 14-day TTL через Celery beat task `policy.purge_old_events`.

    Historical context:
    - Релиз 0 (2026-04-20) полностью disabled logging из-за 145K events/day на 1 user
      staging (projected 5-7M/day на 50-user prod — DB bloat).
    - W2.1.3a re-enabled с deny-only filter — volume reduces 99%+ (estimated 5-50K
      denies/day на prod).

    Flag `POLICY_DECISION_LOGGING_ENABLED` по-прежнему required для enable (default True
    после W2.1.3a, env var override возможен).
    """
    try:
        from django.conf import settings as django_settings

        # Master switch (default on по умолчанию после W2.1.3a)
        if not getattr(django_settings, "POLICY_DECISION_LOGGING_ENABLED", True):
            return

        # Q17 deny-only: skip successful allows (99%+ of traffic, low signal)
        if decision.allowed:
            return

        from audit.models import ActivityEvent
        from audit.service import log_event

        log_event(
            actor=user,
            verb=ActivityEvent.Verb.UPDATE,
            entity_type="policy",
            entity_id=f"{decision.resource_type}:{decision.resource}",
            message="Policy decision (denied)",
            meta={
                "mode": decision.mode,
                "allowed": decision.allowed,  # always False после Q17 filter
                "matched_rule_id": decision.matched_rule_id,
                "matched_effect": decision.matched_effect,
                "default_allowed": decision.default_allowed,
                "context": context or {},
            },
        )
    except Exception:
        # Никогда не ломаем бизнес-логику из-за логирования
        pass


def enforce(
    *,
    user: User,
    resource_type: str,
    resource: str,
    context: dict[str, Any] | None = None,
    log: bool = True,
) -> PolicyDecision:
    """
    Применяет режим policy:\n
    - observe_only: не блокирует, но логирует\n
    - enforce: блокирует deny\n
    Возвращает вычисленное решение.
    """
    decision = decide(user=user, resource_type=resource_type, resource=resource, context=context)

    if log:
        _log_decision(user=user, decision=decision, context=context)

    if decision.mode == PolicyConfig.Mode.OBSERVE_ONLY:
        return decision

    if not decision.allowed:
        raise PermissionDenied(f"Доступ запрещён: {resource}")

    return decision
