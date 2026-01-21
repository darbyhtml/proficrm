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

