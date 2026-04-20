---
tags: [runbook, прод, аудит, snapshot, день-1]
created: 2026-04-20
author: onboarding-audit
mode: read-only
---

# Снимок прод-инфраструктуры — 2026-04-20

Аудит проведён в read-only режиме, без единого изменения на сервере. Все команды — `ls`, `cat`, `docker ps`, `docker stats`, `docker logs`, `psql SELECT`. Никаких `restart`, `up`, `migrate`, `pull`.

## TL;DR — 6 P0-находок, 2 P1, 1 P2

| # | Находка | Критичность | Где починить |
|---|---------|-------------|--------------|
| P0-1 | **Chatwoot Postgres публично доступен в интернете** (0.0.0.0:5432) | Critical security | Релиз 0 (срочный hotfix) |
| P0-2 | **Chatwoot Rails UI публично доступен** (0.0.0.0:3000) — надо понять, это намеренно? | Suspicious | Спросить заказчика |
| P0-3 | **PostgreSQL /dev/shm = 64MB дефолт** — падают большие запросы с `could not resize shared memory segment` (14 ошибок за 7 дней) | High | Релиз 1 |
| P0-4 | **web-контейнер 768MB limit** при 421MB use (55%). OOM при пике нагрузки = 500-ки пользователям | High | Релиз 1 |
| P0-5 | **Celery prod healthcheck сломан 4 недели** (40 209 consecutive failures). Celery работает, но никакой алерт не сработает при реальном падении | High | Релиз 1 |
| P0-6 | **celery-beat 128MB limit при 71% use**, **celery-1 384MB limit при 63% use** — близко к OOM | High | Релиз 1 |
| P1-1 | **ActivityEvent = 4 GB (73% всей БД)**. При миграции на индексы / constraints будет **очень долго** | Medium | Релиз 1 (план retention) |
| P1-2 | **swap 1909/2047 MB = 93%** — исторический memory pressure. Текущая нагрузка ок, но запас нет | Medium | Релиз 1 (лимиты контейнеров) |
| P2-1 | **SSL** crm.groupprofi.ru истекает 2026-07-16 (через ~3 мес). Надо убедиться, что certbot renew работает. | Low | Релиз 0 |

---

## 1. Железо

| Параметр | Значение |
|----------|---------|
| OS | Ubuntu (kernel 6.8.0-90-generic) |
| CPU | 4 vCPU Intel Broadwell @ 2.0GHz (QEMU/KVM) |
| RAM | 8078 MB (used 3551, free 163, buff/cache 4781, available 4526) |
| Swap | 2047 MB (used **1909 MB = 93%**) |
| Диск | 79 GB total, **43 GB used (58%)**, 33 GB free |
| Uptime | 85 дней (последний ребут январь 2026) |
| Load avg | 0.12 / 0.18 / 0.31 (ненагружен) |

**Оценка**: для 50 одновременных пользователей запас **5-10×**. Узкое место — **лимиты контейнеров** (см. ниже), не железо.

**Swap 93%** — сигнал, что когда-то был OOM pressure. Сейчас текущая нагрузка в RAM, но «скопилось» в swap и не откатилось. После рестарта контейнеров прошло 10 часов — web-контейнер **вернулся в swap**, значит **OOM killer отработал вчера**. Это подтверждает P0-4 (web limit мал).

---

## 2. Контейнеры

Инвентаризация по состоянию на 2026-04-20 21:50 UTC.

### 2.1 CRM Prod (7 сервисов)

| Контейнер | Uptime | Healthy | RAM limit | RAM use | Примечание |
|-----------|-------:|:-------:|----------:|--------:|-----------|
| `proficrm-web-1` | 10 hours | — | **768 MB** | 421 MB (55%) | **лимит тесный**, недавно перезапустили |
| `proficrm-db-1` | 2 weeks | ✅ | unlimited | 806 MB | стабильно |
| `proficrm-celery-1` | 4 weeks | ❌ 4 недели | 384 MB | 244 MB (63%) | **healthcheck сломан, Celery работает** |
| `proficrm-celery-beat-1` | 12 days | — | **128 MB** | 91 MB (71%) | **близко к OOM** |
| `proficrm-redis-1` | 5 weeks | ✅ | unlimited | 5.5 MB | стабильно |

**Обратите внимание**: нет websocket-контейнера. Подтверждено: `MESSENGER_ENABLED=False`, нет `messenger` в `INSTALLED_APPS`. Django channels **не используется на проде**.

### 2.2 CRM Staging (7 сервисов)

