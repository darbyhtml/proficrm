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

# Базовый URL API smtp.bz (из документации Swagger UI)
SMTP_BZ_API_BASE = "https://api.smtp.bz/v1"


def get_quota_info(api_key: str) -> Optional[Dict[str, Any]]:
    """
    Получает информацию о тарифе и квоте через API smtp.bz.
    
    Args:
        api_key: API ключ для аутентификации
        
    Returns:
        Словарь с информацией о тарифе и квоте, или None в случае ошибки
        
    Пример ответа (предположительно):
    {
        "tariff": "FREE",
        "renewal_date": "2026-02-19",
        "emails_available": 14794,
        "emails_limit": 15000,
        "sent_per_hour": 6,
        "max_per_hour": 100,
    }
    """
    if not api_key:
        logger.warning("smtp.bz API key not provided")
        return None
    
    try:
        # Пробуем разные возможные эндпоинты API
        # Обычно это /account, /quota, /limits или /info
        endpoints_to_try = [
            "/account",
            "/quota",
            "/limits",
            "/info",
            "/account/quota",
            "/api/account",
            "/api/quota",
            "/api/limits",
            "/api/info",
            "/api/v1/account",
            "/api/v1/quota",
            "/api/v1/limits",
            "/api/v1/info",
            "/api/account/quota",
            "/api/account/info",
            "/api/account/limits",
            "/stats",
            "/api/stats",
            "/api/v1/stats",
            "/balance",
            "/api/balance",
            "/api/v1/balance",
        ]
        
        # Разные варианты аутентификации
        # Согласно документации: Authorization: API-KEY (где API-KEY - это сам ключ)
        auth_variants = [
            {"Authorization": api_key},  # Правильный формат согласно документации
            {"Authorization": f"API-KEY {api_key}"},  # Альтернативный вариант
            {"Authorization": f"Bearer {api_key}"},
            {"X-API-Key": api_key},
            {"Authorization": f"Token {api_key}"},
            {"X-Auth-Token": api_key},
            {"api_key": api_key},  # как query параметр
        ]
        
        headers_base = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        
        for endpoint in endpoints_to_try:
            # Пробуем разные варианты URL
            # Согласно документации: базовый URL https://api.smtp.bz/v1/
            url_variants = [
                f"{SMTP_BZ_API_BASE}{endpoint}",  # https://api.smtp.bz/v1/endpoint
                f"https://api.smtp.bz{endpoint}",  # https://api.smtp.bz/endpoint
            ]
            
            for url in url_variants:
                # Пробуем разные варианты аутентификации
                for auth_header in auth_variants:
                    headers = {**headers_base, **auth_header}
                    
                    try:
                        # Если api_key в auth_header, используем как query параметр
                        if "api_key" in auth_header:
                            response = requests.get(url, params={"api_key": api_key}, headers=headers_base, timeout=10)
                            auth_method = "query_param"
                        else:
                            response = requests.get(url, headers=headers, timeout=10)
                            auth_method = list(auth_header.keys())[0]
                        
                        logger.info(f"smtp.bz API: запрос {url} (auth: {auth_method}), статус: {response.status_code}")
                        
                        if response.status_code == 200:
                            try:
                                data = response.json()
                                logger.info(f"smtp.bz API: успешно получены данные с {url} (auth: {auth_method})")
                                logger.debug(f"smtp.bz API: ответ: {data}")
                                parsed = _parse_quota_response(data)
                                if parsed.get("emails_limit", 0) > 0 or parsed.get("tariff_name"):
                                    # Если получили хоть какие-то данные - считаем успехом
                                    return parsed
                                else:
                                    logger.debug(f"smtp.bz API: получен пустой ответ с {url}")
                            except ValueError as e:
                                # Не JSON ответ
                                logger.warning(f"smtp.bz API: ответ не JSON с {url}: {e}")
                                logger.warning(f"smtp.bz API: тело ответа: {response.text[:500]}")
                        elif response.status_code == 401:
                            logger.warning(f"smtp.bz API: неавторизован с {url} (auth: {auth_method})")
                            logger.warning(f"smtp.bz API: тело ответа: {response.text[:500]}")
                        elif response.status_code == 404:
                            logger.debug(f"smtp.bz API: эндпоинт не найден: {url}")
                        else:
                            logger.warning(f"smtp.bz API: статус {response.status_code} с {url} (auth: {auth_method})")
                            logger.warning(f"smtp.bz API: тело ответа: {response.text[:500]}")
                    except requests.exceptions.Timeout:
                        logger.warning(f"smtp.bz API: таймаут при запросе {url}")
                    except requests.exceptions.ConnectionError as e:
                        logger.warning(f"smtp.bz API: ошибка подключения к {url}: {e}")
                    except requests.exceptions.RequestException as e:
                        logger.warning(f"smtp.bz API: ошибка при запросе {url}: {e}")
        
        logger.warning("smtp.bz API: не удалось получить данные ни с одного эндпоинта. Проверьте правильность API ключа в личном кабинете smtp.bz")
        return None
        
    except Exception as e:
        logger.error(f"smtp.bz API: неожиданная ошибка при получении квоты: {e}", exc_info=True)
        return None


def _parse_quota_response(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Парсит ответ API smtp.bz в стандартизированный формат.
    
    Args:
        data: Сырой ответ от API
        
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
    
    # Пробуем разные варианты структуры ответа
    # Вариант 1: прямые поля
    if "tariff" in data or "tariff_name" in data:
        result["tariff_name"] = data.get("tariff") or data.get("tariff_name", "")
    
    if "renewal_date" in data or "renewalDate" in data:
        renewal_str = data.get("renewal_date") or data.get("renewalDate")
        if renewal_str:
            try:
                result["tariff_renewal_date"] = datetime.strptime(renewal_str, "%Y-%m-%d").date()
            except (ValueError, TypeError):
                pass
    
    if "emails_available" in data or "available" in data:
        result["emails_available"] = int(data.get("emails_available") or data.get("available", 0))
    
    if "emails_limit" in data or "limit" in data:
        result["emails_limit"] = int(data.get("emails_limit") or data.get("limit", 0))
    
    if "sent_per_hour" in data or "sentPerHour" in data:
        result["sent_per_hour"] = int(data.get("sent_per_hour") or data.get("sentPerHour", 0))
    
    if "max_per_hour" in data or "maxPerHour" in data:
        result["max_per_hour"] = int(data.get("max_per_hour") or data.get("maxPerHour", 100))
    
    # Вариант 2: вложенные объекты
    if "quota" in data:
        quota = data["quota"]
        result["emails_available"] = int(quota.get("available", result["emails_available"]))
        result["emails_limit"] = int(quota.get("limit", result["emails_limit"]))
    
    if "limits" in data:
        limits = data["limits"]
        result["sent_per_hour"] = int(limits.get("sent_per_hour", result["sent_per_hour"]))
        result["max_per_hour"] = int(limits.get("max_per_hour", result["max_per_hour"]))
    
    if "account" in data:
        account = data["account"]
        result["tariff_name"] = account.get("tariff", result["tariff_name"])
        if "renewal_date" in account:
            try:
                result["tariff_renewal_date"] = datetime.strptime(account["renewal_date"], "%Y-%m-%d").date()
            except (ValueError, TypeError):
                pass
    
    return result
