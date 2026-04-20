---
tags: [runbook, релиз-1, main-to-prod, messenger]
created: 2026-04-20
status: READY — dress rehearsal пройден успешно
risk: LOW (было HIGH до dress rehearsal)
expected_downtime: 5-10 минут
---

# Runbook Релиз 1 — Деплой main → prod

## Контекст: почему риск ниже, чем казалось

До Day 3 audit считалось:
- 333 коммита gap
- 44 миграции БД, в том числе тяжёлые (messenger 25 migrations + индексы на ActivityEvent 9.5M)
- Downtime 20-60 минут

**После Day 3 dress rehearsal**:
- 333 коммита в коде ✅ (требует rebuild образов)
- **Миграции БД: уже применены на проде** (`migrate --plan` → `No planned migration operations`). Все 84 таблицы, включая 19 messenger-таблиц, уже существуют.
- Pending: 2 **model drift** (не блокируют работу): `accounts.UserAbsence.id` и `messenger.Conversation.status` constraint.
- Main-код **проверен** на staging с прод-БД — работает без ошибок, healthy.
- **Downtime: 5-10 минут** (время пересборки образа + перезапуск).

## Что делает релиз

| # | Что | Effect для пользователей |
|---|-----|---------------------------|
| 1 | Обновление Python-кода (333 коммита) | v3/b карточка компании, исправление багов, новые views |
| 2 | Django 6.0.1 → 6.0.4 (patch) | Незаметно |
| 3 | Celery 5.4.0 → 5.5.2 (minor) | Незаметно (требует перезапуск workers) |
| 4 | Добавление `channels` 4.2.0 + websocket сервис | Фундамент для messenger (пользователь пока не видит) |
| 5 | `MESSENGER_ENABLED=1` в `.env.prod` | `messenger` появляется в `INSTALLED_APPS`, **но пустой** (0 conversations). Пользователь видит раздел «Мессенджер» в меню, внутри — «создать первый inbox». |
| 6 | 2 pending migrations: `accounts.0016`, `messenger.0026` | Незаметно (минорные alter) |
| 7 | Исправления из Релиза 0 (если ещё не применены): `shm_size`, memory limits, Chatwoot-порты, nginx TLS, postfix | См. `10-release-0-night-hotfix.md` |

## Что НЕ делает

- ❌ Не выключает Chatwoot. Менеджеры продолжают работать в `chat.groupprofi.ru`.
- ❌ Не удаляет 45 пустых или 343 orphan-контактов (отдельная management-команда, отложена)
- ❌ Не удаляет 525 MB мёртвых индексов (нужно ADR отдельно)
- ❌ Не фиксит 20 падающих тестов (отдельная задача)

---

## Предусловия

- [x] Dress rehearsal пройден: `docs/runbooks/99-day3-dress-rehearsal.md` (см.)
- [ ] Релиз 0 применён (security + memory). Или применяется **в одно окно** с Релизом 1.
- [ ] Backup Netangels сегодняшний — подтверждён
- [ ] Окно согласовано с заказчиком (ночью, когда менеджеры не работают)
- [ ] Менеджеры предупреждены за 24 часа
- [ ] main последнего коммита собран локально и проверен локально (`docker compose build`)
- [ ] Текущий HEAD main зафиксирован в заметках

---

## Шаг-за-шагом

### T-1 день

1. На staging (уже сделано после Day 3) — убедиться, что main работает корректно на прод-БД.
2. Прогнать `./scripts/smoke_check.sh` (адаптировав для staging URL) — всё PASS.
3. Ручной QA на staging: логин, компании, задачи, рассылка, отчёты.
4. Проверить, что `/companies/<id>/v3/b/` рендерится без ошибок на staging.
5. Если нужны 2 новые миграции для model drift — сгенерировать, закоммитить в main:
   ```
   python manage.py makemigrations accounts messenger
   git add backend/accounts/migrations/0016_* backend/messenger/migrations/0026_*
   git commit -m "Chore(Migrations): generate pending migrations for accounts + messenger"
   git push
   ```

### T+0 (начало окна, ночь)

