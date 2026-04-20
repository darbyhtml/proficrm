"""
Константы модуля mailer.

Важно: выносим сюда, чтобы избежать циклических импортов (tasks <-> views).
"""

# Значение по умолчанию; может быть переопределено в GlobalMailAccount.per_user_daily_limit
PER_USER_DAILY_LIMIT_DEFAULT = 100

# Cooldown на повторное использование email после "очистки" кампании (дней)
COOLDOWN_DAYS_DEFAULT = 3


# ENTERPRISE: Максимальное количество получателей в одной кампании
# Предотвращает блокировку очереди одной большой кампанией
# Значение берётся из settings.MAILER_MAX_CAMPAIGN_RECIPIENTS (по умолчанию 10000)
def get_max_campaign_recipients() -> int:
    """Получить максимальное количество получателей из settings."""
    from django.conf import settings

    return getattr(settings, "MAILER_MAX_CAMPAIGN_RECIPIENTS", 10000)


MAX_CAMPAIGN_RECIPIENTS = get_max_campaign_recipients()  # Для обратной совместимости

# Рабочее время (МСК, Europe/Moscow), когда разрешена отправка авто-рассылок
WORKING_HOURS_START = 9
WORKING_HOURS_END = 18  # не включительно

# Причины отложения (defer) рассылки — для CampaignQueue.defer_reason
DEFER_REASON_DAILY_LIMIT = "daily_limit"
DEFER_REASON_QUOTA = "quota_exhausted"
DEFER_REASON_OUTSIDE_HOURS = "outside_hours"
DEFER_REASON_RATE_HOUR = "rate_per_hour"
DEFER_REASON_TRANSIENT_ERROR = "transient_error"
DEFER_REASONS = (
    (DEFER_REASON_DAILY_LIMIT, "Дневной лимит пользователя"),
    (DEFER_REASON_QUOTA, "Квота smtp.bz исчерпана"),
    (DEFER_REASON_OUTSIDE_HOURS, "Вне рабочего времени"),
    (DEFER_REASON_RATE_HOUR, "Лимит в час"),
    (DEFER_REASON_TRANSIENT_ERROR, "Временная ошибка отправки"),
)

# Redis lock timeout для задачи send_pending_emails (секунды).
# Переопределяется через settings.MAILER_SEND_LOCK_TIMEOUT.
# 120 сек достаточно: батч из 50 писем × ~2 сек/письмо = ~100 сек.
SEND_TASK_LOCK_TIMEOUT = 120

# Задержка перед следующей проверкой квоты smtp.bz (минуты)
QUOTA_RECHECK_MINUTES = 30

# Таймаут для определения "зависшей" кампании в reconcile (минуты)
STUCK_CAMPAIGN_TIMEOUT_MINUTES = 30

# Максимальное количество страниц при синхронизации smtp.bz логов за один запуск
SMTP_BZ_SYNC_MAX_PAGES = 10

# Максимальное количество получателей в одном батче отправки.
# Переопределяется через settings.MAILER_SEND_BATCH_SIZE.
SEND_BATCH_SIZE_DEFAULT = 10

# Circuit breaker: после N подряд transient-ошибок SMTP кампания ставится на паузу
CIRCUIT_BREAKER_THRESHOLD = 10

# Базовая задержка (минуты) перед повтором при transient-ошибке SMTP (exponential backoff)
TRANSIENT_RETRY_DELAY_MINUTES = 5

# Лимит попыток отписки с одного IP в час (защита от перебора токенов)
UNSUBSCRIBE_RATE_LIMIT_PER_HOUR = 10

# TTL (секунды) для ключей Redis rate limiter (2 часа — запас на граничный час)
RATE_LIMIT_CACHE_TTL_SECONDS = 7200

# Fallback-лимиты smtp.bz, если квота ещё не синхронизирована
SMTP_BZ_MAX_PER_HOUR_DEFAULT = 100
SMTP_BZ_EMAILS_LIMIT_DEFAULT = 15000

# Максимальная длина сообщения об ошибке в SendLog и CampaignRecipient.last_error
MAX_ERROR_MESSAGE_LENGTH = 500

# Размер батча при bulk_update (защита от превышения max_allowed_packet MySQL/Postgres)
BULK_UPDATE_BATCH_SIZE = 500
