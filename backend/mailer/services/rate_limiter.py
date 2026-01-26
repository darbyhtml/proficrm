"""
Сервис для контроля rate limiting через Redis.
Обеспечивает строгое соблюдение лимита 100 писем/час.
"""
from __future__ import annotations

import logging
from django.utils import timezone
from django.core.cache import cache

logger = logging.getLogger(__name__)


def reserve_rate_limit_token(max_per_hour: int = 100) -> tuple[bool, int, timezone.datetime | None]:
    """
    Атомарно резервирует токен для отправки письма (reserve → send → commit схема).
    Использует атомарный Redis INCR для гарантии, что два воркера не могут одновременно
    получить токен, если лимит уже достигнут.
    
    Args:
        max_per_hour: Максимальное количество писем в час
        
    Returns:
        (reserved, current_count, next_reset_at)
        - reserved: Успешно ли зарезервирован токен (True = можно отправлять)
        - current_count: Текущее значение после резервации (если reserved=True)
        - next_reset_at: Время сброса счетчика (начало следующего часа), если reserved=False
    
    ENTERPRISE NOTE: Ключ Redis будет иметь формат "crm:mailer:rate:hour:YYYY-MM-DD:HH"
    (KEY_PREFIX="crm" добавляется автоматически Django Redis в production).
    """
    now = timezone.now()
    hour_key = f"mailer:rate:hour:{now.strftime('%Y-%m-%d:%H')}"
    
    try:
        # Атомарно увеличиваем счетчик
        try:
            new_value = cache.incr(hour_key)
            # Устанавливаем TTL при первом создании
            if new_value == 1:
                cache.touch(hour_key, timeout=7200)
            else:
                cache.touch(hour_key, timeout=7200)
            
            # Проверяем лимит ПОСЛЕ атомарного увеличения
            if new_value > max_per_hour:
                # Превысили лимит - откатываем (DECR)
                try:
                    cache.decr(hour_key)
                except Exception:
                    pass
                # Вычисляем время сброса
                from datetime import timedelta
                next_hour = (now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1))
                return False, new_value - 1, next_hour
            
            return True, new_value, None
        except ValueError:
            # Ключ не существует - создаем атомарно
            if cache.add(hour_key, 1, timeout=7200):
                # Проверяем лимит (1 <= max_per_hour всегда True, но для консистентности)
                if 1 > max_per_hour:
                    cache.delete(hour_key)
                    from datetime import timedelta
                    next_hour = (now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1))
                    return False, 0, next_hour
                return True, 1, None
            else:
                # Ключ уже создан другим процессом - используем incr
                new_value = cache.incr(hour_key)
                cache.touch(hour_key, timeout=7200)
                if new_value > max_per_hour:
                    try:
                        cache.decr(hour_key)
                    except Exception:
                        pass
                    from datetime import timedelta
                    next_hour = (now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1))
                    return False, new_value - 1, next_hour
                return True, new_value, None
    except Exception as e:
        # ENTERPRISE: FAIL-OPEN при ошибке Redis (availability-first для rate limiter)
        # Логируем ERROR для alerting, но разрешаем отправку
        logger.error(
            "Error reserving rate limit token (Redis unavailable), allowing send (fail-open)",
            exc_info=True,
            extra={
                "error_type": "rate_limiter_backend_error",
                "policy": "fail_open",  # Для мониторинга
            }
        )
        # При ошибке Redis разрешаем отправку (fail-open для availability)
        # TODO: Alert через monitoring system
        return True, 0, None


def check_rate_limit_per_hour(max_per_hour: int = 100) -> tuple[bool, int, timezone.datetime | None]:
    """
    DEPRECATED: Используйте reserve_rate_limit_token() для атомарной резервации.
    Оставлено для обратной совместимости, но не рекомендуется к использованию
    из-за race condition между проверкой и increment.
    
    Проверяет, не превышен ли лимит отправки писем в час.
    
    Args:
        max_per_hour: Максимальное количество писем в час
        
    Returns:
        (can_send, current_count, next_reset_at)
        - can_send: Можно ли отправить письмо сейчас
        - current_count: Текущее количество отправленных писем в этом часе
        - next_reset_at: Время сброса счетчика (начало следующего часа)
    """
    now = timezone.now()
    hour_key = f"mailer:rate:hour:{now.strftime('%Y-%m-%d:%H')}"
    
    try:
        # Получаем текущее значение (если ключа нет, вернется 0)
        current = cache.get(hour_key, 0)
        
        # Если лимит достигнут
        if current >= max_per_hour:
            # Вычисляем время сброса (начало следующего часа)
            from datetime import timedelta
            next_hour = (now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1))
            return False, int(current), next_hour
        
        return True, int(current), None
    except Exception as e:
        logger.error(f"Error checking rate limit: {e}", exc_info=True)
        # При ошибке Redis разрешаем отправку (fail-open)
        return True, 0, None


def increment_rate_limit_per_hour(max_per_hour: int = 100) -> tuple[bool, int]:
    """
    Атомарно увеличивает счетчик отправленных писем в час.
    Использует Redis INCR для гарантии атомарности.
    
    Args:
        max_per_hour: Максимальное количество писем в час
        
    Returns:
        (success, current_count)
        - success: Успешно ли увеличен счетчик
        - current_count: Текущее значение после увеличения
    """
    now = timezone.now()
    hour_key = f"mailer:rate:hour:{now.strftime('%Y-%m-%d:%H')}"
    
    try:
        # Атомарно увеличиваем счетчик через incr
        # Если ключа нет, incr создаст его со значением 1
        try:
            new_value = cache.incr(hour_key)
            # Устанавливаем TTL при первом создании или обновляем существующий
            if new_value == 1:
                # Первое письмо - устанавливаем TTL на 2 часа (запас)
                cache.touch(hour_key, timeout=7200)
            else:
                # Обновляем TTL на случай, если ключ уже существовал
                cache.touch(hour_key, timeout=7200)
            return True, new_value
        except ValueError:
            # Ключ не существует и incr не может его создать (некоторые backends)
            # Используем add для атомарного создания
            if cache.add(hour_key, 1, timeout=7200):
                return True, 1
            else:
                # Ключ уже создан другим процессом, используем incr
                new_value = cache.incr(hour_key)
                cache.touch(hour_key, timeout=7200)
                return True, new_value
    except Exception as e:
        logger.error(f"Error incrementing rate limit: {e}", exc_info=True)
        return False, 0


def get_effective_quota_available() -> int:
    """
    Вычисляет эффективную доступную квоту с учетом локальных отправок.
    
    Учитывает:
    - emails_available из SmtpBzQuota
    - Локальные отправки с момента последнего sync
    
    Returns:
        Эффективная доступная квота (не может быть отрицательной)
    """
    from mailer.models import SmtpBzQuota, SendLog
    
    quota = SmtpBzQuota.load()
    
    # Если квота не синхронизирована, используем дефолтное значение
    if not quota.last_synced_at or quota.sync_error:
        return 15000  # Дефолт
    
    emails_available = quota.emails_available or 0
    
    # Подсчитываем локальные отправки с момента последнего sync
    sync_time = quota.last_synced_at
    local_sent = SendLog.objects.filter(
        provider="smtp_global",
        status="sent",
        created_at__gte=sync_time
    ).count()
    
    # Эффективная квота = доступная - отправленные локально
    effective = emails_available - local_sent
    
    # Не может быть отрицательной
    return max(0, effective)
