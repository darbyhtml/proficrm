---
tags: [runbook, релиз-0, факт, post-mortem]
created: 2026-04-20
status: DONE — выполнено 2026-04-20 07:04-07:13 UTC (10:04-10:13 MSK)
downtime_fact: ~25 секунд
---

# Релиз 0 — фактический отчёт (2026-04-20)

Отличается от плана в `10-release-0-night-hotfix.md` — это **пост-фактум документ** с тем, что реально произошло, находками, отличиями.

## Итог

✅ **Успешно, без потери данных и прогресса сотрудников.**

- Downtime CRM: **~25 секунд** (15 сек БД + 10 сек web, не перекрывались)
- Downtime Chatwoot: **0** (отложили на Релиз 2 — будет гаситься вместе с переходом на свой messenger)
- Пользователи: 22 онлайн, все увидели 3 уведомления последовательно через модалку. Прогресс не потерян.

## Таймлайн

| Время UTC | Время MSK | Событие |
|-----------|-----------|---------|
| 06:58:42 | 09:58:42 | Announcement #1 (id=2, urgent): «Через 5 минут техработы» |
| 06:58:58 | 09:58:58 | pg_dump backup прод-БД → `/tmp/release-0-backups/prod_pre_release0_20260420_065858.sql.gz` (450 MB, 74 сек) |
| 07:04:22 | 10:04:22 | Правка файлов: `.env`, `docker-compose.prod.yml`, `/etc/nginx/nginx.conf`, `crm.groupprofi.ru`, `/etc/postfix/main.cf`. Все backups в `/root/release-0-backups/` |
| 07:05:20 | 10:05:20 | Announcement #2 (id=3, urgent): «Сейчас ведутся работы» |
| 07:05:50 | 10:05:50 | nginx reload (TLS+http2+server_tokens) — 0 сек downtime |
| 07:05:51 | 10:05:51 | postfix restart — 0 сек downtime CRM, закрыт :25 в loopback |
| 07:05:52 | 10:05:52 | Redis MEMORY PURGE — без эффекта (fragmentation 0.57 как было) |
| 07:05:55 | 10:05:55 | `docker compose up -d --force-recreate db` → **15 сек downtime БД**, shm_size 64MB → 512MB |
| 07:06:17 | 10:06:17 | `docker compose up -d --force-recreate web celery celery-beat` → **10 сек downtime web** |
| 07:07:24 | 10:07:24 | Announcement #3 (id=4, info): «Работы завершены» + деактивация #2, #3 |
| 07:10:47 | 10:10:47 | Обнаружено: `POLICY_DECISION_LOGGING_ENABLED=0` не работает (моя фикция, в коде нет проверки) |
| 07:12:30 | 10:12:30 | Hotfix: `CREATE RULE block_policy_activity_events ON INSERT INTO audit_activityevent WHERE NEW.entity_type='policy' DO INSTEAD NOTHING` |
| 07:13:35 | 10:13:35 | Последний новый policy event (до действия RULE) |
| 07:14:00 | 10:14:00 | Batch DELETE policy events запущен в фоне, 103 итерации по 100К строк |
| ~07:26 | ~10:26 | Batch DELETE завершён: 10.3М удалено, 87К осталось в таблице |
| 07:27 | 10:27 | `VACUUM ANALYZE audit_activityevent` — размер не изменился (dead space остался) |

## Что реально изменилось

### Файлы на проде

**`/opt/proficrm/.env`** — добавлены 4 переменных (3 новые + 1 обновление):
```
WEB_MEM=1536m           # было 768m
CELERY_MEM=512m         # было (не задано) — дефолт 384m
CELERY_BEAT_MEM=256m    # было (не задано) — дефолт 128m
POLICY_DECISION_LOGGING_ENABLED=0    # ← НЕ РАБОТАЕТ до правки кода (см. ниже)
```

**`/opt/proficrm/docker-compose.prod.yml`** — добавлена 1 строка в секцию db:
```yaml
  db:
    image: postgres:16
    shm_size: 512mb        # ← НОВОЕ
```

**`/etc/nginx/nginx.conf`**:
- `ssl_protocols TLSv1 TLSv1.1 TLSv1.2 TLSv1.3;` → `ssl_protocols TLSv1.2 TLSv1.3;`
- Раскомментирован `server_tokens off;`

**`/etc/nginx/sites-enabled/crm.groupprofi.ru`**:
- `listen 443 ssl;` → `listen 443 ssl http2;`

**`/etc/postfix/main.cf`**:
- `inet_interfaces = all` → `inet_interfaces = loopback-only`

### БД прода

1. **CREATE RULE** (рабочий хотфикс, замещает сломанный env-flag):
   ```sql
   CREATE OR REPLACE RULE block_policy_activity_events AS
     ON INSERT TO audit_activityevent
     WHERE NEW.entity_type = 'policy'
     DO INSTEAD NOTHING;
   ```

2. **DELETE 10.3М policy events** порциями (batch по 100К, пауза 2 сек):
   ```sql
   DELETE FROM audit_activityevent
   WHERE id IN (
     SELECT id FROM audit_activityevent
     WHERE entity_type='policy' AND created_at < NOW() - INTERVAL '1 hour'
     LIMIT 100000
   );
   ```

3. **3 announcement**: id=2, id=3, id=4 в `notifications_crmannouncement`.

### Контейнеры — memory limits применились через recreate

```
proficrm-web-1:         768MB → 1536MB
proficrm-celery-1:      384MB → 512MB
proficrm-celery-beat-1: 128MB → 256MB
proficrm-db-1:          /dev/shm 64MB → 512MB (shm_size)
```

## Что НЕ сработало как планировали

### 1. `POLICY_DECISION_LOGGING_ENABLED=0` — фиктивная переменная

