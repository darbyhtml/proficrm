"""
Сервис для работы с API smtp.bz.
Получение информации о тарифе, квоте и лимитах.
"""
import logging
import requests
from typing import Optional, Dict, Any
from django.utils import timezone
from datetime import datetime

logger = logging.getLogger(__name__)

# Базовый URL API smtp.bz (api_documentation_smtpbz.txt, Swagger)
SMTP_BZ_API_BASE = "https://api.smtp.bz/v1"
SMTP_BZ_TIMEOUT = 10
# По документации smtp.bz: ключ передаётся в заголовке Authorization (без указания схемы Bearer).
# https://docs.smtp.bz, раздел «Авторизация»: «Ключ необходимо передавать в каждом запросе в заголовке Authorization».
SMTP_BZ_AUTH_HEADER = "Authorization"


def _smtp_bz_request(
    api_key: str, endpoint: str, *, timeout: int = SMTP_BZ_TIMEOUT, params: Optional[Dict[str, Any]] = None
) -> tuple[int, Optional[dict], Optional[str]]:
    """
    Один GET-запрос к API smtp.bz. Authorization: {api_key} (ключ как значение заголовка, без Bearer).
    Returns: (status_code, json_data or None, error_message or None).
    """
    if not (api_key or "").strip():
        return 0, None, "no_api_key"
    base = SMTP_BZ_API_BASE.rstrip("/")
    path = endpoint if endpoint.startswith("/") else f"/{endpoint}"
    url = f"{base}{path}"
    headers = {
        "Accept": "application/json",
        SMTP_BZ_AUTH_HEADER: (api_key or "").strip(),
    }
    try:
        r = requests.get(url, headers=headers, timeout=timeout, params=params or None)
    except requests.exceptions.Timeout:
        logger.debug("smtp.bz API: timeout %s", endpoint)
        return 0, None, "timeout"
    except requests.exceptions.ConnectionError as e:
        logger.debug("smtp.bz API: connection error %s: %s", endpoint, e)
        return 0, None, "connection_error"
    except requests.exceptions.RequestException as e:
        logger.debug("smtp.bz API: request error %s: %s", endpoint, e)
        return 0, None, "request_error"

    if r.status_code == 200:
        try:
            return 200, r.json(), None
        except ValueError:
            logger.warning("smtp.bz API: ответ не JSON с %s, len=%s", endpoint, len(r.text))
            return 200, None, "invalid_json"
    if r.status_code in (401, 403):
        logger.debug("smtp.bz API: %s %s", r.status_code, endpoint)
        return r.status_code, None, "auth_error"
    if r.status_code == 404:
        return 404, None, "not_found"
    if r.status_code == 429:
        logger.warning("smtp.bz API: 429 rate limit %s", endpoint)
        return 429, None, "rate_limit"
    if 500 <= r.status_code < 600:
        logger.warning("smtp.bz API: %s %s", r.status_code, endpoint)
        return r.status_code, None, "server_error"
    logger.debug("smtp.bz API: %s %s", r.status_code, endpoint)
    return r.status_code, None, "http_error"


def get_quota_info(api_key: str) -> Optional[Dict[str, Any]]:
    """
    Получает информацию о тарифе и квоте через API smtp.bz.
    Один формат auth: Authorization: {api_key} (ключ как значение заголовка, без Bearer, по доке smtp.bz). Эндпоинты: /user, /user/stats.
    Обработка: 401/403/404/429/5xx, таймаут, не-JSON. Retry для 429/5xx (до 2 повторов, backoff 1–2 с).
    Не логируем ключ и полное тело ответа на INFO.
    """
    import time as _time
    if not (api_key or "").strip():
        logger.warning("smtp.bz API key not provided")
        return None
    for endpoint in ("/user", "/user/stats"):
        for attempt in range(3):
            status, data, err = _smtp_bz_request(api_key, endpoint, timeout=SMTP_BZ_TIMEOUT)
            if status == 200 and isinstance(data, dict):
                parsed = _parse_quota_response(data)
                if parsed.get("emails_limit", 0) > 0 or parsed.get("tariff_name") or parsed.get("emails_available", 0) > 0:
                    logger.debug("smtp.bz API: квота получена с %s", endpoint)
                    return parsed
                logger.debug("smtp.bz API: %s без полей квоты, ключи: %s", endpoint, list(data.keys())[:10])
                return parsed
            if status in (401, 403, 404):
                break
            if err in ("rate_limit", "server_error", "timeout", "connection_error") and attempt < 2:
                _time.sleep(1 + attempt)
                continue
            break
    logger.warning("smtp.bz API: не удалось получить квоту. Проверьте API ключ и доступность API.")
    return None


