# Минимальный мониторинг без SaaS (crm.groupprofi.ru)

## health_check.sh

`scripts/health_check.sh` — проверяет доступность `HEALTH_URL` (по умолчанию `https://crm.groupprofi.ru/health/`). При HTTP не 2xx/3xx или ошибке соединения — exit 1. Если заданы `TELEGRAM_BOT_TOKEN` и `TELEGRAM_CHAT_ID`, при ошибке отправляет сообщение в Telegram Bot API.

**Переменные (в .env или при вызове):**

- `HEALTH_URL` — URL для проверки (по умолчанию `https://crm.groupprofi.ru/health/`)
- `TELEGRAM_BOT_TOKEN` — токен бота (получить у [@BotFather](https://t.me/BotFather))
- `TELEGRAM_CHAT_ID` — ID чата или канала (без токенов уведомления не отправляются)

## Запуск по расписанию

Cron, например каждые 5 минут:

```
*/5 * * * * cd /opt/proficrm && ./scripts/health_check.sh >> /var/log/proficrm-health.log 2>&1
```

Или systemd timer по аналогии с `config/systemd/proficrm-backup.*`.

## Настройка Telegram-бота

1. Создать бота: [@BotFather](https://t.me/BotFather) → /newbot → сохранить токен.
2. Узнать `TELEGRAM_CHAT_ID`: написать боту в личку, затем открыть `https://api.telegram.org/bot<TOKEN>/getUpdates` — в ответе `message.chat.id`.
3. Добавить в .env (не коммитить):

   ```
   TELEGRAM_BOT_TOKEN=123:ABC...
   TELEGRAM_CHAT_ID=-1001234567890
   ```

Для канала: добавить бота как администратора, использовать ID канала (обычно отрицательный).
