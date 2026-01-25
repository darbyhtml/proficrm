# Рассылки (Почта): лимиты, статусы, DEFER

## Лимиты

- **per_user_daily_limit** — макс. писем в день на одного менеджера (создателя кампании). По умолчанию 100. Задаётся в `GlobalMailAccount.per_user_daily_limit` (Почта → Настройки).
- **max_per_hour** — макс. писем в час (глобально). Берётся из API smtp.bz (`SmtpBzQuota`) или дефолт 100.
- **Рабочие часы** — 9:00–18:00 МСК. Вне этого окна новые рассылки не стартуют; уже отложенные (deferred) — продолжаются с начала следующего окна.

### Как считается «сегодня»

Границы дня — по московскому времени (МСК). Используется `mailer.utils.msk_day_bounds(now)` → `(start_utc, end_utc, now_msk)`. Подсчёт `sent_today_user` в `SendLog`: `created_at` в `[start_utc, end_utc)`.

## Статусы кампании (Campaign.status)

| Статус   | Описание |
|----------|----------|
| DRAFT    | Черновик |
| READY    | Готово к отправке, в очереди |
| SENDING  | Идёт отправка |
| PAUSED   | На паузе **вручную** (пользователь нажал «Пауза») |
| SENT     | Все письма обработаны |
| STOPPED  | Остановлено |

## Статусы очереди (CampaignQueue.status)

| Статус      | Описание |
|-------------|----------|
| PENDING     | В очереди (в т.ч. отложена по deferred_until) |
| PROCESSING  | Сейчас обрабатывается воркером |
| COMPLETED   | Завершена |
| CANCELLED   | Отменена (ручная пауза, SMTP выключен и т.п.) |

## DEFER (отложенное продолжение)

При достижении **дневного лимита** кампания **не** переводится в PAUSED и **не** требует ручного «Продолжить». Очередь получает:

- **deferred_until** — время начала следующего окна (завтра 9:00 МСК при рабочих часах, иначе завтра 0:05).
- **defer_reason** — `daily_limit`, `quota_exhausted`, `outside_hours`, `rate_per_hour`.

Воркер не обрабатывает записи с `deferred_until > now`. На следующий день рассылка продолжается автоматически.

### Другие причины DEFER

- **quota_exhausted** — квота smtp.bz = 0: очередь остаётся в PENDING, без `deferred_until` (повтор на следующем цикле).
- **rate_per_hour** — лимит в час: `PROCESSING` возвращают в `PENDING`, `started_at` сбрасывают; без `deferred_until`.
- **outside_hours** — вне 9–18 МСК: новые не стартуют; уведомление «возобновим в 09:00».

## Настройки (константы / конфиг)

- `mailer.constants.PER_USER_DAILY_LIMIT_DEFAULT` = 100
- `mailer.constants.WORKING_HOURS_START` = 9, `WORKING_HOURS_END` = 18
- `mailer.constants.DEFER_REASON_*` — коды причин отложения
- `settings.TIME_ZONE` (по умолчанию `Europe/Moscow`) — для `get_next_send_window_start`
- `GlobalMailAccount.per_user_daily_limit` — переопределение лимита на менеджера

## Задачи Celery

- **send_pending_emails** (каждые 60 с) — разбор очереди, отправка батча, при дневном лимите — DEFER.
- **reconcile_campaign_queue** (≈5 мин) — согласование очереди и статусов кампаний.
- **sync_smtp_bz_quota**, **sync_smtp_bz_unsubscribes**, **sync_smtp_bz_delivery_events** — работа с API smtp.bz.

## Поведение кнопок в UI

- **Пауза** — при READY/SENDING; при отложенной по дневному лимиту кампания остаётся READY, показывается баннер «Продолжим завтра в HH:MM».
- **Продолжить** — при PAUSED. Если лимит сегодня исчерпан: кампания переводится в READY, очереди выставляется `deferred_until` на завтра, пользователю показывается сообщение «Продолжим завтра в HH:MM».
