# PM current context

_Живое состояние текущей PM-сессии. PM обновляет этот файл перед предсказуемым compact или каждые 30-60 минут активной работы. После compact — читается ПЕРВЫМ для восстановления контекста._

**Last updated:** 2026-04-24 11:20 UTC (PM).

---

## 🎯 Current session goal

Возобновление W10.2-early — ключи Cloudflare (`CF_API_TOKEN`, `CF_ACCOUNT_ID`) доставлены на `/opt/proficrm-staging/.env`. Исполнитель в расширенной сессии: создаёт бакет R2 + S3-совместимые R2-креды через Cloudflare API, записывает в `.env`, потом ставит WAL-G + PITR end-to-end с обязательным restore drill.

## 📋 Active constraints

- Path E: **ACTIVE**.
- Режим исполнителя: только стейджинг.
- Ключи `CF_API_TOKEN` + `CF_ACCOUNT_ID` на `/opt/proficrm-staging/.env` (пермишены 600).
- Защитный слой (pg_dump ежедневно 03:30 UTC) работает — закоммичено `1e0af81b`.

## 🔄 Last decision made

**Timestamp:** 2026-04-24 11:15 UTC.
**Decision:** продолжить W10.2-early расширенной сессией — R2 setup через Cloudflare API + WAL-G install + restore drill в одной передаче.
**Reasoning:** ключи Cloudflare на VPS, исполнитель может автоматически создать бакет и S3-совместимые креды через API. Если Cloudflare API не поддерживает permanent S3-compatible credentials — stop condition на шаге 1, Дмитрий создаст в дашборде вручную (5 минут).
**Owner:** Дмитрий одобрил, PM пишет промпт.

## ⏭️ Next expected action

1. ✅ Обновить `docs/pm/current-context.md` (этот файл).
2. ✅ Коммит.
3. ⏭️ Написать расширенный промпт — 8 шагов, 5-7 часов, с security-дисциплиной (никаких литералов секретов в логи).
4. ⏭️ Передать Дмитрию для copy-paste исполнителю.
5. ⏭️ После рапорта (через 5-7 часов) — review restore drill доказательства + классификация.

## ❓ Pending questions to Дмитрий

Нет открытых вопросов. Ключи на месте, промпт готовится.

## 📊 Last Executor rapport summary

**Session:** мини-сессия pg_dump (staging safety net).
**Received:** 2026-04-24 10:50 UTC.
**Status:** 🟢 COMPLETE (~17 мин под бюджет 15-30).
**Classification:** win — защитный слой восстановлен.

Следующий ожидаемый рапорт: W10.2-early WAL-G setup end-to-end, через 5-7 часов после старта.

## 🚨 Red flags (if any)

### Security incident (resolved): токен Cloudflare в чате

**2026-04-24 11:00-11:15 UTC.** Дмитрий послал CF API-токен прямо в чат (`cfut_...`). PM остановил работу, flag-нул риск (транскрипты, логи Anthropic, риск коммита в публичный репо), рекомендовал немедленный revoke. Токен отозван. Новый токен создан Дмитрием и положен напрямую в `/opt/proficrm-staging/.env` через SSH.

**Lesson candidate (Lesson 9):** PM failure — в предыдущем сообщении следовало ЯВНО указать «передай токен исполнителю ТОЛЬКО через SSH `.env` на VPS, не в чат». Вместо этого был мягкий вариант «передай мне или исполнителю». Упущение. Добавить в lessons-learned после W10.2-early closure.

## 📝 Running notes

### Что должен сделать исполнитель в расширенной сессии

**Фаза R2 setup (30-60 минут):**

- Шаг 0: branch check (`1e0af81b` в топе), baseline, проверка что CF_API_TOKEN + CF_ACCOUNT_ID читаются из `.env`.
- Шаг 1: создать бакет `proficrm-walg-staging` через Cloudflare API. Создать R2 API Token (S3-совместимые `access_key_id` + `secret_access_key`) либо через Cloudflare API (если endpoint поддерживает), либо stop + Дмитрий делает через дашборд. Записать в `.env`: `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_ENDPOINT`, `R2_BUCKET_NAME`.

**Фаза WAL-G (4-6 часов, прежний scope):**

- Шаг 2: установка WAL-G binary + `/etc/wal-g/walg.env`.
- Шаг 3: `archive_mode=on`, `archive_command`, перезапуск Postgres.
- Шаг 4: первый full base backup + верификация archiving.
- Шаг 5: restore drill с отдельным контейнером (critical).
- Шаг 6: runbook + retention cron.
- Шаг 7: smoke + rapport.

### Security-дисциплина для исполнителя

- Не echo'ить литералы токенов в терминал / логи.
- `curl` с `Authorization: Bearer $CF_API_TOKEN` без `-v`, без `--trace`.
- API response с секретом — сразу перенаправить в `.env` / переменную, не `cat`-ить.
- Если `set -x` активно — временно `set +x` перед работой с токенами.
- После завершения W10.2-early: предложить Дмитрию отозвать CF_API_TOKEN (нужен был только для setup, WAL-G пользуется R2 S3 creds, которые намного уже по scope).

### Update triggers (reminder)

- Перед каждой передачей Дмитрию.
- Каждые 30-60 минут активной работы.
- После получения рапорта исполнителя.
- После принятия решения.
- Перед длительной операцией.
- При приближении к компактификации контекста.
