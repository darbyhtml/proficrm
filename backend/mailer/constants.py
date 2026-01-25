"""
Константы модуля mailer.

Важно: выносим сюда, чтобы избежать циклических импортов (tasks <-> views).
"""

# Значение по умолчанию; может быть переопределено в GlobalMailAccount.per_user_daily_limit
PER_USER_DAILY_LIMIT_DEFAULT = 100

# Cooldown на повторное использование email после "очистки" кампании (дней)
COOLDOWN_DAYS_DEFAULT = 3

# Рабочее время (МСК), когда разрешена отправка авто-рассылок
WORKING_HOURS_START = 9
WORKING_HOURS_END = 18  # не включительно

# Причины отложения (defer) рассылки — для CampaignQueue.defer_reason
DEFER_REASON_DAILY_LIMIT = "daily_limit"
DEFER_REASON_QUOTA = "quota_exhausted"
DEFER_REASON_OUTSIDE_HOURS = "outside_hours"
DEFER_REASON_RATE_HOUR = "rate_per_hour"
DEFER_REASONS = (
    (DEFER_REASON_DAILY_LIMIT, "Дневной лимит пользователя"),
    (DEFER_REASON_QUOTA, "Квота smtp.bz исчерпана"),
    (DEFER_REASON_OUTSIDE_HOURS, "Вне рабочего времени"),
    (DEFER_REASON_RATE_HOUR, "Лимит в час"),
)

