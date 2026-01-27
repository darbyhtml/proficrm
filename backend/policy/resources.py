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
    PolicyResource("ui:mobile_app", "page", "Мобильное приложение"),
    PolicyResource("ui:settings", "page", "Админка (раздел settings)", sensitive=True),
    PolicyResource("ui:mail", "page", "Почта (раздел)"),
    PolicyResource("ui:notifications", "page", "Уведомления (раздел)"),
    PolicyResource("ui:mail:settings", "page", "Почта: настройки (страница)"),
    PolicyResource("ui:mail:signature", "page", "Почта: подпись (страница)"),
    PolicyResource("ui:mail:campaigns", "page", "Почта: кампании (список)"),
    PolicyResource("ui:mail:campaigns:detail", "page", "Почта: кампания (карточка)"),
    PolicyResource("ui:notifications:all", "page", "Уведомления: все"),
    PolicyResource("ui:notifications:reminders", "page", "Уведомления: напоминания"),

    # ---- Web UI actions (опасные/изменяющие) ----
    PolicyResource("ui:companies:create", "action", "Компании: создать"),
    PolicyResource("ui:companies:update", "action", "Компании: редактировать"),
    PolicyResource("ui:companies:delete", "action", "Компании: удалить", sensitive=True),
    PolicyResource("ui:companies:bulk_transfer", "action", "Компании: массовая передача", sensitive=True),
    PolicyResource("ui:companies:cold_call:toggle", "action", "Холодный звонок: отметить"),
    PolicyResource("ui:companies:cold_call:reset", "action", "Холодный звонок: откат", sensitive=True),
    PolicyResource("ui:companies:delete_request:create", "action", "Компании: запрос на удаление (создать)", sensitive=True),
    PolicyResource("ui:companies:delete_request:cancel", "action", "Компании: запрос на удаление (отклонить)", sensitive=True),
    PolicyResource("ui:companies:delete_request:approve", "action", "Компании: запрос на удаление (подтвердить)", sensitive=True),
    PolicyResource("ui:companies:export", "action", "Компании: экспорт", sensitive=True),
    PolicyResource("ui:companies:autocomplete", "action", "Компании: автодополнение"),
    PolicyResource("ui:companies:duplicates", "action", "Компании: поиск дублей"),
    PolicyResource("ui:companies:contract:update", "action", "Компании: обновить договор"),
    PolicyResource("ui:companies:transfer", "action", "Компании: передать"),

    PolicyResource("ui:tasks:create", "action", "Задачи: создать"),
    PolicyResource("ui:tasks:update", "action", "Задачи: редактировать"),
    PolicyResource("ui:tasks:delete", "action", "Задачи: удалить", sensitive=True),
    PolicyResource("ui:tasks:bulk_reassign", "action", "Задачи: массовое переназначение", sensitive=True),
    PolicyResource("ui:tasks:status", "action", "Задачи: смена статуса"),

    PolicyResource("ui:mail:campaigns:manage", "action", "Почта: управление кампаниями"),
    PolicyResource("ui:mail:smtp_settings", "action", "Почта: настройки SMTP", sensitive=True),
    PolicyResource("ui:mail:settings:update", "action", "Почта: настройки SMTP (изменить)", sensitive=True),
    PolicyResource("ui:mail:quota:poll", "action", "Почта: квота (poll)"),
    PolicyResource("ui:mail:progress:poll", "action", "Почта: прогресс рассылки (poll)"),
    PolicyResource("ui:mail:unsubscribes:list", "action", "Почта: отписки (список)", sensitive=True),
    PolicyResource("ui:mail:unsubscribes:delete", "action", "Почта: отписки (удалить)", sensitive=True),
    PolicyResource("ui:mail:unsubscribes:clear", "action", "Почта: отписки (очистить всё)", sensitive=True),
    PolicyResource("ui:mail:campaigns:create", "action", "Почта: кампании (создать)"),
    PolicyResource("ui:mail:campaigns:edit", "action", "Почта: кампании (редактировать)"),
    PolicyResource("ui:mail:campaigns:delete", "action", "Почта: кампании (удалить)", sensitive=True),
    PolicyResource("ui:mail:campaigns:pick", "action", "Почта: кампании (pick)"),
    PolicyResource("ui:mail:campaigns:add_email", "action", "Почта: кампании (добавить email)"),
    PolicyResource("ui:mail:campaigns:recipients:add", "action", "Почта: получатели (добавить)", sensitive=True),
    PolicyResource("ui:mail:campaigns:recipients:delete", "action", "Почта: получатели (удалить)", sensitive=True),
    PolicyResource("ui:mail:campaigns:recipients:bulk_delete", "action", "Почта: получатели (массово удалить)", sensitive=True),
    PolicyResource("ui:mail:campaigns:recipients:generate", "action", "Почта: получатели (сгенерировать)", sensitive=True),
    PolicyResource("ui:mail:campaigns:recipients:reset_failed", "action", "Почта: получатели (сбросить ошибки)", sensitive=True),
    PolicyResource("ui:mail:campaigns:recipients:reset_all", "action", "Почта: получатели (сбросить включая отправленные)", sensitive=True),
    PolicyResource("ui:mail:campaigns:clear", "action", "Почта: кампания (очистить)", sensitive=True),
    PolicyResource("ui:mail:campaigns:send_step", "action", "Почта: кампания (send step)", sensitive=True),
    PolicyResource("ui:mail:campaigns:start", "action", "Почта: кампания (старт)", sensitive=True),
    PolicyResource("ui:mail:campaigns:pause", "action", "Почта: кампания (пауза)", sensitive=True),
    PolicyResource("ui:mail:campaigns:resume", "action", "Почта: кампания (продолжить)", sensitive=True),
    PolicyResource("ui:mail:campaigns:test_send", "action", "Почта: кампания (тест отправки)", sensitive=True),

    PolicyResource("ui:notifications:poll", "action", "Уведомления: poll"),
    PolicyResource("ui:notifications:mark_read", "action", "Уведомления: отметить прочитанным"),
    PolicyResource("ui:notifications:mark_all_read", "action", "Уведомления: отметить все прочитанными"),

    PolicyResource("ui:mobile_app:download", "action", "Мобильное приложение: скачать APK", sensitive=True),
    PolicyResource("ui:mobile_app:qr", "action", "Мобильное приложение: QR вход"),

    PolicyResource("ui:settings:view_as:update", "action", "Settings: режим просмотра (изменить)", sensitive=True),
    PolicyResource("ui:settings:view_as:reset", "action", "Settings: режим просмотра (сбросить)", sensitive=True),

    # ---- DRF API actions ----
    PolicyResource("api:companies:list", "action", "API: компании (list)"),
    PolicyResource("api:companies:retrieve", "action", "API: компании (retrieve)"),
    PolicyResource("api:companies:create", "action", "API: компании (create)", sensitive=True),
    PolicyResource("api:companies:update", "action", "API: компании (update)", sensitive=True),
    PolicyResource("api:companies:delete", "action", "API: компании (delete)", sensitive=True),

    PolicyResource("api:contacts:list", "action", "API: контакты (list)"),
    PolicyResource("api:contacts:retrieve", "action", "API: контакты (retrieve)"),
    PolicyResource("api:contacts:create", "action", "API: контакты (create)", sensitive=True),
    PolicyResource("api:contacts:update", "action", "API: контакты (update)", sensitive=True),
    PolicyResource("api:contacts:delete", "action", "API: контакты (delete)", sensitive=True),

    PolicyResource("api:company_notes:list", "action", "API: заметки компаний (list)"),
    PolicyResource("api:company_notes:retrieve", "action", "API: заметки компаний (retrieve)"),
    PolicyResource("api:company_notes:create", "action", "API: заметки компаний (create)", sensitive=True),
    PolicyResource("api:company_notes:update", "action", "API: заметки компаний (update)", sensitive=True),
    PolicyResource("api:company_notes:delete", "action", "API: заметки компаний (delete)", sensitive=True),

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

