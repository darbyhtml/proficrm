---
tags: [статус, прод, staging, деплой, риски]
updated: 2026-04-20
audit: Day 3 extended onboarding
---

# Прод vs Staging — расхождения на 2026-04-20

**Документ заменяет версию от 2026-04-19**, которая содержала ошибочные данные про миграции и таблицы.

## TL;DR — кардинальный пересмотр

**Было в версии 2026-04-19**: «прод заморожен, 333 коммита gap, 44 миграции не применены, messenger-таблиц нет».

**Реально (по dress rehearsal 2026-04-20)**: код прода отстаёт от main на 333 коммита, **но БД уже синхронизирована** — все 44 миграции применены, все 19 messenger-таблиц созданы (пустые). Релиз main→prod становится **простой пересборкой Docker-образа**, не рискованной миграцией.

## Версии кода

| Параметр | Прод | Staging | Local main |
|----------|------|---------|------------|
| HEAD commit | ≈ `be569ad` (2026-03-17) | `b7dcb21a` (2026-04-19) | `b7dcb21a` |
| Django | 6.0.1 | 6.0.4 | 6.0.4 |
| Celery | 5.4.0 | 5.5.2 | 5.5.2 |
| channels | ❌ отсутствует | ✅ 4.2.0 | ✅ 4.2.0 |

**Разрыв прод → main: 333 коммита по коду. НО БД уже соответствует main.**

## БД — состояние на 2026-04-20

