#!/bin/sh
# Fail-fast: обязательные переменные для web/celery/beat.
# Использование: entrypoint проверяет, затем exec "$@".
# migrate/collectstatic при деплое тоже проходят проверку (env из compose).

REQUIRED="DJANGO_SECRET_KEY POSTGRES_PASSWORD POSTGRES_USER POSTGRES_DB PUBLIC_BASE_URL MAILER_FERNET_KEY DJANGO_ALLOWED_HOSTS DJANGO_CSRF_TRUSTED_ORIGINS"
FAIL=0

for VAR in $REQUIRED; do
    eval "VAL=\$$VAR"
    if [ -z "$VAL" ]; then
        echo "ERROR: $VAR is required and must not be empty. Set it in .env" >&2
        FAIL=1
    fi
done

if [ "$FAIL" -eq 1 ]; then
    exit 1
fi

# Права на volume media/static: при первом создании тома владелец root — crmuser не может писать.
# На проде часто используются bind-mount (./data/media, ./data/staticfiles): chown там может быть
# запрещён (Operation not permitted), а владелец на хосте уже 1000:1000 = crmuser — делаем chown без выхода по ошибке.
if [ -d /app/backend/media ]; then
    chown -R crmuser:crmuser /app/backend/media 2>/dev/null || true
fi
if [ -d /app/backend/staticfiles ]; then
    chown -R crmuser:crmuser /app/backend/staticfiles 2>/dev/null || true
fi

# Запуск от crmuser; если переключение пользователя запрещено (rootless Docker, политики), запускаем от root.
if gosu crmuser true 2>/dev/null; then
    exec gosu crmuser "$@"
else
    exec "$@"
fi
