---
tags: [runbook, прод, аудит, day2, индексы, nginx, postfix]
created: 2026-04-20
author: onboarding-audit
mode: read-only
---

# Снимок прод-инфраструктуры — Day 2, 2026-04-20

Продолжение `00-prod-snapshot-2026-04-20.md`. Фокус: Postfix, nginx, PostgreSQL-индексы, целостность БД, тесты.

## TL;DR

| Область | Находка | Действие |
|---------|---------|---------:|
| **Postfix** | inet_interfaces=all → слушает 0.0.0.0:25, но relay закрыт (`defer_unauth_destination`). Безопасно, но некрасиво. | Закрыть в loopback-only (Релиз 0) |
| **nginx** | TLSv1.0 и TLSv1.1 **включены** (deprecated) | Отключить (Релиз 0, 30 сек) |
| **nginx** | Нет `server_tokens off`, brotli, HTTP/2 | Улучшения (Релиз 1) |
| **PostgreSQL** | **525 MB мёртвых индексов** — никогда не использовались | Удалить после аудита кода (Релиз 1) |
| **PostgreSQL** | pg_stat **никогда не сбрасывался** → мёртвые индексы гарантированно мёртвые | — |
| **БД целостность** | 343 contacts без company, 45 tasks без company, 95 events без actor | Документ + обсудить с заказчиком |
| **Тесты** | **43 test-файла** в backend/ — прошлый разработчик писал тесты. Прогнать на staging-копии. | Day 3 |
| **Бэкапы** | Локально `/var/backups/` только системные (apt/dpkg). БД-бэкапы — только Netangels. | Проверить retention у провайдера |

---

## 1. Postfix на порту 25

### Конфигурация (postconf -n)
```
inet_interfaces           = all                   ← слушает 0.0.0.0
mynetworks                = 127.0.0.0/8 [::1]/128
relayhost                 = (пусто)
smtpd_relay_restrictions  = permit_mynetworks permit_sasl_authenticated defer_unauth_destination
```

### Вывод
- **Open relay невозможен**. Чужой адрес без SASL-аутентификации → `defer_unauth_destination` → 450.
- **Спам нам в лицо тоже невозможен**: только mynetworks=localhost может принимать почту без аутентификации.
- **Зачем торчит в интернет?** Дефолт Debian при установке `mailutils`/`sendmail-compat`. Не было специальной настройки.
- **Логи за 24 часа**: `No entries` — **никто не шлёт через него**. Даже локально.
- **Приложение использует его?** По коду `mailer/` и `.env` — **нет**. Используется внешний `smtp.bz` (видно в логах Celery: `mailer.tasks.sync_smtp_bz_*`). Postfix **не нужен вообще**.

### Рекомендация
В Релиз 1 добавить в `/etc/postfix/main.cf`:
```
inet_interfaces = loopback-only
```
Затем `systemctl restart postfix`. Ничего не сломается — им никто не пользуется. Порт 25 исчезнет из публичного listing.

Либо ещё лучше: `apt purge postfix mailutils bsd-mailx` — снести совсем. Освободит пару десятков MB.

---

## 2. Nginx — полный конфиг прода `crm.groupprofi.ru`

### Что хорошо
- ✅ Кастомные 500/502/503/504 error pages
- ✅ `/static/` с `expires 30d` + immutable (оптимальная кэш-стратегия)
- ✅ `/media/` с `expires 7d`
- ✅ Отдельный location `/settings/amocrm/migrate/` с `proxy_read_timeout 300s` (под долгие импорты)
- ✅ `proxy_intercept_errors on` — unified error-pages для upstream-ошибок
- ✅ `client_max_body_size 100m` — щедро, для вложений и импортов
- ✅ Certbot autorenewal работает (`certbot.timer` active, next run 09:07 UTC)
- ✅ Последний renewal — 2026-04-19 19:34 UTC (вчера, корректно)

### Что улучшить (Релиз 1)
| Улучшение | Зачем |
|-----------|-------|
| `listen 443 ssl http2;` | HTTP/2 — multiplexing, ~15-30% быстрее на multi-request страницах |
| `server_tokens off;` в nginx.conf | Скрыть версию (сейчас в `Server: nginx/1.24.0`) |
| `ssl_protocols TLSv1.2 TLSv1.3;` (убрать TLSv1 и TLSv1.1) | Deprecated, SSL Labs даст B вместо A |
| `brotli on; brotli_comp_level 4;` | 15-25% лучше сжатие для JSON/HTML vs gzip |
| Rate-limit zone для `/api/token/*` и `/login/` | Защита от брутфорса на nginx-уровне (сейчас только Django middleware) |
| `proxy_set_header X-Forwarded-Host $host;` | Django правильно строит redirect URLs |
| `proxy_set_header X-Real-IP $remote_addr;` | Честный IP в логах Django (сейчас через X-Forwarded-For — работает, но X-Real-IP читается приоритетнее) |

### TLS — глобально в `nginx.conf`
```
ssl_protocols TLSv1 TLSv1.1 TLSv1.2 TLSv1.3;  ← TLSv1 и TLSv1.1 — DEPRECATED
```
**Фикс в Релизе 0**: меняем на `TLSv1.2 TLSv1.3`, `nginx -t && nginx -s reload`. 30 секунд без даунтайма.

