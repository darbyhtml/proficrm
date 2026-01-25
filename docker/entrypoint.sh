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

exec "$@"
