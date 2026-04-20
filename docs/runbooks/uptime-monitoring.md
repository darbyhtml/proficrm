# Runbook: Uptime monitoring (Uptime Kuma)

_Wave 0.4 Track D (2026-04-20). Self-hosted, replaces UptimeRobot._

## Почему не UptimeRobot

- **Недоступен в РФ** без VPN (IP-block провайдером — проверено 2026-04).
- **Telegram integration** с 2024 в Pro plan ($54/mo) — противоречит
  «только self-hosted/free» принципу Wave 0 (00_MASTER_PLAN.md §2.1).

## Стек

**Uptime Kuma** (`louislam/uptime-kuma:1`). Open-source, ~80 MB RAM, UI-driven,
native Telegram/Email/Slack/webhook notifications.

Директория: `/opt/proficrm-observability/` (рядом с GlitchTip). Отдельный
compose project `proficrm-uptime`.

## Расположение

```
/opt/proficrm-observability/
├── docker-compose.observability.yml   (GlitchTip stack)
├── docker-compose.uptime.yml           (Uptime Kuma stack)
└── scripts/
    ├── glitchtip-backup.sh
    └── ... (uptime-kuma-backup.sh — W10)
```

## Memory budget

| Сервис | Hard limit | Реально |
|--------|-----------|---------|
| uptime-kuma | 128 MB | ~91 MB (71%) |

Итого observability-стека: GlitchTip 608 MB + Kuma 128 MB = **736 MB**.
VPS total 8 GB, after full observability ~4.8 GB used + 3.0 GB available.
Swap стабилен на 1.0 GB.

## Доступ

### Пока нет DNS (см. Q8 open-questions.md)

SSH tunnel:
```bash
ssh -L 3001:localhost:3001 root@5.181.254.172
# Открыть http://localhost:3001 в браузере
```

### После DNS uptime.groupprofi.ru + TLS

```bash
# На сервере:
sudo cp configs/nginx/uptime.groupprofi.ru.conf /etc/nginx/sites-available/
sudo ln -sf /etc/nginx/sites-available/uptime.groupprofi.ru.conf /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d uptime.groupprofi.ru --non-interactive --agree-tos -m admin@groupprofi.ru --redirect

# Добавить basic-auth (защита от публичного доступа):
sudo htpasswd -c /etc/nginx/.htpasswd-uptime admin
# Раскомментировать auth_basic в nginx config.
```

## Первый setup (через UI)

1. Tunnel → `http://localhost:3001/`
2. Форма admin setup — создать учётку:
   - Username: `admin`
   - Password: strong random (сохранить в `/etc/proficrm/env.d/uptime-kuma.conf` mode 600)
3. После логина — Settings → General:
   - Timezone: `Europe/Moscow`
   - Primary base URL: `https://uptime.groupprofi.ru` (заполнить после DNS)

## 3 обязательных monitor'а

Конфигурация через UI → «Add New Monitor»:

### Monitor 1 — CRM Production

| Поле | Значение |
|------|----------|
| Type | HTTP(s) |
| Friendly name | CRM Production |
| URL | `https://crm.groupprofi.ru/health/` |
| Heartbeat interval | 60 seconds |
| Retries | 3 |
| Accept status codes | 200 |
| Ignore TLS errors | No |

_Замечание: `/live/` появится в prod после W0.5a. Пока используем `/health/`._

### Monitor 2 — CRM Staging

| Поле | Значение |
|------|----------|
| Type | HTTP(s) |
| URL | `https://crm-staging.groupprofi.ru/live/` |
| Heartbeat | 60 sec |
| Retries | 3 |
| Accept | 200 |

### Monitor 3 — GlitchTip

| Поле | Значение |
|------|----------|
| Type | HTTP(s) |
| URL | `https://glitchtip.groupprofi.ru/_health/` |
| Heartbeat | 120 sec (реже — не критичный сервис для UX) |
| Retries | 2 |
| Accept | 200 |

## Telegram alerts

**Pending Q7 в `open-questions.md`** — нужен Telegram bot token.

После получения:
1. UI → Settings → Notifications → + Setup Notification
2. Type: **Telegram**
3. Friendly name: «Infra Alerts → Admin»
4. Bot Token: из `/etc/proficrm/env.d/telegram-alerts.conf`
5. Chat ID: тоже из файла
6. Test → «Test notification sent successfully»
7. Edit каждый из 3 monitors → Notifications → включить этот channel

### Альтернатива: Email (пока нет Telegram)

UI → Settings → Notifications → + Setup → **SMTP**:
- SMTP server: `smtp.bz` (тот же что для CRM)
- Username/password: из `/etc/proficrm/env.d/mailer.conf`
- From: `noreply@groupprofi.ru`
- To: `admin@groupprofi.ru`

## Alert тест

**Не проводить на production!** Только на staging:

```bash
# Искусственный downtime на staging
ssh root@5.181.254.172 'docker stop crm_staging_web'
# Ждать 3 interval × 60s = ~4 минуты
# Должен прийти alert (email / Telegram)

# Восстановить
ssh root@5.181.254.172 'docker start crm_staging_web'
# Через 2 minute — recovery alert
```

**Важные SLI для документирования**:
- Время от падения до первого alert: target < 5 минут.
- Recovery alert после восстановления: target < 3 минут.
- False-positives: target 0 в месяц (если есть — поднять retries или heartbeat interval).

## Troubleshooting

### Kuma не стартует

```bash
ssh root@5.181.254.172 '
cd /opt/proficrm-observability
docker compose -f docker-compose.uptime.yml -p proficrm-uptime logs --tail 50
'
```

### Слишком много false-positives

- Увеличить `retries` с 3 до 5.
- Увеличить `heartbeat interval` с 60 до 120.
- Добавить `Connection timeout` = 15 сек (для медленных ответов).

### Потеря истории после restart

История в volume `proficrm-uptime_uptime-kuma-data`. Не должна теряться. Если
потеря — проверить `docker volume ls | grep uptime`.

## Бэкап config

Текущая конфигурация Kuma экспортируется через UI → Settings → Backup →
«Export Backup» (JSON). В W10 добавим авто-бэкап `uptime-kuma-backup.sh`
в `/var/backups/uptime-kuma/`.

Пока — раз в месяц вручную сохранять JSON в `/etc/proficrm/backups/kuma/`.

## Связанные документы

- `docker-compose.uptime.yml` — compose.
- `docs/audit/telegram-bot-inventory.md` — статус Telegram bot.
- `docs/open-questions.md` — Q7 (bot) + Q8 (DNS uptime).
- `docs/runbooks/glitchtip-setup.md` — рядом стоит GlitchTip.
