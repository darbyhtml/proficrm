from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.core.exceptions import PermissionDenied

from accounts.models import User
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


def _baseline_allowed(*, user: User, resource_type: str, resource_key: str) -> bool:
    """
    Базовые правила по умолчанию (до настроек в админке), чтобы поведение
    было предсказуемым и соответствовало текущей логике проекта.

    Важно: эти дефолты можно расширять/перекрывать PolicyRule'ами.
    """
    role = getattr(user, "role", "") or ""

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
        ):
            return True
        if resource_key == "ui:analytics":
            return bool(
                getattr(user, "is_superuser", False)
                or role
                in (
                    User.Role.ADMIN,
                    User.Role.GROUP_MANAGER,
                    User.Role.BRANCH_DIRECTOR,
                    User.Role.SALES_HEAD,
                )
            )
        if resource_key == "ui:settings":
            return bool(getattr(user, "is_superuser", False) or role == User.Role.ADMIN)

    # API / phone endpoints: по умолчанию разрешаем аутентифицированным,
    # конкретные ограничения делают queryset/per-object проверки.
    if resource_key.startswith("api:") or resource_key.startswith("phone:"):
        return True

    # Fallback: safe default based on registry
    r: PolicyResource | None = RESOURCE_INDEX.get(resource_key)
    if r and r.sensitive:
        return False
    return True


def decide(*, user: User, resource_type: str, resource: str, context: dict[str, Any] | None = None) -> PolicyDecision:
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

    qs = PolicyRule.objects.filter(enabled=True, resource_type=resource_type, resource=resource).order_by("priority", "id")

    # Сначала user-specific, затем role
    user_rules = qs.filter(subject_type=PolicyRule.SubjectType.USER, user_id=user.id)
    role_rules = qs.filter(subject_type=PolicyRule.SubjectType.ROLE, role=(getattr(user, "role", "") or ""))

    for rule in list(user_rules) + list(role_rules):
        if rule.effect == PolicyRule.Effect.ALLOW:
            return PolicyDecision(
                allowed=True,
                mode=mode,
                matched_rule_id=rule.id,
                matched_effect=rule.effect,
                default_allowed=_baseline_allowed(user=user, resource_type=resource_type, resource_key=resource),
                resource=resource,
                resource_type=resource_type,
            )
        if rule.effect == PolicyRule.Effect.DENY:
            return PolicyDecision(
                allowed=False,
                mode=mode,
                matched_rule_id=rule.id,
                matched_effect=rule.effect,
                default_allowed=_baseline_allowed(user=user, resource_type=resource_type, resource_key=resource),
                resource=resource,
                resource_type=resource_type,
            )

    # Нет правил — применяем дефолт по ресурсу
    default_allowed = _baseline_allowed(user=user, resource_type=resource_type, resource_key=resource)
    return PolicyDecision(
        allowed=default_allowed,
        mode=mode,
        matched_rule_id=None,
        matched_effect=None,
        default_allowed=default_allowed,
        resource=resource,
        resource_type=resource_type,
    )


def _log_decision(*, user: User, decision: PolicyDecision, context: dict[str, Any] | None = None) -> None:
    """
    Логируем решение в audit (чтобы видеть расхождения и понимать реальное использование).
    """
    try:
        from audit.models import ActivityEvent
        from audit.service import log_event

        log_event(
            actor=user,
            verb=ActivityEvent.Verb.UPDATE,
            entity_type="policy",
            entity_id=f"{decision.resource_type}:{decision.resource}",
            message="Policy decision",
            meta={
                "mode": decision.mode,
                "allowed": decision.allowed,
                "matched_rule_id": decision.matched_rule_id,
                "matched_effect": decision.matched_effect,
                "default_allowed": decision.default_allowed,
                "context": context or {},
            },
        )
    except Exception:
        # Никогда не ломаем бизнес-логику из-за логирования
        pass


def enforce(*, user: User, resource_type: str, resource: str, context: dict[str, Any] | None = None, log: bool = True) -> PolicyDecision:
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