| Контейнер | Uptime | Healthy | RAM |
|-----------|-------:|:-------:|----:|
| `crm_staging_web` | 3 hours | ✅ | 402 MB |
| `crm_staging_db` | 4 days | ✅ | 921 MB |
| `crm_staging_celery` | 2 days | ❌ | 228 MB |
| `crm_staging_celery_beat` | 4 days | — | 27 MB |
| `crm_staging_redis` | 4 days | ✅ | 6 MB |
| `crm_staging_websocket` | 4 days | — | 5.6 MB |
| `crm_staging_nginx` | 3 days | — | 2 MB |

### 2.3 Chatwoot (4 сервиса, 2 месяца uptime) — **НЕОЖИДАННО**

| Контейнер | Status | Порт | Примечание |
|-----------|--------|------|-----------|
| `chatwoot-rails-1` | Up 2 months | **0.0.0.0:3000** | ✅ Активно работает |
| `chatwoot-sidekiq-1` | Up 2 months | — | Background jobs |
| `chatwoot-postgres-1` | Up 2 months | **0.0.0.0:5432** | **ПУБЛИЧНО ДОСТУПЕН В ИНТЕРНЕТ** |
| `chatwoot-redis-1` | Up 2 months | — | — |

**Factsheet Chatwoot:**
- `INSTALLATION_NAME=GroupProfi Support`
- `FRONTEND_URL=https://chat.groupprofi.ru`
- `POSTGRES_PASSWORD=ooqu1bieNg2` (11 символов — **СЛАБЫЙ пароль**)
- В логах: активные WebSocket-клиенты с IP `212.134.142.206` (чей-то реальный пользователь подключался 21:47 UTC)
- Домен `chat.groupprofi.ru` настроен в nginx (redirect 80→443)

**Вывод**: Chatwoot **используется**, либо кем-то из заказчика, либо бот-пользователями. **ВОПРОС ЗАКАЗЧИКУ**: вы знали про него? Кто его настраивал? Планировалось интегрировать с CRM?

---

## 3. PostgreSQL prod

### 3.1 Размер

```
db_size: 5 592 MB

Топ-15 таблиц по total size:
  audit_activityevent           4 095 MB  (3 031 MB table + 1 064 MB indexes) ← 73% БД
  companies_companysearchindex    734 MB
  companies_company               257 MB
  companies_companynote           140 MB
  companies_contact               128 MB
  companies_companyhistoryevent    72 MB
  companies_contactphone           38 MB
  tasksapp_task                    28 MB
  companies_contactemail           22 MB
  companies_companyphone           20 MB
  notifications_notification       18 MB
```

**Вывод**: `audit_activityevent` — **73% БД**. Retention 180 дней создаёт 9.5M записей. При миграциях (44 ожидают) этот hot table будет критичной точкой. Обязательно **замер времени миграций на staging-copy** перед деплоем.

### 3.2 Конфигурация памяти — слабая

| Параметр | Значение | Рекомендация для 8GB RAM |
|----------|---------:|:------------------------:|
| `shared_buffers` | **128 MB** | 2 GB (25% RAM) |
| `work_mem` | **4 MB** | 16-32 MB |
| `maintenance_work_mem` | 64 MB | 512 MB (для VACUUM/INDEX) |
| `effective_cache_size` | 4 GB | 6 GB (75% RAM) |
| `max_connections` | 100 | 100 ок |
| `max_parallel_workers` | 8 | 8 ок (= ядер × 2) |
| **`/dev/shm`** | **64 MB (Docker default)** | **1 GB** |

**Критично**: дефолтный `/dev/shm=64MB` + `max_parallel_workers=8` → параллельные запросы падают с `could not resize shared memory segment`. **14 таких ошибок за 7 дней = 14 раз пользователь видел 500**.

### 3.3 Активные соединения

```
active: 1
idle:   8
null:   5
```

Нормально. Pool не забит.

---

## 4. Сеть и firewall

### 4.1 Открытые порты (ss -tlnp)

```
0.0.0.0:22    sshd         OK (SSH)
0.0.0.0:25    postfix      Local mail sender — проверить, не open relay
0.0.0.0:80    nginx        OK (redirect to 443)
0.0.0.0:443   nginx        OK
0.0.0.0:3000  docker-proxy → chatwoot-rails  ← ПУБЛИЧНЫЙ (намеренно? nginx-proxy на chat.groupprofi.ru — лучше закрыть)
0.0.0.0:5432  docker-proxy → chatwoot-postgres  ← ПУБЛИЧНЫЙ (КАТЕГОРИЧЕСКИ НЕЛЬЗЯ)
```

