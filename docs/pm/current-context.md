# PM current context

_Живое состояние текущей PM-сессии. PM обновляет этот файл перед предсказуемым compact или каждые 30-60 минут активной работы. После compact — читается ПЕРВЫМ для восстановления контекста._

**Last updated:** 2026-04-24 15:20 UTC (PM).

---

## 🎯 Current session goal

W10.2-early 🔴 **STILL BLOCKED после fix-сессии**. Исполнитель fix'нул permissions (chown успешно), но натолкнулся на более глубокий архитектурный блокер: wal-g **внутри контейнера** не может установить соединение с Cloudflare R2 (IPv6 resolver hang + HTTP/2 connection failure). Plus wrapper-скрипт теряет `%p` параметр (передаёт пустую строку в `wal-push`). Исполнитель готовит рапорт.

## 📋 Active constraints

- Path E: **ACTIVE**.
- Staging API работает (HTTP 200), 7/7 контейнеров healthy.
- Защитный слой pg_dump активен.
- R2 bucket `proficrm-walg-staging` всё ещё пустой (не изменился).
- `archive_mode = on`, `archive_command = /etc/wal-g/archive-command.sh %p`, `archive_timeout = 1min`.

## 🔄 Last decision made

**Timestamp:** 2026-04-24 15:20 UTC.
**Decision:** ждать рапорт исполнителя, потом решать rollback vs fix in place.
**Reasoning:** исполнитель сам пишет «let me clean up then produce honest rapport» — пусть закончит cleanup, даст полную картину. Я как PM даю брифинг Дмитрию с моими findings, Дмитрий решит стратегию.
**Owner:** Дмитрий (decision), исполнитель (rapport), PM (analysis + options).

## ⏭️ Next expected action

1. ✅ Обновить `docs/pm/current-context.md`.
2. ✅ Коммит.
3. ⏭️ Брифинг Дмитрию — что я нашёл на VPS, options на решение.
4. ⏭️ Ждать рапорт исполнителя.
5. ⏭️ После рапорта — финальное решение с Дмитрием: rollback до baseline (archive_mode=off, только pg_dump) **vs** deep-dive thinking session для architecture fix.

## ❓ Pending questions to Дмитрий

- [ ] После рапорта исполнителя — какую стратегию выбираем:
  - **A:** Immediate rollback archive_command → `/bin/true` (trivial no-op), archive_mode остаётся on. Staging стабилен, pg_dump продолжает быть safety net. W10.2-early закрыть как «PARTIAL, blocked на container networking, retry после архитектурного review».
  - **B:** Pivot architecture — wal-g запускается с хоста (не из контейнера), archive_command пишет WAL в shared spool dir, host cron push'ит. Обходит container networking issues.
  - **C:** Deep-dive в container networking — разобрать HTTP/2 / IPv6 block, fix в docker-compose или walg.env.

## 📊 Diagnostic findings (PM side, 15:10-15:20 UTC)

### Состояние postgres за последний час

**🔴 Postgres нестабилен — 3 recovery цикла за 15 минут:**

- `14:57:12` — all server processes terminated, automatic recovery.
- `15:02:21` — PID 11749 **exit code 124 (timeout)**, terminated + recovery.
- `15:11:39` — PID 11776 terminated by signal 15 (SIGTERM), recovery.

Exit 124 = timeout. Скорее всего `archive_command` висит дольше чем `archive_timeout` (1 минута), постgres убивает процесс. Постоянный цикл: archive попытка → timeout → kill → recovery → новая попытка.

Каждый recovery — несколько секунд простоя. Staging **выглядит** healthy снаружи (HTTP 200), но db-контейнер внутри страдает.

### Wrapper script bug

`/etc/wal-g/archive-command.sh`:

```bash
#!/bin/bash
# W10.2-early: archive_command wrapper.
# Loads R2 creds from walg.env и invokes wal-g wal-push.
# envdir was not installed в postgres:16 Debian image — so we source env manually.

set -e
set -a
. /etc/wal-g/walg.env
set +a
exec /usr/local/bin/wal-g wal-push ""
```

