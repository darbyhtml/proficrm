#!/usr/bin/env python3
"""
Создаёт или обновляет .env.staging из env.staging.template.
Подставляет сгенерированные DJANGO_SECRET_KEY и MAILER_FERNET_KEY (если ещё CHANGE_ME).
POSTGRES_PASSWORD нужно задать вручную в .env.staging после запуска скрипта.
Запуск: из корня репозитория — python scripts/setup_staging_env.py
        или из папки стагинга — python scripts/setup_staging_env.py (если скрипт в репо).
"""
from pathlib import Path
import secrets
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = REPO_ROOT / "env.staging.template"
OUTPUT = REPO_ROOT / ".env.staging"


def main():
    if not TEMPLATE.exists():
        print(f"Не найден {TEMPLATE}. Запустите скрипт из корня репозитория.", file=sys.stderr)
        sys.exit(1)

    content = TEMPLATE.read_text(encoding="utf-8")

    if "CHANGE_ME_GENERATE_STRONG_KEY" in content:
        key = secrets.token_urlsafe(50)
        content = content.replace("DJANGO_SECRET_KEY=CHANGE_ME_GENERATE_STRONG_KEY", f"DJANGO_SECRET_KEY={key}")
        print("Подставлен новый DJANGO_SECRET_KEY")

    if "CHANGE_ME_GENERATE_FERNET_KEY" in content:
        try:
            from cryptography.fernet import Fernet
            key = Fernet.generate_key().decode()
            content = content.replace("MAILER_FERNET_KEY=CHANGE_ME_GENERATE_FERNET_KEY", f"MAILER_FERNET_KEY={key}")
            print("Подставлен новый MAILER_FERNET_KEY")
        except ImportError:
            print("Установите cryptography: pip install cryptography — затем снова запустите скрипт или сгенерируйте ключ: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"")

    if "CHANGE_ME_STRONG_PASSWORD" in content:
        print("ВНИМАНИЕ: задайте пароль БД вручную в .env.staging: POSTGRES_PASSWORD=ваш_надёжный_пароль")

    OUTPUT.write_text(content, encoding="utf-8")
    print(f"Записано: {OUTPUT}")
    print("Проверьте и при необходимости отредактируйте POSTGRES_PASSWORD и SECURITY_CONTACT_EMAIL, затем запустите ./deploy_staging.sh")


if __name__ == "__main__":
    main()
