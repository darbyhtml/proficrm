"""
ENTERPRISE: JSON formatter для structured logging.
Гарантирует, что extra поля попадают в лог-вывод.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime


class JSONFormatter(logging.Formatter):
    """
    JSON formatter для structured logging.
    Гарантирует, что все extra поля попадают в лог-вывод.
    """
    
    def format(self, record: logging.LogRecord) -> str:
        """
        Форматирует log record в JSON.
        
        Формат:
        {
            "level": "INFO",
            "logger": "mailer.tasks",
            "message": "Email sent successfully",
            "timestamp": "2026-01-26T12:00:00Z",
            "campaign_id": "uuid",
            "queue_id": "uuid",
            ... (все extra поля)
        }
        """
        log_data = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
        
        # Добавляем все extra поля
        if hasattr(record, "extra") and isinstance(record.extra, dict):
            log_data.update(record.extra)
        else:
            # Извлекаем extra из record.__dict__ (стандартный способ)
            for key, value in record.__dict__.items():
                if key not in {
                    "name", "msg", "args", "levelname", "levelno", "pathname",
                    "filename", "module", "lineno", "funcName", "created",
                    "msecs", "relativeCreated", "thread", "threadName",
                    "processName", "process", "message", "exc_info", "exc_text",
                    "stack_info", "getMessage",
                }:
                    # Это custom поле (скорее всего из extra)
                    try:
                        # Проверяем, что значение сериализуемо
                        json.dumps(value)
                        log_data[key] = value
                    except (TypeError, ValueError):
                        # Если не сериализуемо, конвертируем в строку
                        log_data[key] = str(value)
        
        # Добавляем exception info если есть
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        
        try:
            return json.dumps(log_data, ensure_ascii=False, default=str)
        except (TypeError, ValueError) as e:
            # Fallback на простой формат если JSON не получается
            return json.dumps({
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "format_error": str(e),
            }, ensure_ascii=False)
