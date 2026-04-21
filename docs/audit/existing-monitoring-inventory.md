# Existing monitoring inventory — 2026-04-21

_Обнаружено при W0.4 regression investigation Track I._

## Overlap: старый скрипт + Kuma шлют в один Telegram канал

Пользователь заметил: Telegram канал «ПРОФИ CRM - Уведомления» (chat_id
`1363929250`) получает сообщения **из двух источников**:

1. Старый bash-скрипт (с марта 2026) — формат `🔴 CRM ПРОФИ — УПАЛ` / `🟢 ВОССТАНОВЛЕН`
2. Kuma (с 2026-04-21) — формат `[CRM Staging] [🔴 Down] ...`

## Inventory старого скрипта

### Path
`/opt/proficrm/scripts/health_alert.sh` (в git repo main branch).

### Schedule
Crontab пользователя `sdm`:
```cron
*/5 * * * * bash /opt/proficrm/scripts/health_alert.sh
```

### Что мониторит
- **URL**: `http://127.0.0.1:8001/health/` (локальный внутри prod VPS, минуя nginx/TLS)
- **Success**: HTTP 200 + JSON body содержит `"status": "ok"`
- **Failure**: любой не-200 код или отсутствие `"status": "ok"` в body

### Scope
- **Только prod CRM** на localhost:8001 (именно gunicorn `proficrm-web-1`).
- **НЕ мониторит** staging (другой порт/сервер).
- **НЕ мониторит** GlitchTip (добавлен только в W0.4).

### Куда шлёт
- Bot: `@proficrmdarbyoff_bot` (TG_BOT_TOKEN из `/opt/proficrm/.env`)
- Chat: `TG_CHAT_ID` из prod .env = `1363929250` (личный Telegram владельца)

### Логика алертов
**State-based**: алерт только при смене статуса (up→down или down→up).
Состояние хранится в `/tmp/crm_health_state`. Formatting через HTML + emoji.

Пример DOWN:
```
🔴 CRM ПРОФИ — УПАЛ

❌ {detail}
🕐 DD.MM.YYYY HH:MM:SS
🔗 https://crm.groupprofi.ru
```

### Связанный скрипт
`/opt/proficrm/scripts/log_alert.sh` — cron `*/15 * * * *`. Мониторит ErrorLog
(БД Django) на рост новых записей. Другая цель (не uptime), не блокирует Kuma.

## Overlap анализ (prod CRM)

| Source | Method | Interval | Alert на: | Alert format |
|--------|--------|----------|-----------|--------------|
| health_alert.sh | Local HTTP 127.0.0.1:8001 | 5 мин | Django app down / 503 | `🔴 CRM ПРОФИ — УПАЛ` |
| Kuma monitor #1 (CRM Production) | External HTTPS crm.groupprofi.ru/health/ | 1 мин | Sirver / nginx / Django down | `[CRM Production] [🔴 Down]` |

**Совпадающие failure-ы при которых оба alert шлются**:
- Django app crash → оба видят.
- DB/Redis unhealthy → оба видят через /health/ 503.

**Отличающиеся failure-ы**:
- nginx/TLS error → только Kuma видит (health_alert.sh ходит локально, минуя nginx).
- Network isolation (VPS без внешнего интернета) → только health_alert.sh видит
  (Kuma ходит через external URL — при cutoff Kuma не сможет проверять и
  сам выключится).

## Рекомендация (ADR draft — waiting Q9 answer)

**Option C (split scope)** — рекомендовано:
- health_alert.sh остаётся как **внутренний probe** (localhost, не внешний).
  Сохраняет historical continuity, custom checks, независимость от DNS/TLS.
- Kuma отвечает за **внешний uptime** (nginx+TLS layer) + staging + GlitchTip.
- Прод alert от health_alert.sh удобнее при network-cutoff (Kuma не видит).

**Как реализовать**:
1. Kuma: убрать monitor #1 CRM Production. Оставить только Staging + GlitchTip.
2. Добавить в Kuma: **prod через другой path** (например `/` → 200/302),
   чтобы различать внешний uptime от internal. Но это дубль health_alert.sh
   при cascade failure.

**Альтернатива Option B** (только Kuma):
- Выключить `*/5 * * * * bash /opt/proficrm/scripts/health_alert.sh` cron.
- Keep Kuma. Проще, но теряем историю «кто когда падал» за март-апрель 2026.

**Пользователь выбирает в Q9** (см. `docs/open-questions.md`).

## Q9 resolved 2026-04-21 — Option C (split-scope) implemented

Пользователь выбрал вариант **C — split-scope**. Реализация:

1. **Kuma monitor #1 «CRM Production» paused** (не удалён — history сохраняется
   для возможного re-enable). Action: `api.pause_monitor(id=1)` через
   `uptime-kuma-api` client. Scripted в `scripts/_kuma_pause_prod.py`.
2. **health_alert.sh** (cron `*/5 * * * *` от sdm) — **остаётся активным**,
   единственный источник prod uptime alerts.
3. **Uptime Kuma self-check добавлен** (id=4): HTTP HEAD на
   `https://uptime.groupprofi.ru/` (401 принимается как OK = basic auth = nginx живой).

### Текущий split

| Источник | Scope | Active? |
|----------|-------|---------|
| `scripts/health_alert.sh` | prod CRM `127.0.0.1:8001/health/` (local) | ✅ yes |
| Kuma monitor #1 CRM Production | ❌ paused (было crm.groupprofi.ru/health/ external) | ❌ no |
| Kuma monitor #2 CRM Staging | crm-staging.groupprofi.ru/live/ | ✅ yes |
| Kuma monitor #3 GlitchTip | glitchtip.groupprofi.ru/_health/ | ✅ yes |
| Kuma monitor #4 Uptime Kuma self | uptime.groupprofi.ru/ (HEAD, 401 OK) | ✅ yes |

Zero overlap на prod. Telegram чат получает:
- `🔴 CRM ПРОФИ — УПАЛ` / `🟢 ВОССТАНОВЛЕН` — **только** от health_alert.sh (prod).
- `[Monitor] [🔴 Down]` — **только** от Kuma (staging / GlitchTip / uptime-self).
