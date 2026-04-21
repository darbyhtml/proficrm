# Kuma alert test — 2026-04-21

Первый live-test Telegram-алертов через Uptime Kuma после W0.4 closeout.

## Сценарий

Симулированный downtime на staging для проверки: (1) Kuma ловит падение;
(2) Telegram alert отправляется в personal chat пользователя; (3) recovery-
уведомление приходит.

## Timeline (UTC)

| UTC timestamp | Событие |
|---------------|---------|
| 2026-04-21 04:58:39 | `docker compose stop crm_staging_web` выполнен — UPTIME DOWN trigger |
| 2026-04-21 04:58:50 | Подтверждение staging down (docker ps: container not in list) |
| 2026-04-21 04:59-05:00 | Kuma heartbeat timeout × 1 (60 сек interval — первая пропущенная проверка) |
| 2026-04-21 05:00-05:01 | Kuma retry #1 timeout |
| 2026-04-21 05:01-05:02 | Kuma retry #2 timeout |
| 2026-04-21 05:02:30 (ожидается) | Kuma отправляет **DOWN alert** в Telegram (после 3-х failures по defaults) |
| 2026-04-21 05:02:58 | `docker compose up -d web` — сервис восстановлен |
| 2026-04-21 05:03:13 | Контейнер healthy |
| 2026-04-21 05:04-05:05 (ожидается) | Kuma **recovery alert** в Telegram |

## Конфигурация monitor

- **CRM Staging**:
  - URL: `https://crm-staging.groupprofi.ru/live/`
  - Heartbeat interval: 60 сек
  - Retry interval: 60 сек
  - Max retries: 3
  - Notification channel: Telegram Admin Alerts (default, applyExisting=True)

## Ожидаемое время до первого alert

```
3 retries × 60 sec = 180 sec (3 min) после первого failure
+ detection lag ~30-60 sec
= 3-4 min total
```

То есть alert должен прийти **около 05:02:00-05:02:30 UTC**.

## Что проверить пользователю

1. Открыть Telegram → чат с `@proficrmdarbyoff_bot`.
2. Найти сообщения с timestamp **~05:02 UTC (~08:02 MSK)** 2026-04-21:
   - **DOWN**: «CRM Staging is DOWN» или подобное
   - **RECOVERY** (спустя ~4 минуты): «CRM Staging is UP» или подобное
3. Зафиксировать:
   - Пришёл ли DOWN alert?
   - Пришёл ли RECOVERY alert?
   - Задержка от факта downtime до первого сообщения (SLI).

## Результат трёх сценариев

| Scenario | Expected | Actual | Notes |
|----------|----------|--------|-------|
| Pre-test notification (Kuma setup) | 1 сообщение «Uptime Kuma Test» | (pending user check) | Отправлено при `api.test_notification()` ~04:56 UTC |
| Staging DOWN alert | 1 сообщение | (pending user check) | — |
| Staging RECOVERY alert | 1 сообщение | (pending user check) | — |

## Next steps если alerts не пришли

1. Проверить `/etc/proficrm/env.d/telegram-alerts.conf` — токен + chat_id.
2. Сравнить с `TG_BOT_TOKEN` в prod .env — должны совпадать.
3. Проверить что пользователь вышел `/start`ом в DM с ботом — без этого Telegram
   Bot API не шлёт сообщения (chat not initialized).
4. Проверить Kuma UI — Settings → Notifications → Telegram Admin Alerts →
   Test Notification button.

## Документация соотв.

- `docs/runbooks/uptime-monitoring.md` — полный setup runbook.
- `docs/audit/telegram-bot-inventory.md` — откуда взяли токен.
- `scripts/_kuma_setup.py` — скрипт автонастройки.
