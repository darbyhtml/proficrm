"""
Сервис для контроля rate limiting через Redis.
Обеспечивает строгое соблюдение лимита N писем/час.

Политика при недоступности Redis задаётся настройкой MAILER_RATE_LIMIT_FAIL_OPEN:
  True  (по умолчанию) — fail-open:  разрешить отправку, залогировать CRITICAL.
  False               — fail-closed: запретить отправку до восстановления Redis.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.core.cache import cache
from django.utils import timezone

from mailer.constants import RATE_LIMIT_CACHE_TTL_SECONDS

logger = logging.getLogger(__name__)


def _hour_ttl(now) -> int:
    """
    Возвращает TTL (сек) до конца текущего часа + 1 час запаса.
    Это предотвращает ситуацию, когда фиксированный TTL истекает
    раньше смены часа и сбрасывает счётчик раньше времени.
    """
    seconds_remaining_in_hour = (60 - now.minute) * 60 + (60 - now.second)
    return seconds_remaining_in_hour + 3600  # остаток часа + буфер


def _touch_safe(key: str, ttl: int) -> None:
    """cache.touch() с перехватом ошибок — дрейф TTL не критичен."""
    try:
        cache.touch(key, timeout=ttl)
    except Exception:
        logger.debug("Rate limiter: cache.touch() failed for key %s (non-critical)", key)


def reserve_rate_limit_token(max_per_hour: int = 100) -> tuple[bool, int, timezone.datetime | None]:
    """
    Атомарно резервирует токен для отправки письма.

    Returns:
        (reserved, current_count, next_reset_at)
        - reserved:      можно ли отправлять
        - current_count: счётчик после инкремента
        - next_reset_at: время сброса, если reserved=False
    """
    from django.conf import settings

    fail_open: bool = getattr(settings, "MAILER_RATE_LIMIT_FAIL_OPEN", True)

    now = timezone.now()
    hour_key = f"mailer:rate:hour:{now.strftime('%Y-%m-%d:%H')}"
    ttl = _hour_ttl(now)

    try:
        # Атомарный INCR
        try:
            new_value = cache.incr(hour_key)
            _touch_safe(hour_key, ttl)
        except ValueError:
            # Ключ не существует — создаём атомарно
            if cache.add(hour_key, 1, timeout=ttl):
                return True, 1, None
            # Ключ уже создан другим процессом
            new_value = cache.incr(hour_key)
            _touch_safe(hour_key, ttl)

        if new_value > max_per_hour:
            # Откатываем превышение
            try:
                cache.decr(hour_key)
            except Exception:
                logger.warning(
                    "Rate limiter: DECR failed for key %s, counter may drift by 1", hour_key
                )
            next_hour = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            return False, new_value - 1, next_hour

        return True, new_value, None

    except Exception as exc:
        if fail_open:
            logger.critical(
                "RATE LIMITER UNAVAILABLE: Redis недоступен. "
                "Отправка разрешена (fail-open, MAILER_RATE_LIMIT_FAIL_OPEN=True). "
                "Возможно превышение лимита %d писем/час.",
                max_per_hour,
                exc_info=True,
                extra={"error_type": "rate_limiter_backend_error", "policy": "fail_open"},
            )
            return True, 0, None
        else:
            logger.critical(
                "RATE LIMITER UNAVAILABLE: Redis недоступен. "
                "Отправка ЗАБЛОКИРОВАНА (fail-closed, MAILER_RATE_LIMIT_FAIL_OPEN=False).",
                exc_info=True,
                extra={"error_type": "rate_limiter_backend_error", "policy": "fail_closed"},
            )
            next_retry = now + timedelta(minutes=1)
            return False, 0, next_retry


def increment_rate_limit_per_hour(max_per_hour: int = 100) -> tuple[bool, int]:
    """Атомарно увеличивает счётчик (без проверки лимита — используется для аудита)."""
    now = timezone.now()
    hour_key = f"mailer:rate:hour:{now.strftime('%Y-%m-%d:%H')}"
    ttl = _hour_ttl(now)

    try:
        try:
            new_value = cache.incr(hour_key)
            _touch_safe(hour_key, ttl)
            return True, new_value
        except ValueError:
            if cache.add(hour_key, 1, timeout=ttl):
                return True, 1
            new_value = cache.incr(hour_key)
            _touch_safe(hour_key, ttl)
            return True, new_value
    except Exception as e:
        logger.error("Rate limiter: increment failed: %s", e, exc_info=True)
        return False, 0


_QUOTA_CACHE_KEY = "mailer:effective_quota_available"
_QUOTA_CACHE_TTL = 30  # секунд


def get_effective_quota_available() -> int:
    """
    Вычисляет эффективную доступную квоту с учётом локальных отправок.
    Кешируется на 30 секунд.
    """
    try:
        cached = cache.get(_QUOTA_CACHE_KEY)
        if cached is not None:
            return cached
    except Exception:
        pass  # fail-open: пересчитаем

    from mailer.models import SmtpBzQuota, SendLog

    quota = SmtpBzQuota.load()

    if not quota.last_synced_at or quota.sync_error:
        return 15000  # Дефолт при отсутствии данных

    emails_available = quota.emails_available or 0
    sync_time = quota.last_synced_at
    local_sent = SendLog.objects.filter(
        provider="smtp_global",
        status=SendLog.Status.SENT,
        created_at__gte=sync_time,
    ).count()

    effective = max(0, emails_available - local_sent)

    try:
        cache.set(_QUOTA_CACHE_KEY, effective, timeout=_QUOTA_CACHE_TTL)
    except Exception:
        pass

    return effective