```bash
# 0. Бэкап "на всякий случай" поверх Netangels
TS=$(date +%Y%m%d_%H%M%S)
mkdir -p /tmp/release-1-backups
docker exec proficrm-db-1 pg_dump -U crm crm --no-owner --no-acl | gzip -6 > /tmp/release-1-backups/prod_pre_release1_${TS}.sql.gz

# 1. Обновление .env.prod — добавить новые переменные
# MESSENGER_ENABLED=1
# CORS_ALLOWED_ORIGINS — добавить widget-origins (когда будем подключать виджеты)
# MESSENGER_WIDGET_STRICT_ORIGIN=True

# 2. Git pull
cd /opt/proficrm
git fetch origin main
git log --oneline HEAD..origin/main | wc -l   # покажет число новых коммитов
git diff --stat HEAD..origin/main | tail -10   # покажет, что меняется
git pull origin main

# 3. Rebuild образов (web, celery, celery-beat, websocket)
# Займёт 3-5 минут, pip install + npm + collectstatic
time docker compose -f docker-compose.prod.yml build web celery celery-beat websocket

# 4. Применить Релиз 0 docker-compose изменения, если ещё не применены (shm_size, limits)
# См. docs/runbooks/10-release-0-night-hotfix.md Шаг 3

# 5. Поднять БД с новым shm_size (если не сделано в Релизе 0)
docker compose -f docker-compose.prod.yml up -d --no-deps --force-recreate db
# Ждать 30 секунд — DB healthy
sleep 30 && docker compose -f docker-compose.prod.yml ps db

# 6. Migrate — должен быть "No migrations to apply" (если pending migrations добавлены — применит их)
docker compose -f docker-compose.prod.yml run --rm web python manage.py migrate --noinput
# Ожидаемый вывод: "No migrations to apply" ИЛИ 2 строки для accounts/0016 + messenger/0026 (секунды)

# 7. Collectstatic
docker compose -f docker-compose.prod.yml run --rm web python manage.py collectstatic --noinput

# 8. Rolling restart сервисов
docker compose -f docker-compose.prod.yml up -d web celery celery-beat
# websocket контейнер — если он в docker-compose.prod.yml; если нет — надо добавить
docker compose -f docker-compose.prod.yml up -d websocket 2>/dev/null || echo "websocket не в compose — добавить"

# 9. Nginx reload (если менялось)
nginx -t && nginx -s reload

# 10. Smoke-check
./scripts/smoke_check.sh
```

### T+10 мин — QA

1. Открыть `https://crm.groupprofi.ru/`
2. Залогиниться. Дашборд грузится без 500.
3. Открыть `/companies/` — список видим, фильтры работают.
4. Открыть карточку любой компании по classic URL: `/companies/<id>/`. Отображается.
5. Открыть **v3/b**: `/companies/<id>/v3/b/`. Должен корректно отрендериться (новый дизайн).
6. Открыть **messenger**: `/messenger/`. Ожидаем: либо «у вас нет inbox'ов» + кнопка «создать», либо пустой список.
7. Создать задачу. Открыть. Изменить due_at. Завершить.
8. Зайти в настройки > Рассылки. Одна кампания должна быть видна.
9. Проверить админку `/admin/`.

### T+30 мин — наблюдение

Первые 30 минут **не отходим от компьютера**.

```bash
# В отдельных терминалах
docker compose -f docker-compose.prod.yml logs -f --tail=100 web
docker compose -f docker-compose.prod.yml logs -f --tail=100 celery

# PostgreSQL — смотрим slow queries
docker exec proficrm-db-1 psql -U crm crm -c "SELECT query, calls, mean_exec_time FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 10"  # если включён pg_stat_statements

# Метрики памяти/CPU
watch -n 5 'docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}"'
```

Если в любой из первых 30 минут видны:
- 5xx больше нуля → **ОТКАТ**
- OOM kill контейнеров → **ОТКАТ**
- PostgreSQL errors в `audit_errorlog` кроме `could not resize shared memory segment` → **ОТКАТ** (эта ошибка не должна появляться при `shm_size=512mb`)
- Celery задачи накапливаются в Redis queue → **ОТКАТ**

---

## План отката

### Быстрый откат (5 минут)

```bash
cd /opt/proficrm
git log --oneline -5   # посмотреть HEAD до pull и до него
git reset --hard <HEAD_PRE_PULL>
docker compose -f docker-compose.prod.yml build web celery celery-beat  # обратно на старый код
docker compose -f docker-compose.prod.yml up -d web celery celery-beat
./scripts/smoke_check.sh
```

Это возвращает код, но **не БД**. БД не трогали (миграций не было).

### Полный откат (БД из бэкапа)

Только если БД **повреждена** или применилась миграция, которую надо отменить:
```bash
# Из локального бэкапа:
gunzip -c /tmp/release-1-backups/prod_pre_release1_*.sql.gz | docker exec -i proficrm-db-1 psql -U crm crm
# Или из Netangels (10-15 минут).
```

### Откат `MESSENGER_ENABLED`

Если включение messenger ломает что-то неожиданное:
```bash
# В .env.prod:
MESSENGER_ENABLED=0
docker compose -f docker-compose.prod.yml up -d --force-recreate web celery
```
Messenger становится невидимым, всё остальное работает.

---

## Что делать ПОСЛЕ Релиза 1 (в роадмап)

### Неделя 1 после релиза
- Fix 20 падающих тестов (`tests_recurrence`, `test_reports`)
- Мониторинг — Sentry free tier подключить
- UptimeRobot — 5 мониторов
- GitHub Actions — настроить автодеплой на staging по push в main

### Неделя 2-3
- Пакетное удаление 343 orphan contacts (management-команда)
- ADR про 525 MB мёртвых индексов — удалять или оставить
- Обновление `docs/wiki/04-Статус/Прод-vs-Staging.md` (сильно устарел)

### Неделя 4+
- Релиз 2 планирование: редизайн остальных страниц, живой messenger, переезд с Chatwoot, mailer полировка, Android

---

## Аудитор

Подготовлено: 2026-04-20, Day 3.
Dress rehearsal: **PASSED** (staging web работает на прод-снимке БД, 0 ошибок).
Статус: **READY**. Ждёт подтверждения окна деплоя.
