# Telegram bot inventory — 2026-04-20

_Проверка в рамках W0.4 Track D — замена UptimeRobot на self-hosted
monitoring с Telegram alerts._

## Результат поиска: **Telegram bot НЕ найден**

Проверены источники:

| Источник | Результат |
|----------|-----------|
| `/opt/proficrm-staging/.env` | нет `TELEGRAM*` / `TG_*` переменных |
| `/opt/proficrm/.env` | нет (проверено в рамках общего env scan) |
| `/etc/proficrm/env.d/*.conf` (mailer, glitchtip) | нет |
| Backend code `backend/**/*.py` | нет импорта `telebot`, `pyTelegramBotAPI`, `aiogram`, нет `TELEGRAM_BOT*` констант |
| `docs/**/*.md` | только упоминания «нужно сделать Telegram alerts» в runbook 40 и roadmap |

## Альтернативы поиска (если бот где-то есть, но я пропустил)

Если пользователь вспомнит существующий бот:
1. Проверить в личном Telegram @BotFather → `/mybots` — список созданных ботов.
2. Проверить в `/etc/systemd/system/*.service` — может быть отдельный systemd unit.
3. Проверить cron-задачи на сервере: `crontab -l` (root), `crontab -u sdm -l`.

## Что это значит для W0.4 Track D

- **Uptime Kuma** ставится **без Telegram notifications** в этой сессии.
  Алерты будут только в email (если настроен SMTP в Kuma) или UI.
- Telegram wiring отложен до ответа пользователя в `open-questions.md` Q7.
- После получения токена — добавление notification channel в Kuma занимает
  2 минуты через UI (Settings → Notifications → Telegram → token + chat_id).

## Что сделать пользователю

Минимум: создать нового бота через @BotFather:

```
/newbot
→ имя: "GroupProfi Infra Alerts"
→ username: @groupprofi_infra_alerts_bot
→ Получить token: <REAL_TOKEN>

Добавить бота в личный chat → написать /start
Получить chat_id:
  curl https://api.telegram.org/bot<REAL_TOKEN>/getUpdates | jq '.result[0].message.chat.id'
```

Сохранить token + chat_id в `/etc/proficrm/env.d/telegram-alerts.conf` (mode 600).
