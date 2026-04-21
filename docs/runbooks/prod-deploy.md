# Runbook: Prod deploy (Gated Promotion)

_Принят 2026-04-20 на смену blanket hook-block. Применимо начиная с Release v1.0._

См. `CLAUDE.md` §«Деплой — Gated Promotion Model» R1-R5.

---

## Prerequisites (все 4 обязательны)

1. **Tag создан и запушен**:
   ```bash
   # Обычно создаётся ПОСЛЕ завершения волны (W0.4 completed + all smoke green):
   git tag -a release-v1.N-wX.Y-<short-name> -m "Wave X.Y complete: <summary>" main
   git push origin release-v1.N-wX.Y-<short-name>
   ```

2. **Staging зелёный**:
   - `docker exec crm_staging_web python manage.py test` → 100% pass
   - `tests/smoke/prod_post_deploy.sh` (с `BASE_URL=https://crm-staging.groupprofi.ru`) зелёный
   - UptimeRobot последние 24ч — без downtime на staging

3. **Промпт содержит 2 маркера**:
   - `DEPLOY_PROD_TAG=release-v1.N-wX.Y-<name>` — какой тег деплоим
   - `CONFIRM_PROD=yes` — явное разрешение от пользователя трогать prod

4. **Rollback plan знаком**: понимаете на какой тег откатиться если что
   (обычно предыдущий `release-v1.(N-1)-...`).

---

## Step 1 — Snapshot (обязательно)

Pre-deploy snapshot создаётся **каждый раз**, даже если deploy выглядит
«мелким». Сохраняется в git + файловую систему prod-хоста.

```bash
# 1. На prod-хосте
ssh root@5.181.254.172 '
SNAPSHOT_DATE=$(date +%Y%m%d_%H%M%S)
SNAPSHOT_DIR=/var/backups/proficrm-pre-deploy/$SNAPSHOT_DATE
mkdir -p $SNAPSHOT_DIR

# 1a. DB dump
cd /opt/proficrm
docker compose -f docker-compose.prod.yml -p proficrm exec -T db \
    pg_dump -U crm -d crm --format=custom \
    > $SNAPSHOT_DIR/db.pgdump

# 1b. Media rsync (если media/ используется)
rsync -aH /opt/proficrm/data/media/ $SNAPSHOT_DIR/media/ 2>/dev/null || true

# 1c. Env files
cp /opt/proficrm/.env $SNAPSHOT_DIR/env.backup
cp -r /etc/proficrm/env.d/ $SNAPSHOT_DIR/env.d/

# 1d. Current HEAD в git
git -C /opt/proficrm rev-parse HEAD > $SNAPSHOT_DIR/pre_deploy_head.txt

echo "Snapshot saved: $SNAPSHOT_DIR"
du -sh $SNAPSHOT_DIR
'
```

Снапшот коммитится в git отдельно (factual marker в истории):
```bash
# Локально:
git commit --allow-empty -m "pre-release-v1.N-snapshot-$(date +%Y-%m-%d)" \
    -m "DB snapshot + media + env backed up on prod host at /var/backups/proficrm-pre-deploy/YYYYMMDD_HHMMSS"
git push origin main
```

---

## Step 2 — Announcement для менеджеров

За 10 минут до deploy:
```bash
# Пример: отправить через Django admin CrmAnnouncement API (если есть),
# либо через Telegram @groupprofi_team бот:
curl -X POST https://api.telegram.org/bot<TOKEN>/sendMessage \
    -d chat_id=<MANAGERS_CHAT> \
    -d text="📢 Деплой CRM через ~10 минут (релиз v1.N). Возможны короткие перебои 20-60 секунд. Если столкнулись с ошибкой — дождитесь ещё 2 минуты."
```

Если обновление критичное (breaking UI) — предупреждать **за 1 час**
через CRM `Announcement` модалку.

---

## Step 3 — Deploy

```bash
# Предусловие: промпт содержит
#   DEPLOY_PROD_TAG=release-v1.N-wX.Y-name
#   CONFIRM_PROD=yes

ssh root@5.181.254.172 '
cd /opt/proficrm
git fetch --tags
git checkout release-v1.N-wX.Y-name

# Применяем миграции (--check сначала — если unsafe предупредит):
docker compose -f docker-compose.prod.yml -p proficrm exec -T web \
    python manage.py migrate --check 2>&1 || echo "MIGRATIONS PENDING"

docker compose -f docker-compose.prod.yml -p proficrm pull web celery worker
docker compose -f docker-compose.prod.yml -p proficrm exec -T web \
    python manage.py migrate --noinput
docker compose -f docker-compose.prod.yml -p proficrm up -d --no-deps web celery worker
docker compose -f docker-compose.prod.yml -p proficrm exec -T web \
    python manage.py collectstatic --noinput --clear
'
```

**Downtime expected**: 10-30 секунд (recreate web container через `up -d`).

---

## Step 4 — Post-deploy smoke