**Bug:** `wal-push ""` — передаёт **пустую строку** вместо аргумента `%p`. Postgres вызывает `/etc/wal-g/archive-command.sh /path/to/WAL_FILE`, script получает путь как `$1`, но передаёт в wal-g empty. WAL-G получает пустое имя файла и... непонятно что делает, но явно не архивирует правильно.

**Правильная последняя строка:** `exec /usr/local/bin/wal-g wal-push "$1"`.

### HTTP/2 / IPv6 issue (исполнитель diagnostic)

Из сообщений исполнителя перед cleanup:

- Wal-g на хосте работает (может подключиться к R2).
- Wal-g внутри контейнера fails — Go resolver пробует IPv6 first, hangs.
- С force IPv4 — затем HTTP/2 connection failure.

Это означает разница в network setup между хостом и Docker-контейнером. Возможные причины:
- Docker network не поддерживает IPv6.
- MTU / packet size issue с HTTP/2 через Docker bridge.
- Cloudflare R2 endpoint использует HTTP/2 только, и прокси внутри Docker ломает handshake.

### R2 bucket state

- Пустой. Ни WAL, ни backup.
- `wal-g st ls basebackups_005/` → empty.
- `wal-g st ls wal_005/` → empty.

### WAL files on disk

- 5 WAL файлов в `pg_wal/` (не накопилось много — очищается checkpoint'ом).
- Disk: 24 ГБ свободно на `/` (было 23 ранее, небольшое сжатие нормально).

### Что НЕ пострадало

- Staging API HTTP 200 ✅.
- Все 7 контейнеров healthy ✅.
- Данные БД целы (recovery automatic без data loss) ✅.
- pg_dump cron работает ✅.

## 🚨 Red flags (if any)

### 🔴 Critical: postgres crash loop

3 recovery цикла за 15 минут — **каждую archive_command попытку**. Это продолжается и сейчас. Накопится ли проблема? Вероятно нет (каждый recovery быстрый, данные в order), но это **не нормальное состояние**. Чем дольше ждать, тем больше WAL потеряется / потребуется fresh checkpoint.

**Immediate mitigation option:** ALTER SYSTEM SET archive_command = '/bin/true'; SELECT pg_reload_conf(); — это не требует restart, немедленно останавливает failing archive_command loop. Stable state до решения стратегии.

Но я сам этого **не делаю** (role boundary: не трогаю staging config). Исполнитель или Дмитрий — да.

### 🟡 Lesson candidates (добавить после closure)

- **Lesson 12:** Never trust pg_stat_archiver alone — always verify R2 bucket listing before объявлять success.
- **Lesson 13:** Container networking ≠ host networking. Для cloud storage через Docker тестировать connectivity до архитектурного commit.
- **Lesson 14:** Wrapper scripts для archive_command — обязательный тест с реальным `%p` параметром до activation archive_mode.

## 📝 Running notes

### После рапорта исполнителя — моя рекомендация

Думаю **Option A с архитектурным follow-up** — правильный путь:

1. Исполнитель сделает `ALTER SYSTEM SET archive_command = '/bin/true'; pg_reload_conf();` — останавливает crash loop.
2. Staging возвращается в стабильное состояние (archive_mode=on но no-op archive).
3. W10.2-early закрывается как **PARTIAL (blocked on container networking)**.
4. Новый hotlist item: «W10.2-early continuation — wal-g container networking fix или host-level architecture».
5. Новая сессия позже с deep-dive: исследовать HTTP/2/IPv6 block, тестировать wrangler / host-level wal-g / docker-compose network settings.

Options B и C требуют new arch decisions — лучше в чистой сессии с правильным research, не в heat-of-moment продолжении failing setup.

### Update triggers (reminder)

- Перед каждой передачей Дмитрию.
- Каждые 30-60 минут активной работы.
- После получения рапорта исполнителя.
- После принятия решения.
- Перед длительной операцией.
- При приближении к компактификации контекста.