| Метрика | Прод (из snapshot'а) | Staging | Комментарий |
|---------|---------------------:|--------:|-------------|
| Размер БД | **5 592 MB** | 4 984 MB (после restore) | staging ≈ копия прода |
| Всего таблиц | **84** | 84 | совпадают |
| Всего миграций применено | **205** | 205 | совпадают |
| Messenger таблицы | **19 (все есть)** | 19 | данных 0 |

### Pending migrations (реально отстают)

| App | Миграция | Тип |
|-----|----------|-----|
| `accounts` | 0016_alter_userabsence_id | `Alter field id on userabsence` (минор) |
| `messenger` | 0026_remove_conversation_conversation_valid_status_and_more | alter check constraint (минор) |

**ВАЖНО**: обе миграции — это **model drift** (модели в коде опережают БД), но не **блокируют работу**. Main-код работает на текущей схеме прода без ошибок (проверено dress rehearsal'ом).

## Объёмы данных (прод)

| Модель | Прод | Staging (до restore) | Соотношение |
|--------|-----:|---------------------:|:-----------:|
| Company | 45 709 | 34 003 | staging 74% |
| Contact | 99 152 | 84 129 | staging 85% |
| CompanyNote | 243 585 | 225 307 | staging 92% |
| Task | 18 281 | 9 119 | staging 50% |
| **ActivityEvent** | **9 516 330** | 613 776 | staging 6% |
| Notification | 39 116 | 16 359 | staging 42% |
| **Messenger Conversation** | **0** | 0 | — (таблицы есть, но не используются) |
| **Messenger Message** | **0** | 0 | — |

**Ключевое**: прод-схема **полностью содержит** messenger-модуль, но **ни одной конверсации не создано**. Messenger «спит» с момента деплоя миграций.

## ENV различия

**Только на staging**:
```
MESSENGER_ENABLED=1
```

**Требуется перед деплоем на прод** (Релиз 1):
- `MESSENGER_ENABLED=1` в `.env.prod` (активирует messenger в INSTALLED_APPS)
- CORS widget-origin для будущих inbox-виджетов
- `MESSENGER_WIDGET_STRICT_ORIGIN=True`

## Поведение UI

- **Прод**: `/companies/<id>/` — classic карточка. Preview v3/b отсутствует в коде (be569ad).
- **Staging**: classic + v3/a, v3/b, v3/c preview. `/companies/<id>/v3/b/` рендерится, class `.vb-hero` присутствует.

После Релиза 1 прод получит v3/b preview. Classic останется основой.

## Docker compose

| Параметр | Прод | Staging |
|----------|------|---------|
| Compose-файлы | `docker-compose.prod.yml` + `docker-compose.vds.yml` | `docker-compose.staging.yml` |
| Сервисы | db, redis, web, celery, celery-beat | + nginx (в контейнере), + websocket |
| Nginx | Хостовый `/etc/nginx/...` | В контейнере |
| Volumes | `pgdata`, `redisdata` + хостовые медиа | `pgdata_staging`, `redisdata_staging`, `media_staging`, `static_staging` |
| Сеть | `proficrm_default` | `crm_staging_network` |
| Websocket | Не запускается (channels не в requirements) | Daphne активен |
| Chatwoot | Активно работает на `chat.groupprofi.ru` (отдельные контейнеры) | — |

## Hotfix-ы на проде (2026-04-18)

Ручной `docker cp` в `proficrm-web-1`:

1. `ui/views/company_detail.py` — CASCADE task delete перед `company.delete()`
2. `templates/ui/company_detail.html` — обновлённый текст модалки
3. `templates/ui/company_list.html` — баннер «Активен дополнительный фильтр»

**Все 3 зеркалены коммитом `b7dcb21a`** → при Релизе 1 перезапишутся на каноничную версию. Конфликтов нет.

## Риски Релиза 1 (пересмотрено)

### P0 (блокирующие) — НЕТ

Предыдущая версия документа указывала 3 P0-блокера. **Все сняты** после dress rehearsal:

- ~~44 миграции разом~~ — уже применены
- ~~`messenger + channels` в INSTALLED_APPS требует env~~ — да, требует, но env известен (`MESSENGER_ENABLED=1`)
- ~~БД схема опережает миграции~~ — теперь схема и миграции синхронны

### P1 (важно)

1. **Пересборка Docker-образов** — 3-5 минут, пересобрать `web`, `celery`, `celery-beat`, + новый `websocket`
2. `channels 4.2.0` + `websocket` контейнер — запустить с channel-layer в Redis
3. **Host nginx reload** после деплоя (websocket location, CORS widget, SSE messenger)
4. `CORS_ALLOWED_ORIGINS` — добавить widget-origins в `.env.prod`

### P2 (мониторить)

5. 1 Campaign на проде (staging 11) — схема `CampaignRecipient` совпадает
6. Preview v3/a/b/c на проде **появляется впервые** — менеджеры увидят в /companies/<id>/v3/b/
7. Mobile app: **есть, 78 MB Kotlin кода, готов к pre-prod полировке** (не в Релизе 1, отдельный трек)

## Рекомендуемый порядок Релиза 1

```
1. Backup БД (pg_dump в /tmp — как на Day 3)
2. Обновить .env.prod: MESSENGER_ENABLED=1, CORS widget-origin
3. git pull на /opt/proficrm/
4. docker compose build web celery celery-beat websocket
5. python manage.py migrate  (2 минорные миграции, секунды)
6. collectstatic
7. docker compose up -d web celery celery-beat websocket
8. Host nginx reload
9. Smoke: /companies/, /tasks/, /messenger/ (пустой), /companies/<id>/v3/b/
```

**Ожидаемый downtime: 5-10 минут.** До Day 3 оценивался как 20-60 минут.

## Связанное

- [[Статус прод]] — основная страница статуса production
- [[Статус staging]] — статус staging
- [[Известные проблемы]] — текущие баги
- `docs/runbooks/00-prod-snapshot-2026-04-20.md` — Day 1 аудит
- `docs/runbooks/01-prod-snapshot-day2.md` — Day 2 аудит
- `docs/runbooks/10-release-0-night-hotfix.md` — план Релиза 0
- `docs/runbooks/20-release-1-main-to-prod.md` — план Релиза 1
- `docs/runbooks/30-orphan-contacts-cleanup.md` — удаление 343 orphan-контактов
