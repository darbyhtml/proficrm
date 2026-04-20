---
tags: [runbook, observability, sentry, cicd, github-actions]
created: 2026-04-20
status: READY — требует разовой ручной настройки secret-а
---

# Observability + CI/CD — что активировать

Ответ на оценку «Observability 3/10» и «Деплой 6/10» из `02-prod-snapshot-day3-extended.md`.

Код уже в main (коммиты `397eb85e` Sentry, `<deploy-staging>` auto-deploy). Остаётся **одноразовая ручная настройка** — 15 минут.

## 1. Sentry free tier

### Что даёт

- Все Python exception'ы в web и celery автоматически собираются и группируются
- Видно: кто, когда, с каким браузером и какими аргументами запроса падал
- Stacktrace с переменными фрейма (при `send_default_pii=False` — без личных данных)
- Интеграция с Redis / Django ORM / Celery — трассировка SQL+cache в каждом событии
- Уведомления на email (или Slack) при новых ошибках
- **5 000 событий/месяц бесплатно** — для 50 пользователей с уже-исправленным policy engine этого **хватит с запасом**

### Шаги настройки (10 минут)

1. Зарегистрироваться на https://sentry.io (free tier). Один аккаунт.
2. Create project → Django. Название: `proficrm`.
3. Скопировать DSN — что-то вида `https://abc@o123456.ingest.sentry.io/0987654`.
4. В Netangels / через SSH добавить в `/opt/proficrm/.env`:
   ```
   SENTRY_DSN=https://abc@o123456.ingest.sentry.io/0987654
   SENTRY_ENVIRONMENT=production
   SENTRY_RELEASE=main-2026-04-21       # или $(git rev-parse HEAD) после деплоя
   ```
5. И в `/opt/proficrm-staging/.env.staging`:
   ```
   SENTRY_DSN=<тот же DSN>
   SENTRY_ENVIRONMENT=staging
   ```
   (Можно отдельный проект `proficrm-staging` в Sentry для разделения.)
6. `docker compose up -d --no-deps --force-recreate web celery` — 30 сек downtime web.
7. Проверить: в settings.py `sentry_sdk.init()` сработал:
   ```bash
   docker exec proficrm-web-1 python -c "import sentry_sdk; print(sentry_sdk.Hub.current.client)"
   ```
   Ожидаем не-None клиента.
8. Триггер-тест: открыть `/trigger-sentry/` или выполнить через shell `1/0`. В Sentry dashboard через 5-10 сек появится событие.

### Что внутри

В `settings.py` (коммит `397eb85e`) условная инициализация:
- Без `SENTRY_DSN` env — no-op (локальная разработка, CI).
- С DSN — активируются DjangoIntegration, CeleryIntegration, RedisIntegration.
- `traces_sample_rate=0.0` — перформанс-трассировка выключена (экономим квоту).
- `send_default_pii=False` — без ФИО/email пользователей.
- Игнорируем ожидаемые исключения (`Http404`, `PermissionDenied`).

### Тюнинг после первого дня

- Если квота съедается быстро (>100 events/день) — посмотреть в Sentry → Issues top, исправить самые частые.
- Если всё тихо — поднять `SENTRY_TRACES_SAMPLE_RATE=0.05` для перформанс-мониторинга.

## 2. GitHub Actions: auto-deploy на staging

### Что делает

После каждого push в `main` и прохождения CI (`test` + `secret-scan`) — staging автоматически подтягивает main, пересобирает образы, применяет миграции, перезапускает сервисы.

**Прод не трогается никогда.** Прод деплоится только вручную по `21-release-1-ready-to-execute.md`.

### Шаги настройки (5 минут)

1. На локальной машине сгенерировать новый ed25519-ключ для Actions:
   ```bash
   ssh-keygen -t ed25519 -f ~/.ssh/github_actions_staging -N ""
   ```
2. Public part (`github_actions_staging.pub`) добавить в `~/.ssh/authorized_keys` root-а на staging:
   ```bash
   ssh -i ~/.ssh/id_proficrm_deploy root@5.181.254.172 "echo '<содержимое .pub>' >> ~/.ssh/authorized_keys"
   ```
3. Private part (`github_actions_staging` — без `.pub`, содержит `-----BEGIN OPENSSH PRIVATE KEY-----`):
   - GitHub → proficrm repo → Settings → Secrets and variables → Actions
   - New repository secret
   - Name: `STAGING_SSH_PRIVATE_KEY`
   - Value: вставить всё содержимое приватного файла (включая `-----BEGIN` и `-----END` строки)
4. Следующий push в `main` запустит workflow `Deploy Staging`. Проверить через https://github.com/darbyhtml/proficrm/actions.

### Безопасность

- Ключ **только для staging** — если утечёт, прод в безопасности (разные IP в iptables / другие ACL)
- Можно ограничить ключ (force-command): в `authorized_keys` перед публичным ключом добавить `command="cd /opt/proficrm-staging && git pull && ..."` — тогда ключом нельзя будет делать ничего кроме deploy'я. **Рекомендую** для Релиза 2.

### Workflow пропускает, если HEAD не изменился

Если по какой-то причине пуш был в `main`, но staging уже на этом HEAD — deploy skipping. Это экономит билды (GitHub Actions 2000 мин/мес free).

## 3. UptimeRobot (бесплатно, 5 минут)

Если хочется мониторить uptime и получать SMS при 502:
1. https://uptimerobot.com → Sign up (free — 50 мониторов, 5-минутный интервал)
2. New monitor → HTTP(s):
   - `https://crm.groupprofi.ru/health/` → ожидаемый 200
   - `https://crm-staging.groupprofi.ru/health/`
   - `https://chat.groupprofi.ru/` (Chatwoot)
3. Contacts → email + Telegram bot (через `@UptimeRobot`)

50 пользователей получают Tg-алерт если CRM недоступна 5+ минут.

## 4. CI health monitoring

Уже есть `.github/workflows/ci.yml`:
- `lint` (ruff)
- `secret-scan` (gitleaks)
- `test` (Django)
- `deps-audit` (pip-audit, не блокирует)

Можно добавить **badge** в README.md:
```
![CI](https://github.com/darbyhtml/proficrm/actions/workflows/ci.yml/badge.svg)
![Deploy Staging](https://github.com/darbyhtml/proficrm/actions/workflows/deploy-staging.yml/badge.svg)
```

## Сводная таблица

| Функциональность | Статус до сегодня | Статус после |
|------------------|-------------------|--------------|
| Error tracking | нет | Sentry free (5K/мес) |
| Auto-deploy staging | нет (ручной SSH) | GitHub Actions on push |
| Uptime monitoring | нет | UptimeRobot free (50 мониторов) |
| CI | ✅ уже было | ✅ без изменений |

После этих трёх активаций **observability: 3/10 → 7/10**. Для CRM на 50 пользователей — более чем достаточно.

## Аудитор

Senior onboarding audit 2026-04-20.
Все **код** и **yaml** уже в main. Нужны только разовые ручные шаги выше.
