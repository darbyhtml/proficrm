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
        ]
        
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        
        # Также пробуем с X-API-Key заголовком
        headers_alt = {
            "X-API-Key": api_key,
            "Content-Type": "application/json",
        }
        
        for endpoint in endpoints_to_try:
            url = f"{SMTP_BZ_API_BASE}{endpoint}"
            
            # Пробуем с Bearer токеном
            try:
                response = requests.get(url, headers=headers, timeout=10)
                if response.status_code == 200:
                    data = response.json()
                    logger.info(f"smtp.bz API: успешно получены данные с {endpoint}")
                    return _parse_quota_response(data)
            except requests.exceptions.RequestException as e:
                logger.debug(f"smtp.bz API: ошибка при запросе {endpoint} с Bearer: {e}")
            
            # Пробуем с X-API-Key
            try:
                response = requests.get(url, headers=headers_alt, timeout=10)
                if response.status_code == 200:
                    data = response.json()
                    logger.info(f"smtp.bz API: успешно получены данные с {endpoint} (X-API-Key)")
                    return _parse_quota_response(data)
            except requests.exceptions.RequestException as e:
                logger.debug(f"smtp.bz API: ошибка при запросе {endpoint} с X-API-Key: {e}")
        
        logger.warning("smtp.bz API: не удалось получить данные ни с одного эндпоинта")
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
