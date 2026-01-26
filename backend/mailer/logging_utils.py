"""
ENTERPRISE: PII-safe logging utilities для mailer.
Обеспечивает безопасное логирование email адресов согласно политике.
"""
from __future__ import annotations

import hashlib
import logging
from django.conf import settings

logger = logging.getLogger(__name__)


def mask_email(email: str) -> str:
    """
    Маскирует email адрес для безопасного логирования.
    
    Примеры:
    - "john.doe@company.com" -> "jo***@company.com"
    - "a@b.com" -> "a***@b.com"
    - "verylongname@domain.co.uk" -> "ve***@domain.co.uk"
    
    Args:
        email: Email адрес для маскировки
    
    Returns:
        Маскированный email (первые 2 символа + *** + домен)
    """
    if not email or "@" not in email:
        return "***@***"
    
    local, domain = email.rsplit("@", 1)
    if len(local) <= 2:
        masked_local = local[0] + "***" if local else "***"
    else:
        masked_local = local[:2] + "***"
    
    return f"{masked_local}@{domain}"


def email_domain(email: str) -> str:
    """
    Извлекает домен из email адреса.
    
    Args:
        email: Email адрес
    
    Returns:
        Домен (часть после @) или "unknown" если невалидный
    """
    if not email or "@" not in email:
        return "unknown"
    return email.rsplit("@", 1)[1]


def email_hash(email: str, salt: str = None) -> str:
    """
    Создаёт короткий хэш email адреса для логирования.
    
    Args:
        email: Email адрес
        salt: Соль для хэширования (по умолчанию из settings)
    
    Returns:
        Короткий хэш (первые 12 символов hex)
    """
    if not email:
        return "000000000000"
    
    if salt is None:
        salt = getattr(settings, "MAILER_LOG_HASH_SALT", "default-salt-change-in-production")
    
    hash_obj = hashlib.sha256((email + salt).encode("utf-8"))
    return hash_obj.hexdigest()[:12]


def safe_email_for_logging(email: str, log_level: int = logging.INFO) -> dict[str, str]:
    """
    Возвращает безопасное представление email для structured logging.
    
    Политика:
    - INFO: только domain, masked_email и hash (НЕ полный email)
    - WARNING/ERROR: domain + masked_email (НЕ полный email)
    - DEBUG: может включать полный email ТОЛЬКО если MAILER_LOG_FULL_EMAILS=True
    
    Args:
        email: Email адрес
        log_level: Уровень логирования (logging.INFO, logging.ERROR, etc.)
    
    Returns:
        Словарь с безопасными полями для extra={}
    """
    pii_level = getattr(settings, "MAILER_LOG_PII_LEVEL", "ERROR")
    log_full_emails = getattr(settings, "MAILER_LOG_FULL_EMAILS", False)
    
    result = {
        "email_domain": email_domain(email),
        "email_masked": mask_email(email),
        "email_hash": email_hash(email),
    }
    
    # Полный email только в DEBUG и только если явно разрешено
    if log_level == logging.DEBUG and log_full_emails:
        result["email_full"] = email
    elif log_level >= logging.WARNING and pii_level in ("WARNING", "ERROR"):
        # В WARNING/ERROR можно добавить masked, но не полный
        pass  # Уже есть email_masked
    # В INFO и выше - только domain, masked, hash
    
    return result


def get_pii_log_fields(email: str, log_level: int = logging.INFO) -> dict[str, str]:
    """
    Удобный alias для safe_email_for_logging.
    Используется в structured logging.
    """
    return safe_email_for_logging(email, log_level)