### worker_processes / worker_connections
- `worker_processes auto` ✅ (=4 ядра)
- `worker_connections 768` — скромно. На 8GB RAM можно 2048. При 50 пользователях хватает, но при реклам-акции или спам-боте упрёмся.

---

## 3. PostgreSQL — мёртвые индексы (525 MB)

### Статистика актуальна
```
stats_reset: NULL  ← pg_stat_* никогда не сбрасывался
```
Это значит: idx_scan=0 — гарантированно никогда не использовался за всё время жизни БД. Не случайный snapshot за день.

### Топ мёртвых индексов

| Таблица | Индекс | Размер | Тип | Комментарий |
|---------|--------|-------:|-----|-------------|
| companies_companysearchindex | cmp_si_plain_trgm_idx | **207 MB** | GIN trigram | FTS |
| companies_companysearchindex | cmp_si_vd_gin_idx | 101 MB | GIN | FTS |
| audit_activityevent | ..._entity_id_3ce1a0f3 | 91 MB | btree | не используется — все запросы идут по `company_id`+`created_at` |
| companies_companysearchindex | cmp_si_digits_trgm_idx | 22 MB | GIN trigram | FTS (цифры) |
| companies_companysearchindex | cmp_si_vb_gin_idx | 21 MB | GIN | FTS |
| companies_companysearchindex | cmp_si_vc_gin_idx | 17 MB | GIN | FTS |
| companies_company | cmp_email_trgm_gin_idx | 4.3 MB | GIN | |
| companies_company | cmp_inn_trgm_gin_idx | 2.7 MB | GIN | |
| companies_company | cmp_phone_trgm_gin_idx | 2.4 MB | GIN | |
| + ещё 11 индексов | | — | | |

**Итого мёртво: 525 MB.**

### Ключевой вопрос: trigram-индексы для FTS **точно** не используются?

`companies_companysearchindex` — это материализованный search-индекс (не тот, что pg_index). Там хранятся `plain` (название), `va/vb/vc/vd` (векторы полей разного веса), `digits` (телефоны/ИНН). 5 GIN-индексов для быстрого поиска.

Если ни один из них не используется — значит **поиск по компаниям не работает** через FTS. Либо:
- Код использует `ILIKE %term%` (медленно, но работает)
- Код использует другую таблицу
- Фича «умный поиск» ещё не запущена

**Действие для Day 3**: прочитать `companies/search.py`, `ui/views/company_list.py`, понять, как именно работает поиск. ДО удаления этих индексов — никаких DROP.

### companies_company: 34 индекса

Это много. Подозрительно. На day 3 — `audit_company_indexes.sql`: найти:
- Дубли (два индекса по одному столбцу с одним типом)
- Индексы, «перекрытые» составными (если есть btree(a) и btree(a,b) — btree(a) не нужен)
- Индексы по boolean-полям с очень низкой селективностью (`is_deleted=true` — 99% одного значения)

---

## 4. Целостность БД: сироты

```
tasks_no_company    :  45  ← manual-created или после cascade-delete
tasks_no_assignee   :   0
notes_no_company    :   0  ← FK NOT NULL? Проверить схему — иначе это "неожиданно здорово"
contacts_no_company : 343  ← 0.35% от 99152
events_no_actor     :  95  ← системные (backfill, migrations)
```

### 45 tasks без company
Из memory-заметок сессии знаю: это manual-created задачи, не мусор. Пользователь уже видел и решил не чистить.

### 343 contacts без company — требует решения

Из 99 152 контактов 343 «свободных». Возможные причины:
1. Была CASCADE-delete компании, контакт не удалился (SET_NULL)
2. Ручной ввод контакта без привязки (функциональность была?)
3. Импорт из AmoCRM — в AmoCRM тоже бывают «свободные» контакты

**Куда их показывать в UI?** Сейчас карточка компании — единственный путь к контактам. 343 контакта **невидимы для пользователя**. Либо:
- Страница «Контакты без компании» в `/contacts/?no_company=1`
- Пакетное привязывание к компаниям
- Удалить (с подтверждением)

**Действие**: обсудить с заказчиком в следующую итерацию UI.

### 95 events без actor_id
Системные события (`backfill`, автоматические миграции). ОК.