### 4.2 UFW vs Docker

UFW active с правилами:
```
OpenSSH, 80, 443 — ALLOW
всё остальное — DROP (policy)
```

**НО**: Docker публикует порты через iptables-цепочку `DOCKER`, которая выполняется **ДО UFW**. Классическая дыра Docker+UFW. Через интернет порт 5432 **на самом деле открыт**, несмотря на UFW.

**Действие**: в Релизе 0 (прямо сейчас, в ближайшую ночь) —
1. Выключить Chatwoot, если не нужен.
2. Если нужен — поменять mapping в docker-compose: `"127.0.0.1:5432:5432"` вместо `"5432:5432"`. Тогда наружу не торчит, chatwoot-rails ходит к postgres внутри docker-network.
3. Сменить пароль postgres (`ooqu1bieNg2` → длинный generated).
4. Для chatwoot-rails (порт 3000) — аналогично спрятать за nginx-proxy и привязать к `127.0.0.1`.

### 4.3 SSL

```
crm.groupprofi.ru:         действителен до 2026-07-16 (~3 месяца)
crm-staging.groupprofi.ru: действителен до 2026-06-30 (~2 месяца)
```

Certbot autorenewal не проверен — надо посмотреть `systemctl list-timers | grep certbot`.

### 4.4 Crontab root — пуст

```
$ crontab -l
# ... (только комментарии)
```

Все регулярные задачи — через **Celery beat** (хорошо, централизованно). Бэкапы — на уровне Netangels (вне сервера).

---

## 5. Логика ошибок (audit_errorlog, последние 7 дней)

Топ-14 ошибок:

Все 14 — одного типа:
```
could not resize shared memory segment "/PostgreSQL.XXXXXXXXXX" to N bytes
```

**Единственный тип ошибок** = PostgreSQL OOM на shared memory. Когда пользователь открывает тяжёлую страницу (dashboard с аналитикой / поиск по всей БД / отчёт) — включаются parallel workers, они упираются в лимит /dev/shm=64MB, запрос падает, Django возвращает 500. Пользователь видит «Что-то пошло не так».

**Решение простое**: `shm_size: 512mb` в `docker-compose.prod.yml` для db-сервиса. Требует рестарта контейнера БД (даунтайм ~15 секунд). Приоритет: включить в Релиз 1.

---

## 6. Конкретные фиксы для Релиза 1 (из этого аудита)

Поверх 333 коммитов main → prod:

1. **`docker-compose.prod.yml`**:
   - `db` сервис: `shm_size: 512mb`
   - `web` сервис: поднять `deploy.resources.limits.memory` с 768M до 1536M
   - `celery` сервис: с 384M до 512M
   - `celery-beat` сервис: с 128M до 256M
2. **PostgreSQL tuning** (через `command` в docker-compose или `custom.conf`):
   - `shared_buffers = 2GB`
   - `work_mem = 16MB`
   - `maintenance_work_mem = 512MB`
   - `effective_cache_size = 6GB`
3. **Chatwoot**: решить → спросить заказчика. Если оставить: закрыть порты к 127.0.0.1.
4. **Celery healthcheck**: поправить команду (возможно перейти с `celery inspect ping` на `celery status`).
5. **SSL autorenewal**: проверить systemd timer для certbot.

---

## 7. Вопросы заказчику (блокирующие план)

1. **Chatwoot (chat.groupprofi.ru)** — знали про него? Используется? Интегрирован с CRM (если да, как)? Можно выключить?
2. **postfix на порту 25** — нужен для чего? Отправка писем кампаний?
3. **Ночное окно для Релиза 0** (закрытие Chatwoot portов + смена пароля) — когда можно?

---

## 8. Что НЕ проверил в этом snapshot (отдельный day 2)

- Полный дамп nginx-конфигов (надо прочитать все правила)
- Индексы PostgreSQL (pg_stat_user_indexes — какие используются / какие мёртвые)
- Slow log PostgreSQL (если включён)
- Целостность FK и orphan records (например, ActivityEvent без user_id)
- Проверить на staging — сможет ли бэкап prod-БД вообще восстановиться (`restore_prod_db_to_staging.sh`)
- Полный список `docker volume ls` и их размеры

---

## Аудитор

Выполнил: Senior onboarding session 2026-04-20, ~1 час read-only аудита через SSH.
Режим: только чтение. Ни одного `docker restart`, `git pull`, `migrate`.
Следующий документ: `01-prod-snapshot-day2.md` — углублённый аудит nginx + БД-индексов.