```bash
tests/smoke/prod_post_deploy.sh
# или с explicit BASE_URL:
BASE_URL=https://crm.groupprofi.ru tests/smoke/prod_post_deploy.sh
```

Все чеки должны пройти. Если **хоть один красный** — немедленно rollback
(см. Step 6).

---

## Step 5 — Monitoring первый час

1. **GlitchTip** (`https://glitchtip.groupprofi.ru/`) — чек на новые issues:
   - Фильтр по `environment=production` + «последний час».
   - Особо: rate error'ов > обычного baseline (обычно < 5/час).

2. **UptimeRobot** — все 3 монитора up, нет alert'ов.

3. **Grafana** (появится в W10):
   - Request rate (обычно 50-200 req/мин в рабочие часы)
   - p95 latency < 2 сек на `/companies/`
   - Celery queue length < 50

4. **Живые менеджеры**: чекнуть в чате Telegram через 15 минут нет ли жалоб.

---

## Step 6 — Rollback (если что-то плохо)

```bash
ssh root@5.181.254.172 '
cd /opt/proficrm

# Найти предыдущий тег:
git tag --sort=-creatordate | head -3
# Например: release-v1.4-w0.3-feature-flags

PREV_TAG=release-v1.4-w0.3-feature-flags
git checkout $PREV_TAG

# Если миграции НЕ обратимые — восстановить БД из snapshot:
# LATEST_SNAPSHOT=$(ls -td /var/backups/proficrm-pre-deploy/* | head -1)
# docker compose -f docker-compose.prod.yml -p proficrm stop web celery worker
# docker compose -f docker-compose.prod.yml -p proficrm exec -T db \
#     pg_restore --clean --if-exists -U crm -d crm < $LATEST_SNAPSHOT/db.pgdump
# docker compose -f docker-compose.prod.yml -p proficrm up -d

# Если миграции обратимые (обычно так — см. django-migration-linter в W0.2):
docker compose -f docker-compose.prod.yml -p proficrm up -d --no-deps web celery worker
'
```

Post-rollback:
1. Проверить `tests/smoke/prod_post_deploy.sh` снова зелёным.
2. Telegram уведомление «откатились на v1.N-1, проблема чинится».
3. Создать issue/hotlist-item про причину rollback'а.

---

## Step 7 — Post-deploy verification

### Через 1 час

- GlitchTip error rate: в пределах baseline (± 20%).
- UptimeRobot: нет downtime.
- Живые пользователи: нет жалоб в Telegram.
- Django ErrorLog (`/admin/audit/errorlog/`): нет новых критичных.

### Через 24 часа

- GlitchTip: total events за сутки в пределах baseline.
- DB: `SELECT COUNT(*) FROM django_session WHERE expire_date > NOW()` — активные сессии ok, не drop.
- Celery: `celery inspect stats` — queue length 0, tasks succeeded > baseline.

Если 24h verification зелёное — deploy «confirmed stable», можно начинать
планировать следующую волну.

---

## Примеры тегов

Исторические:
- `release-v0.0-prod-current` — зафиксированное состояние prod до gated
  promotion (2026-04-20, commit `be569ad4`, 330+ commits behind main).
  **2026-04-21 verify**: tag survived `git filter-repo` в public-readiness
  cleanup (commit pre-W0.4, не затронут фильтрацией). Rollback procedure OK.

Типичные будущие:
- `release-v1.0-w0-complete` — когда W0 (все 0.0-0.6 этапы) завершены,
  manual post-deploy steps done, GlitchTip accepts events.
- `release-v1.1-w1-company-detail-refactor` — после завершения W1.
- `release-v1.2-w2-policy-enforce` — после W2 (блокирующий!).

---

## Checklist перед командой «DEPLOY_PROD_TAG=... CONFIRM_PROD=yes»

- [ ] Tag создан и запушен (`git ls-remote origin refs/tags/release-*`)
- [ ] Staging зелёный: manage.py test + smoke + UptimeRobot 24ч
- [ ] Pre-deploy snapshot готов (DB + media + env)
- [ ] Migrations reviewed через `django-migration-linter`
- [ ] Announcement пользователям сделан
- [ ] Rollback-tag выбран и зафиксирован в промпте
- [ ] Смoke-test команда готова
- [ ] Team aware (Telegram пинг)
- [ ] Memory/disk на prod есть
- [ ] Backup cron для prod-БД работает (есть свежие дампы < 24ч)

Только после всех ✓ — дать промпт с маркерами.

---

## Связанные документы

- `CLAUDE.md` §«Деплой — Gated Promotion Model» — R1-R5 правила.
- `tests/smoke/prod_post_deploy.sh` — smoke-тесты.
- `docs/audit/process-lessons.md` — уроки процесса деплоев.
- `docs/runbooks/glitchtip-setup.md` — setup observability (пререквизит).
- `docs/runbooks/11-release-0-actual-2026-04-20.md` — фактический отчёт Release 0 (historical).
