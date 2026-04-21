"""
Uptime Kuma automated bootstrap — Wave 0.4 closeout (2026-04-21).

Выполняет один раз после `docker compose up -d` Kuma стека:
1. Создаёт admin user (если setup не сделан).
2. Создаёт Telegram notification channel (reuses token from
   /etc/proficrm/env.d/telegram-alerts.conf).
3. Отправляет test-notification (проверка что Telegram доставка работает).
4. Создаёт 3 HTTP monitors: CRM prod, CRM staging, GlitchTip.

Идемпотентный — повторный запуск пропустит существующие сущности.

Использование (на staging VPS):
    # Python + uptime-kuma-api недоступны на хосте, поэтому запускаем
    # внутри glitchtip-web контейнера, подключённого к Kuma network:

    # 1. Временно подключить glitchtip-web к kuma network:
    docker network connect proficrm-uptime_default \
        proficrm-observability-glitchtip-web-1

    # 2. Убедиться что uptime-kuma-api установлен:
    docker exec proficrm-observability-glitchtip-web-1 pip install --quiet uptime-kuma-api

    # 3. Copy скрипт + env vars:
    docker cp scripts/kuma-bootstrap.py proficrm-observability-glitchtip-web-1:/tmp/
    docker exec \
        -e KUMA_URL=http://uptime-kuma:3001 \
        -e KUMA_ADMIN_PWD="$(grep KUMA_ADMIN_PWD /etc/proficrm/env.d/kuma-admin.conf | cut -d= -f2)" \
        -e TELEGRAM_ALERT_TOKEN="$(grep TELEGRAM_ALERT_TOKEN /etc/proficrm/env.d/telegram-alerts.conf | cut -d= -f2)" \
        -e TELEGRAM_ALERT_CHAT_ID="$(grep TELEGRAM_ALERT_CHAT_ID /etc/proficrm/env.d/telegram-alerts.conf | cut -d= -f2)" \
        proficrm-observability-glitchtip-web-1 python /tmp/kuma-bootstrap.py

    # 4. Отключить обратно (изоляция network):
    docker network disconnect proficrm-uptime_default \
        proficrm-observability-glitchtip-web-1

После успешного запуска:
- Admin user: "admin", пароль в /etc/proficrm/env.d/kuma-admin.conf
- 3 monitor'а видны в UI https://uptime.groupprofi.ru/
- При падении staging/prod/glitchtip — alert в Telegram chat_id 1363929250
"""

from __future__ import annotations

import os
import sys

from uptime_kuma_api import MonitorType, NotificationType, UptimeKumaApi


def _require_env(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        print(f"ERROR: env var {name} required", file=sys.stderr)
        sys.exit(2)
    return val


def main() -> None:
    kuma_url = _require_env("KUMA_URL")
    admin_pwd = _require_env("KUMA_ADMIN_PWD")
    tg_token = _require_env("TELEGRAM_ALERT_TOKEN")
    tg_chat = _require_env("TELEGRAM_ALERT_CHAT_ID")

    api = UptimeKumaApi(kuma_url)

    # --- Admin setup (first run) or login (subsequent runs) ---
    try:
        api.setup("admin", admin_pwd)
        print("setup: admin created")
    except Exception as e:  # already set up — ok
        print(f"setup: skipped ({e!s:.80})")
    api.login("admin", admin_pwd)
    print("login: ok")

    # --- Telegram notification channel ---
    notif_id: int | None = None
    for n in api.get_notifications():
        if n.get("name") == "Telegram Admin Alerts":
            notif_id = n["id"]
            print(f"notification: already exists id={notif_id}")
            break

    if notif_id is None:
        res = api.add_notification(
            name="Telegram Admin Alerts",
            type=NotificationType.TELEGRAM,
            isDefault=True,
            applyExisting=True,
            telegramBotToken=tg_token,
            telegramChatID=tg_chat,
        )
        notif_id = res.get("id")
        print(f"notification: created id={notif_id}")

    # --- Test notification (once on fresh setup) ---
    try:
        api.test_notification(
            name="Bootstrap test",
            type=NotificationType.TELEGRAM,
            telegramBotToken=tg_token,
            telegramChatID=tg_chat,
        )
        print("test notification: sent (check Telegram)")
    except Exception as e:
        print(f"test notification: {e!s:.80}")

    # --- 3 monitors ---
    desired = [
        {
            "name": "CRM Production",
            "url": "https://crm.groupprofi.ru/health/",
            "interval": 60,
            "retryInterval": 60,
            "maxretries": 3,
        },
        {
            "name": "CRM Staging",
            "url": "https://crm-staging.groupprofi.ru/live/",
            "interval": 60,
            "retryInterval": 60,
            "maxretries": 3,
        },
        {
            "name": "GlitchTip",
            "url": "https://glitchtip.groupprofi.ru/_health/",
            "interval": 120,
            "retryInterval": 60,
            "maxretries": 2,
        },
    ]

    existing = {m["name"]: m["id"] for m in api.get_monitors()}
    for spec in desired:
        if spec["name"] in existing:
            print(f"monitor: '{spec['name']}' exists id={existing[spec['name']]}")
            continue
        res = api.add_monitor(
            type=MonitorType.HTTP,
            name=spec["name"],
            url=spec["url"],
            interval=spec["interval"],
            retryInterval=spec["retryInterval"],
            maxretries=spec["maxretries"],
            notificationIDList=[notif_id] if notif_id else [],
        )
        print(f"monitor: '{spec['name']}' created id={res.get('monitorID')}")

    api.disconnect()
    print("done")


if __name__ == "__main__":
    main()