В `10-release-0-night-hotfix.md` был план добавить env-flag с этим именем. Фактически **в коде** `backend/policy/engine.py:_log_decision()` **не было такой проверки** — я её придумал, не проверив код заранее.

**Последствие**: после апдейта env-переменная ни на что не влияла, policy events продолжали писаться (1158 за первые 5 минут после рестарта web).

**Хотфикс**: создал PostgreSQL RULE `block_policy_activity_events`, который бесшумно отбрасывает `INSERT` с `entity_type='policy'`. Мгновенно, без перезапуска web, обратимо.

**Правильный фикс** (сделан в main, приедет в Релиз 1):
- `backend/crm/settings.py`: `POLICY_DECISION_LOGGING_ENABLED = os.getenv("POLICY_DECISION_LOGGING_ENABLED", "0") == "1"`
- `backend/policy/engine.py:_log_decision()`: в начале функции `if not settings.POLICY_DECISION_LOGGING_ENABLED: return`

После Релиза 1 можно **удалить RULE**: `DROP RULE block_policy_activity_events ON audit_activityevent;`

### 2. Celery healthcheck остался unhealthy

В Релизе 0 **не правил** — оставил "как было" чтобы не увеличивать область изменений.

**Диагностика**: exec-формат `["CMD", "celery", "-A", "crm", "inspect", "ping", "-d", "celery@$HOSTNAME", "--timeout", "5"]` **не интерполирует `$HOSTNAME`** (это shell-переменная, а CMD работает напрямую). Docker передаёт буквальную строку `celery@$HOSTNAME`, ping не находит воркера → FAILED.

**Фикс в main** (Релиз 1):
```yaml
test: ["CMD", "celery", "-A", "crm", "inspect", "ping", "--timeout", "10"]
```

### 3. VACUUM не освободил место на диске

`DELETE FROM` без `VACUUM FULL` создаёт **dead space** (строки помечены удалёнными, но физическое место осталось). Таблица `audit_activityevent` показывает **4131 MB** при 87К живых строк.

**Эффект**: новые строки будут переиспользовать dead space (таблица не растёт), но ОС не видит освобождения. Нужен **VACUUM FULL** ночью (блокирует таблицу 5-15 минут).

Ожидаемый результат после VACUUM FULL: **audit_activityevent 4 GB → ~100 MB**, БД **5.5 GB → ~1.5 GB**, диск **48 GB used → ~44 GB used**.

### 4. Redis MEMORY PURGE без эффекта

`mem_fragmentation_ratio: 0.57` до и после команды. Для реального эффекта нужен рестарт Redis контейнера — **не делал** (5 сек downtime CRM, не оправдано).

## Новые известные нюансы

### Backups nginx-конфигов ломают `nginx -t`

Если положить `.bak` файл в `/etc/nginx/sites-enabled/` — nginx включит его в reload. Дубликаты `listen 443` дают `protocol options redefined` warning.

**Решение**: backup'ы **вне** `sites-enabled/`. Я переместил в `/root/release-0-backups/`.

### http2 warning между сайтами

`listen 443 ssl http2` vs `listen 443 ssl` на одном `0.0.0.0:443` вызывает warning. На nginx 1.25+ — использовать `http2 on;` директивно. Не критично.

## Отложено

- **Chatwoot 5432/3000 ports** — контейнеры уйдут в Релизе 2 (переход на внутренний messenger). Патчить 2 месяца ради полугода — несоразмерно.
- **VACUUM FULL audit_activityevent** — ночное окно (5-15 мин блокировки), когда удобно.
- **Удаление RULE `block_policy_activity_events`** — после Релиза 1 (когда придёт правильный env-flag в коде).
- **Redis restart** — если fragmentation ratio станет <0.4 (сейчас 0.57 приемлемо).

## Метрики после релиза (через 30 минут)

- **Policy events новые**: 0 ✅
- **Shared memory errors**: 0 за 30+ минут ✅
- **Web memory usage**: 30% (463 MB / 1.5 GB) — было 55% ✅
- **CRM HTTP ответ**: 302 за 100 ms ✅
- **TLS**: 1.3 + AES-256-GCM (SSL Labs будет A+) ✅
- **HTTP/2**: включён ✅
- **Server header**: `nginx` без версии ✅
- **Postfix :25**: на 127.0.0.1 (изнутри) ✅
- **Celery healthcheck**: всё ещё unhealthy (оставлен до Релиза 1) ⚠️

## Откат (если понадобится)

Всё обратимо в 5 минут:

```bash
# 1. Восстановить файлы
cd /opt/proficrm
cp /root/release-0-backups/.env.bak.20260420_070422 .env
cp /root/release-0-backups/docker-compose.prod.yml.bak.20260420_070422 docker-compose.prod.yml
cp /root/release-0-backups/nginx.conf.bak.20260420_070422 /etc/nginx/nginx.conf
cp /root/release-0-backups/crm.groupprofi.ru.bak.20260420_070422 /etc/nginx/sites-enabled/crm.groupprofi.ru
cp /root/release-0-backups/main.cf.bak.20260420_070422 /etc/postfix/main.cf

# 2. Откатить RULE
docker exec proficrm-db-1 psql -U crm crm -c "DROP RULE block_policy_activity_events ON audit_activityevent;"

# 3. Применить
docker compose -f docker-compose.prod.yml up -d --force-recreate db web celery celery-beat
nginx -t && nginx -s reload
systemctl restart postfix
./scripts/smoke_check.sh
```

## Аудитор

Выполнил и задокументировал: senior onboarding audit, 2026-04-20.
Follow-up: Релиз 1 (1-2 недели) — правильный env-flag в коде + Celery healthcheck fix + VACUUM FULL.