def _parse_quota_response(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Парсит ответ API smtp.bz в стандартизированный формат.
    
    Args:
        data: Сырой ответ от API (может быть от /user или /user/stats)
        
    Returns:
        Стандартизированный словарь с информацией о квоте
    """
    result = {
        "tariff_name": "",
        "tariff_renewal_date": None,
        "emails_available": 0,
        "emails_limit": 0,
        "sent_per_hour": 0,
        "max_per_hour": 100,
    }
    
    logger.debug(f"smtp.bz API: парсинг ответа, ключи: {list(data.keys())}")
    
    # Пробуем разные варианты структуры ответа
    # Вариант 1: прямые поля в корне ответа (формат smtp.bz API)
    # Согласно реальному ответу API: tarif, expires_quota, quota, tarif_quota, hsent, hlimit
    
    # Тариф
    if "tarif" in data:
        result["tariff_name"] = str(data.get("tarif", "")).upper()  # 'free' -> 'FREE'
    elif "tariff" in data or "tariff_name" in data or "plan" in data:
        result["tariff_name"] = data.get("tariff") or data.get("tariff_name") or data.get("plan", "")
    
    # Дата окончания квоты
    if "expires_quota" in data:
        renewal_str = data.get("expires_quota")
        if renewal_str:
            try:
                result["tariff_renewal_date"] = datetime.strptime(str(renewal_str)[:10], "%Y-%m-%d").date()
            except (ValueError, TypeError):
                pass
    elif "renewal_date" in data or "renewalDate" in data or "expires_at" in data:
        renewal_str = data.get("renewal_date") or data.get("renewalDate") or data.get("expires_at")
        if renewal_str:
            try:
                # Пробуем разные форматы даты
                for fmt in ["%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"]:
                    try:
                        result["tariff_renewal_date"] = datetime.strptime(str(renewal_str)[:10], "%Y-%m-%d").date()
                        break
                    except ValueError:
                        continue
            except (ValueError, TypeError):
                pass
    
    # Доступно писем (остаток квоты)
    # Согласно реальному ответу: quota = 14794 (доступно)
    if "quota" in data:
        quota_val = data.get("quota")
        if isinstance(quota_val, (int, float)):
            result["emails_available"] = int(quota_val)
        elif isinstance(quota_val, dict):
            result["emails_available"] = int(quota_val.get("available", quota_val.get("remaining", 0)))
    elif "emails_available" in data:
        result["emails_available"] = int(data.get("emails_available", 0))
    elif "available" in data:
        result["emails_available"] = int(data.get("available", 0))
    elif "remaining" in data:
        result["emails_available"] = int(data.get("remaining", 0))
    elif "left" in data:
        result["emails_available"] = int(data.get("left", 0))
    elif "balance" in data:
        result["emails_available"] = int(data.get("balance", 0))
    
    # Лимит писем (общая квота)
    # Согласно реальному ответу: tarif_quota = 15000 (общий лимит)
    if "tarif_quota" in data:
        result["emails_limit"] = int(data.get("tarif_quota", 0))
    elif "emails_limit" in data:
        result["emails_limit"] = int(data.get("emails_limit", 0))
    elif "limit" in data:
        result["emails_limit"] = int(data.get("limit", 0))
    elif "total" in data:
        result["emails_limit"] = int(data.get("total", 0))
    elif "monthly_limit" in data:
        result["emails_limit"] = int(data.get("monthly_limit", 0))
    elif "quota" in data and isinstance(data["quota"], dict):
        quota = data["quota"]
        result["emails_limit"] = int(quota.get("limit", quota.get("total", 0)))
    
    # Если нашли limit, но не нашли available, вычисляем как разницу
    if result["emails_limit"] > 0 and result["emails_available"] == 0:
        # Может быть, available = limit - sent
        if "sent" in data or "sent_today" in data or "sent_month" in data or "dsent" in data:
            sent = int(data.get("sent") or data.get("sent_today") or data.get("sent_month") or data.get("dsent", 0))
            result["emails_available"] = max(0, result["emails_limit"] - sent)
    
    # Отправлено за час
    # Согласно реальному ответу: hsent = 6
    if "hsent" in data:
        result["sent_per_hour"] = int(data.get("hsent", 0))
    elif "sent_per_hour" in data or "sentPerHour" in data or "sent_hour" in data:
        result["sent_per_hour"] = int(data.get("sent_per_hour") or data.get("sentPerHour") or data.get("sent_hour", 0))
    
    # Лимит в час
    # Согласно реальному ответу: hlimit = 100
    if "hlimit" in data:
        result["max_per_hour"] = int(data.get("hlimit", 100))
    elif "max_per_hour" in data or "maxPerHour" in data or "hourly_limit" in data or "rate_limit" in data:
        result["max_per_hour"] = int(data.get("max_per_hour") or data.get("maxPerHour") or data.get("hourly_limit") or data.get("rate_limit", 100))
    
    # Вариант 2: вложенные объекты
    if "quota" in data and isinstance(data["quota"], dict):
        quota = data["quota"]
        result["emails_available"] = int(quota.get("available", quota.get("remaining", result["emails_available"])))
        result["emails_limit"] = int(quota.get("limit", quota.get("total", result["emails_limit"])))
    
    if "limits" in data and isinstance(data["limits"], dict):
        limits = data["limits"]
        result["sent_per_hour"] = int(limits.get("sent_per_hour", limits.get("sent_hour", result["sent_per_hour"])))
        result["max_per_hour"] = int(limits.get("max_per_hour", limits.get("hourly_limit", limits.get("rate_limit", result["max_per_hour"]))))
    
    if "account" in data and isinstance(data["account"], dict):
        account = data["account"]
        result["tariff_name"] = account.get("tariff", account.get("plan", result["tariff_name"]))
        if "renewal_date" in account or "expires_at" in account:
            renewal_str = account.get("renewal_date") or account.get("expires_at")
            if renewal_str:
                try:
                    result["tariff_renewal_date"] = datetime.strptime(str(renewal_str)[:10], "%Y-%m-%d").date()
                except (ValueError, TypeError):
                    pass
    
    # Вариант 3: данные в /user/stats могут быть в другом формате
    if "stats" in data and isinstance(data["stats"], dict):
        stats = data["stats"]
        # Можем попытаться извлечь информацию из статистики
        if "quota" in stats:
            result["emails_limit"] = int(stats.get("quota", result["emails_limit"]))
        if "remaining" in stats:
            result["emails_available"] = int(stats.get("remaining", result["emails_available"]))
    
    logger.debug(f"smtp.bz API: распарсенные данные: {result}")
    return result


def get_message_info(api_key: str, message_id: str) -> Optional[Dict[str, Any]]:
    """
    Получает информацию о письме по его ID через API smtp.bz.
    
    Args:
        api_key: API ключ для аутентификации
        message_id: ID письма (Message-ID)
        
    Returns:
        Словарь с информацией о письме, или None в случае ошибки
        
    Пример ответа:
    {
        "status": "sent",  # sent, resent, return, bounce, cancel
        "error": "...",
        "bounce_reason": "...",
        ...
    }
    """
    if not api_key or not message_id:
        return None
    try:
        status, data, _ = _smtp_bz_request(api_key, f"/log/message/{message_id}", timeout=5)
        if status == 200 and isinstance(data, dict):
            return data
        if status in (400, 404):
            logger.debug("smtp.bz API: /log/message/... %s", status)
        return None
    except Exception as e:
        logger.error("smtp.bz API: ошибка get_message_info: %s", e, exc_info=True)
        return None


def get_message_logs(
    api_key: str,
    to_email: Optional[str] = None,
    from_email: Optional[str] = None,
    tag: Optional[str] = None,
    status: Optional[str] = None,
    is_open: Optional[bool] = None,
    is_unsubscribe: Optional[bool] = None,
    limit: int = 100,
    offset: int = 0,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Получает список писем через API smtp.bz с фильтрацией.
    
    Args:
        api_key: API ключ для аутентификации
        to_email: Email получателя (фильтр)
        from_email: Email отправителя (фильтр)
        tag: Идентификатор X-Tag (фильтр)
        status: Статус письма (sent, resent, return, bounce, cancel)
        is_open: Только открытые письма (True/False)
        is_unsubscribe: Только письма с отпиской (True/False)
        limit: Количество строк возврата
        offset: Шаг (пагинация)
        start_date: Дата от (формат 2020-01-01)
        end_date: Дата до (формат 2020-01-01)
        
    Returns:
        Словарь с данными о письмах, или None в случае ошибки
    """
    if not api_key:
        return None
    prm: Dict[str, Any] = {"limit": limit, "offset": offset}
    if to_email:
        prm["to"] = to_email
    if from_email:
        prm["from"] = from_email
    if tag:
        prm["tag"] = tag
    if status:
        prm["status"] = status
    if is_open is not None:
        prm["is_open"] = "true" if bool(is_open) else "false"
    if is_unsubscribe is not None:
        prm["is_unsubscribe"] = "true" if bool(is_unsubscribe) else "false"
    if start_date:
        prm["startDate"] = start_date
    if end_date:
        prm["endDate"] = end_date
    try:
        st, data, _ = _smtp_bz_request(api_key, "/log/message", params=prm, timeout=SMTP_BZ_TIMEOUT)
        if st == 200 and isinstance(data, dict):
            return data
        if st == 404:
            return {"data": [], "total": 0}
        return None
    except Exception as e:
        logger.error("smtp.bz API: ошибка get_message_logs: %s", e, exc_info=True)
        return None


def get_unsubscribers(
    api_key: str,
    *,
    limit: int = 200,
    offset: int = 0,
    address: Optional[str] = None,
    reason: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    GET /unsubscribe — получение списка отписчиков (smtp.bz).

    Параметры (query):
      - limit, offset
      - address (email)
      - reason: bounce, user, unsubscribe
    """
    if not api_key:
        return None
    prm: Dict[str, Any] = {"limit": limit, "offset": offset}
    if address:
        prm["address"] = address
    if reason:
        prm["reason"] = reason
    try:
        st, data, _ = _smtp_bz_request(api_key, "/unsubscribe", params=prm, timeout=SMTP_BZ_TIMEOUT)
        if st == 200 and isinstance(data, dict):
            return data
        if st == 404:
            return {"data": [], "total": 0}
        return None
    except Exception as e:
        logger.error("smtp.bz API: ошибка get_unsubscribers: %s", e, exc_info=True)
        return None
