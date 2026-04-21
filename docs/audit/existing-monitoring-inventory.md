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

## До решения Q9 — что делаем

**Ничего не меняем.** Оба работают. Пользователь видит дубли в Telegram, но:
- Оба алерта state-based (только при смене) — не спам каждую минуту.
- Timing отличается: Kuma 1 мин, old script 5 мин — first-alert от Kuma, follow-up от old script.
- Forget cost низкий, но double на redshold изменения.

Рекомендация до Q9: Q9 решается «в течение недели» — не критично.
