---
tags: [статус, прод, staging, деплой, риски]
updated: 2026-04-19
---

# Прод vs Staging — расхождения на 2026-04-19

Snapshot состояния: **prod заморожен 2026-03-17**, **staging догоняет main**.

## Версии кода

| Параметр | Прод | Staging | Local main |
|----------|------|---------|------------|
| Image build date | 2026-03-20T03:08 UTC | 2026-04-16T16:19 UTC | — |
| Image SHA | `667d1a7c93a6` | `proficrm-staging-web` | — |
| HEAD commit | ≈ `be569ad` (2026-03-17) | `b7dcb21a` (2026-04-19) | `b7dcb21a` |
| Django | **6.0.1** | **6.0.4** | — |
| Celery | **5.4.0** | **5.5.2** | — |
| channels | ❌ отсутствует | ✅ 4.2.0 | — |

**Разрыв прод → main: 333 коммита.**

## Django apps

**Прод (23 apps)**:
```
admin, auth, contenttypes, sessions, messages, staticfiles, postgres,
rest_framework, simplejwt, token_blacklist, django_filters, corsheaders, drf_spectacular,
accounts, companies, tasksapp, ui, audit, mailer, notifications, phonebridge, amocrm, policy
```

**Staging (+2 apps)**:
- `channels` (WebSocket support)
- `messenger` (весь live-chat модуль)

## Миграции

| Метрика | Прод | Staging |
|---------|-----:|--------:|
| Всего применённых | **161** | **205** |
| Разрыв | — | **+44 миграции** |

### Не применены на проде (44 миграции)

| App | Миграции | Количество |
|-----|----------|-----------|
| `accounts` | 0010 messenger_online, 0011 branch_region, 0012 regions_data, 0013 tenderist_role, 0014 remove_dup_magic_idx, 0015 user_absence | 6 |
| `companies` | 0050 companynote_note_type_meta, 0051 sms_type, 0052 task_indexes_constraints, 0053 company_dashboard_indexes, 0054 contract_type_amount_thresholds | 5 |
| `messenger` | 0001_initial … 0025_conversation_off_hours (весь модуль) | **25** |
| `phonebridge` | 0010 qr_token_hash, 0011 remove_dup_qr_idx | 2 |
| `policy` | 0003 livechat_escalation | 1 |
| `tasksapp` | 0013 uniq_recurrence_occurrence, 0014 indexes_constraints, 0015 assignee_updated_idx | 3 |
| `ui` | 0011 font_scale_widen, 0012 per_page, 0013 amoapi_client_secret_enc | 3 |

**БД-таблиц**: прод 63, staging 84 (+21, из них messenger 19).

## Объёмы данных

| Модель | Прод | Staging | Соотношение |
|--------|-----:|--------:|:-----------:|
| Company | 45 708 | 34 003 | staging 74% |
| Contact | 99 152 | 84 129 | staging 85% |
| CompanyNote | 243 585 | 225 307 | staging 92% |
| Task | 18 281 | 9 119 | staging 50% |
| ActivityEvent | **9 516 330** | 613 776 | staging 6% |
| Notification | 39 116 | 16 359 | staging 42% |
| Campaign | 1 | 11 | prod 9% |
| CompanyDeal | 0 | 0 | — (не используется) |
| User | 36 | 31 | — |
| Branch | 3 | 3 | 100% |

**Ключевое**: `ActivityEvent` на проде в 15× больше staging — миграции на audit-таблицах будут **долгими**.

## Поля моделей

### Company
- `phone_comment`, `work_timezone`, `workday_start/end`, `work_schedule`, `region`, `employees_count` — **есть на обоих** (возможно через `docker cp` миграций или совпадение схемы).
- Reverse-accessor `messenger_conversations` — только на staging.

⚠ **Опасно**: staging миграции `companies 0050-0054` формально не применены на проде, но поля уже в БД. `migrate --plan` на проде может показать конфликт — обязательно анализ до `migrate`.

### Contact, Task
- **Идентичны по полям** (18 и 21 поля соответственно).
- Индексы на `Task` (миграции `tasksapp 0013-0015`) на проде **отсутствуют** → медленнее dashboard-запросы, риск дублей в recurrence.

## ENV различия

**Только на staging**:
```
MESSENGER_ENABLED=1
```

**Требуется перед деплоем на прод**:
- `MESSENGER_ENABLED=1` в `.env.prod`
- CORS widget-origin для новых inbox
- `MESSENGER_WIDGET_STRICT_ORIGIN=True` (в проде)

## Поведение UI

- **Прод**: `/companies/<id>/` — **classic** карточка. Preview v3/a/b/c **отсутствуют**.
- **Staging**: classic + v3/a, v3/b, v3/c preview (`/companies/<id>/v3/b/` рендерится, class `.vb-hero` присутствует).

