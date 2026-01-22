"""
Единый реестр ресурсов для политики доступа.

Важно:
- Ключи ресурсов должны быть стабильными (используются в БД правилах).
- В MVP (pages_actions) ресурсы делим на page/action.
- Ресурсы охватывают Web UI, DRF API и phone/Android API.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


ResourceType = Literal["page", "action"]


@dataclass(frozen=True)
class PolicyResource:
    key: str
    resource_type: ResourceType
    title: str
    description: str = ""
    sensitive: bool = False


RESOURCES: tuple[PolicyResource, ...] = (
    # ---- Web UI pages ----
    PolicyResource("ui:dashboard", "page", "Рабочий стол"),
    PolicyResource("ui:companies:list", "page", "Компании: список"),
    PolicyResource("ui:companies:detail", "page", "Компании: карточка"),
    PolicyResource("ui:tasks:list", "page", "Задачи: список"),
    PolicyResource("ui:tasks:detail", "page", "Задачи: просмотр"),
    PolicyResource("ui:analytics", "page", "Аналитика"),
    PolicyResource("ui:help", "page", "Помощь"),
    PolicyResource("ui:preferences", "page", "Настройки пользователя"),
    PolicyResource("ui:settings", "page", "Админка (раздел settings)", sensitive=True),
    PolicyResource("ui:mail", "page", "Почта (раздел)"),
    PolicyResource("ui:notifications", "page", "Уведомления (раздел)"),

    # ---- Web UI actions (опасные/изменяющие) ----
    PolicyResource("ui:companies:create", "action", "Компании: создать"),
    PolicyResource("ui:companies:update", "action", "Компании: редактировать"),
    PolicyResource("ui:companies:delete", "action", "Компании: удалить", sensitive=True),
    PolicyResource("ui:companies:bulk_transfer", "action", "Компании: массовая передача", sensitive=True),
    PolicyResource("ui:companies:cold_call:toggle", "action", "Холодный звонок: отметить"),
    PolicyResource("ui:companies:cold_call:reset", "action", "Холодный звонок: откат", sensitive=True),

    PolicyResource("ui:tasks:create", "action", "Задачи: создать"),
    PolicyResource("ui:tasks:update", "action", "Задачи: редактировать"),
    PolicyResource("ui:tasks:delete", "action", "Задачи: удалить", sensitive=True),
    PolicyResource("ui:tasks:bulk_reassign", "action", "Задачи: массовое переназначение", sensitive=True),
    PolicyResource("ui:tasks:status", "action", "Задачи: смена статуса"),

    PolicyResource("ui:mail:campaigns:manage", "action", "Почта: управление кампаниями"),
    PolicyResource("ui:mail:smtp_settings", "action", "Почта: настройки SMTP", sensitive=True),

    # ---- DRF API actions ----
    PolicyResource("api:companies:list", "action", "API: компании (list)"),
    PolicyResource("api:companies:retrieve", "action", "API: компании (retrieve)"),
    PolicyResource("api:companies:create", "action", "API: компании (create)", sensitive=True),
    PolicyResource("api:companies:update", "action", "API: компании (update)", sensitive=True),
    PolicyResource("api:companies:delete", "action", "API: компании (delete)", sensitive=True),

    PolicyResource("api:tasks:list", "action", "API: задачи (list)"),
    PolicyResource("api:tasks:retrieve", "action", "API: задачи (retrieve)"),
    PolicyResource("api:tasks:create", "action", "API: задачи (create)"),
    PolicyResource("api:tasks:update", "action", "API: задачи (update)"),
    PolicyResource("api:tasks:delete", "action", "API: задачи (delete)", sensitive=True),

    # ---- Phone/Android API ----
    PolicyResource("phone:devices:register", "action", "Phone API: регистрация устройства"),
    PolicyResource("phone:devices:heartbeat", "action", "Phone API: heartbeat устройства"),
    PolicyResource("phone:calls:pull", "action", "Phone API: получить команду звонка"),
    PolicyResource("phone:calls:update", "action", "Phone API: обновить информацию о звонке"),
    PolicyResource("phone:telemetry", "action", "Phone API: телеметрия"),
    PolicyResource("phone:logs:upload", "action", "Phone API: загрузка логов", sensitive=True),
    PolicyResource("phone:qr:create", "action", "Phone API: создать QR токен", sensitive=True),
    PolicyResource("phone:qr:exchange", "action", "Phone API: обмен QR токена"),
    PolicyResource("phone:logout", "action", "Phone API: logout"),
    PolicyResource("phone:logout_all", "action", "Phone API: logout all", sensitive=True),
    PolicyResource("phone:user:info", "action", "Phone API: user info"),
)


RESOURCE_INDEX: dict[str, PolicyResource] = {r.key: r for r in RESOURCES}


def is_known_resource(key: str) -> bool:
    return key in RESOURCE_INDEX


def list_resources(*, resource_type: ResourceType | None = None) -> list[PolicyResource]:
    if resource_type is None:
        return list(RESOURCES)
    return [r for r in RESOURCES if r.resource_type == resource_type]