### Проверка FK
Нужно посмотреть `\d tasksapp_task` в части `Foreign-key constraints` с ON DELETE. Видно по схеме:
- `audit_activityevent.actor_id_fk_accounts_user_id` → DEFERRABLE INITIALLY DEFERRED (значит блокирует удаление user'а **в конце транзакции**)

Это **важная деталь**: если попробовать удалить пользователя с 10k ActivityEvent — транзакция будет долго катиться (DEFERRABLE проверяется в коммите). Возможно, Django таймаутит. Надо проверить админку, насколько сейчас легко удалить user.

---

## 5. Тесты Django — 43 файла в `backend/`

Прошлый разработчик **писал тесты**. Это переворачивает оценку проекта ещё сильнее.

**Действие на Day 3**: запустить их на staging:
```
docker exec crm_staging_web python manage.py test --verbosity=2
```

Замерить:
- Сколько pass / fail / skip
- Время прогона
- Покрытие (если установлен `coverage.py`)

Если pass rate > 90% — у нас **готовая test-суита для dress rehearsal**. Это огромный плюс.

---

## 6. Бэкапы — пересмотр картины

В `/var/backups/` — только системные (apt, dpkg, alternatives). Никаких `*.sql.gz`.

**Бэкапы БД — только у Netangels**. Заказчик подтвердил: «1 бэкап в день, тестировали восстановление, надёжно». Принимаю.

**Вопрос для Netangels (через заказчика)**: сколько дней retention? Нужен **хотя бы недельный** (для отката миграции, которую заметили через 3 дня).

### Скрипт `restore_prod_db_to_staging.sh`

Прочитал, логика чистая:
1. Берёт последний `.sql.gz` из `/opt/proficrm/backups/` (**на проде**, значит сначала `backup_postgres.sh` должен отработать)
2. Останавливает `web` и `nginx` staging
3. Завершает сессии staging БД
4. DROP SCHEMA public CASCADE
5. Restore с подменой owner `crm` → `crm_staging`

**Проблема**: `backup_postgres.sh` нужно запускать **на проде**, что сейчас заблокировано хуком. Альтернативы:
- Скачать бэкап через Netangels веб-интерфейс, положить локально, указать путь
- Сделать бэкап через `docker exec proficrm-db-1 pg_dump` **без** обращения к `/opt/proficrm/` — результат сразу в `>` перенаправить в локальный путь `/tmp/`. Это **чтение** БД прода (не модификация), но требует явного разрешения от заказчика.

**Действие**: спросить заказчика в 17:30 — можно ли сделать snapshot прод-БД в `/tmp/prod_dump_YYYYMMDD.sql.gz` для dress rehearsal? Это чтение, не запись на прод. Отличается от «деплоя» принципиально.

---

## 7. Обновлённый checklist Релиз 0 (ночной hotfix)

**Длительность**: ~15-20 минут downtime на CRM + закрытие Chatwoot portов.

| # | Действие | Где | Downtime |
|---|---------|-----|---------:|
| 1 | Backup БД прода через Netangels | перед началом | 0 |
| 2 | `docker-compose.prod.yml`: Chatwoot postgres mapping → `127.0.0.1:5432:5432` | prod | 0 |
| 3 | Chatwoot postgres password → длинный random | prod | 30 сек |
| 4 | `docker-compose.prod.yml`: Chatwoot rails mapping → `127.0.0.1:3000:3000` (и nginx proxy) | prod | 30 сек |
| 5 | PostgreSQL `shm_size: 512mb` | prod db | 15 сек |
| 6 | web memory limit 768 → 1536 MB | prod | 15 сек (rolling) |
| 7 | celery limit 384 → 512, beat 128 → 256 MB | prod | 30 сек |
| 8 | Celery healthcheck: перейти на `celery status` или добавить `-b ${REDIS_URL}` | prod | 0 |
| 9 | nginx: `ssl_protocols TLSv1.2 TLSv1.3` (убрать v1/v1.1) | host | 0 (reload) |
| 10 | `inet_interfaces = loopback-only` в postfix + restart | host | 0 |
| 11 | `smoke_check.sh` прогон | host | 0 |

### Откат
Все изменения обратимы через `git revert` коммита конфига + `docker compose up -d --no-deps <service>`. Каждое изменение — **отдельным коммитом в main** на staging, потом воспроизведём на проде.

---

## 8. Готовность к Релиз 1 — следующие шаги

Блокеры:
- [ ] День 3: прогнать `python manage.py test` на staging
- [ ] День 3: разобраться с FTS-индексами — они legacy или нужны?
- [ ] День 3-4: dress rehearsal 44 миграций на staging-копии
- [ ] День 4: замер времени каждой миграции
- [ ] День 5: Runbook Релиз 1 + план отката

Оценка Релиз 1: **10-14 дней** от момента разрешения dress rehearsal.

---

## 9. Вопросы к заказчику (добавлено за Day 2)

| # | Вопрос |
|---|-------|
| 1 | **Netangels retention бэкапов** — сколько дней хранится? (нужен ≥7 для отката миграций) |
| 2 | **Snapshot прод-БД в /tmp/** через `docker exec pg_dump` — разрешаете? (только чтение, ничего не пишется на прод) |
| 3 | **343 «свободных» контакта** без company_id — что с ними делать в UI? Показывать отдельной страницей? Пакетно привязывать? Удалять? |
| 4 | **Chatwoot → messenger переход** — когда планируем? Это влияет на то, внедрять ли мост данных (бекфилл переписок из Chatwoot в messenger), или начать с чистого листа. |

---

## Аудитор

Выполнил: Senior onboarding session 2026-04-20, Day 2 (~40 минут read-only).
Режим: только чтение (`psql SELECT`, `cat`, `ls` без `/opt/proficrm/`).
Следующий документ: `02-prod-snapshot-day3.md` — тесты + FTS-анализ + dress rehearsal plan.