## Docker compose

| Параметр | Прод | Staging |
|----------|------|---------|
| Compose-файлы | `docker-compose.prod.yml` + `docker-compose.vds.yml` | `docker-compose.staging.yml` |
| Сервисы | db, redis, web, websocket, celery, celery-beat | + nginx (в контейнере) |
| Nginx | Хостовый `/etc/nginx/...` | В контейнере |
| Volumes | `pgdata`, `redisdata` + хостовые медиа | `pgdata_staging`, `redisdata_staging`, `media_staging`, `static_staging` |
| Сеть | `proficrm_default` | `crm_staging_network` |
| Websocket | Контейнер есть, но не используется (нет channels) | Активен (Daphne) |

## Hotfix-ы только на проде (без git)

2026-04-18: ручной `docker cp` в `proficrm-web-1`:

1. **`ui/views/company_detail.py`** — CASCADE task delete перед `company.delete()` (2 места: approve + direct). Маркер `# Hotfix 2026-04-18`.
2. **`templates/ui/company_detail.html`** — обновлённый текст модалки: «Все задачи и заметки этой компании будут удалены» + новый `onsubmit confirm()`.
3. **`templates/ui/company_list.html`** — баннер «Активен дополнительный фильтр» + ссылка «снять этот фильтр».

Все 3 **зеркалены коммитом `b7dcb21a`** → при следующем полном деплое main→prod перезапишутся на каноничную версию. Конфликтов не ожидается.

## Риски деплоя main → prod

### P0 (блокирующие)

1. **44 миграции разом** — минимум 5-10 минут на БД с 9.5M ActivityEvent:
   - `messenger 0001-0025` — создание 19 таблиц + индексов + backfill (phase1 + phase2). Данных messenger на проде нет, но ALTER/CREATE займёт время.
   - `companies 0052/0053` — индексы по Task и dashboard-запросам. **Если без `CREATE INDEX CONCURRENTLY` — ALTER TABLE lock**.
   - `tasksapp 0013 uniq_recurrence_occurrence` — UNIQUE constraint. На 18 281 задаче может найти дубли → **migrate упадёт**. Проверить заранее:
     ```python
     from tasksapp.models import Task
     from django.db.models import Count
     dup = Task.objects.values('parent_recurring_task', 'recurrence_next_generate_after').annotate(c=Count('id')).filter(c__gt=1)
     ```
2. **Добавление `messenger` + `channels` в INSTALLED_APPS** — требует `MESSENGER_ENABLED=1` в `.env.prod`. Без переменной — ImproperlyConfigured при старте.
3. **БД-схема частично опережает миграции** (`phone_comment`, `work_timezone` уже в таблице) — обязательно `migrate --plan` до `migrate`.

### P1 (важно)

4. Django 6.0.1 → 6.0.4 (patch) — обычно совместимо.
5. Celery 5.4.0 → 5.5.2 (minor) — перезапустить workers.
6. `channels` + `websocket` сервис — активировать с channels-backend в Redis.
7. Host nginx: после деплоя `nginx -t && nginx -s reload` (websocket location, CORS widget, SSE messenger).
8. CORS_ALLOWED_ORIGINS — добавить widget-origins.
9. `ActivityEvent` retention: 9.5M — любой index rebuild будет долгим.

### P2 (мониторить)

10. 1 Campaign на проде (staging 11) — схема `CampaignRecipient` совпадает (обе на mailer 0032).
11. Preview v3/a/b/c на проде **не существует** — после деплоя убедиться что нет битых ссылок.
12. Mobile app QR — на проде нет миграций phonebridge 0010-0011 (hash QR), Android-клиент на старом формате.

## Рекомендуемый порядок деплоя

```
1. Backup: pg_dump до миграций (9.5M ActivityEvent не восстановить просто)
2. Обновить .env.prod: MESSENGER_ENABLED=1, CORS widget-origin
3. git pull на /opt/proficrm/ (диф с hotfix-ами заранее sha256 сверить)
4. docker build web websocket celery celery-beat
5. python manage.py migrate --plan (ПРОЧИТАТЬ вывод до migrate!)
6. docker compose up -d --no-deps web (entrypoint запустит migrate)
7. Проверить showmigrations consistency
8. docker compose up -d websocket celery celery-beat
9. Хостовый nginx reload
10. Smoke: /companies/, /tasks/, /messenger/ (SSE), /widget/ тестовый inbox
```

## Связанное

- [[Статус прод]] — основная страница статуса production
- [[Статус staging]] — статус staging
- [[Известные проблемы]] — текущие баги
- `project_prod_state.md` (memory) — состояние deploy-готовности
