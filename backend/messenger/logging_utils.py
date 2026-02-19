"""
Утилиты для безопасного логирования в Messenger.

- Логгеры: messenger.widget и messenger.ui
- Маскировка токенов в логах
"""

import logging

# Создаём логгеры
widget_logger = logging.getLogger("messenger.widget")
ui_logger = logging.getLogger("messenger.ui")


def mask_token(token: str | None, show_chars: int = 4) -> str:
    """
    Маскирует токен для безопасного логирования.
    
    Показывает только первые и последние N символов.
    Если токен короче 2*N символов - показывает только первые N.
    
    Args:
        token: Токен для маскировки
        show_chars: Количество символов с начала и конца (по умолчанию 4)
    
    Returns:
        Замаскированный токен (например, "abcd...xyz1" или "abcd..." если короткий)
    """
    if not token:
        return "<empty>"
    
    token_str = str(token)
    token_len = len(token_str)
    
    if token_len <= show_chars * 2:
        # Токен слишком короткий - показываем только начало
        return f"{token_str[:show_chars]}..."
    
    return f"{token_str[:show_chars]}...{token_str[-show_chars:]}"


def safe_log_widget_error(
    logger: logging.Logger,
    level: int,
    message: str,
    *,
    widget_token: str | None = None,
    session_token: str | None = None,
    inbox_id: int | None = None,
    conversation_id: int | None = None,
    contact_id: str | None = None,
    error: Exception | None = None,
    **extra,
):
    """
    Безопасное логирование ошибок Widget API без утечки токенов.
    """
    # Нельзя класть ключи вроде "message" в extra — logging.LogRecord зарезервировал их.
    # Храним текст сообщения в отдельном поле.
    safe_data = {
        "log_message": message,
    }
    
    if widget_token is not None:
        safe_data["widget_token"] = mask_token(widget_token)
    if session_token is not None:
        safe_data["session_token"] = mask_token(session_token)
    if inbox_id is not None:
        safe_data["inbox_id"] = inbox_id
    if conversation_id is not None:
        safe_data["conversation_id"] = conversation_id
    if contact_id is not None:
        safe_data["contact_id"] = str(contact_id) if contact_id else None
    # Добавляем дополнительные поля, избегая конфликтов с LogRecord
    if extra:
        extra = dict(extra)
        extra.pop("message", None)
        extra.pop("msg", None)
        safe_data.update(extra)
    
    if error:
        logger.log(level, message, extra=safe_data, exc_info=True)
    else:
        logger.log(level, message, extra=safe_data)
