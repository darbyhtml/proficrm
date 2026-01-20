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
        # Эндпоинты API согласно Swagger UI документации
        # Базовый URL: https://api.smtp.bz/v1
        # Доступные эндпоинты:
        # - GET /user - Данные о пользователе (может содержать информацию о квоте)
        # - GET /user/stats - Статистика по рассылкам (может содержать лимиты)
        endpoints_to_try = [
            "/user",           # Данные о пользователе
            "/user/stats",     # Статистика по рассылкам
        ]
        
        # Разные варианты аутентификации
        # Согласно документации: ключ API передается в заголовке Authorization
        # Формат может быть: Authorization: <api_key> или Authorization: API-KEY <api_key>
        auth_variants = [
            {"Authorization": api_key},  # Просто ключ в заголовке
            {"Authorization": f"API-KEY {api_key}"},  # С префиксом API-KEY
            {"Authorization": f"Bearer {api_key}"},  # Bearer токен
        ]
        
        headers_base = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        
        # Ограничиваем количество попыток для ускорения
        max_attempts = 20  # Максимум 20 попыток
        attempts = 0
        
        for endpoint in endpoints_to_try:
            if attempts >= max_attempts:
                logger.warning(f"smtp.bz API: достигнут лимит попыток ({max_attempts}), прекращаем поиск")
                break
                
            # Пробуем разные варианты URL
            # Согласно документации: базовый URL https://api.smtp.bz/v1/
            url_variants = [
                f"{SMTP_BZ_API_BASE}{endpoint}",  # https://api.smtp.bz/v1/endpoint
                f"https://api.smtp.bz{endpoint}",  # https://api.smtp.bz/endpoint
            ]
            
            for url in url_variants:
                if attempts >= max_attempts:
                    break
                    
                # Пробуем только первые 2 варианта аутентификации (самые вероятные)
                for auth_header in auth_variants[:2]:  # Только Authorization: api_key и Authorization: API-KEY api_key
                    if attempts >= max_attempts:
                        break
                    
                    attempts += 1
                    headers = {**headers_base, **auth_header}
                    
                    try:
                        # Всегда используем заголовок Authorization
                        response = requests.get(url, headers=headers, timeout=5)
                        auth_method = list(auth_header.keys())[0]
                        
                        # Логируем только важные статусы
                        if response.status_code == 200:
                            try:
                                data = response.json()
                                logger.info(f"smtp.bz API: успешно получены данные с {url} (auth: {auth_method})")
                                logger.info(f"smtp.bz API: полный ответ API: {data}")  # Логируем на INFO для диагностики
                                
                                parsed = _parse_quota_response(data)
                                logger.info(f"smtp.bz API: распарсенные данные: {parsed}")
                                
                                # Если получили хоть какие-то данные - считаем успехом
                                if parsed.get("emails_limit", 0) > 0 or parsed.get("tariff_name") or parsed.get("emails_available", 0) > 0:
                                    return parsed
                                else:
                                    logger.warning(f"smtp.bz API: получен ответ, но нет данных о квоте. Структура: {list(data.keys())}")
                                    # Возвращаем хотя бы частично распарсенные данные
                                    return parsed
                            except ValueError as e:
                                # Не JSON ответ
                                logger.warning(f"smtp.bz API: ответ не JSON с {url}: {e}")
                                logger.warning(f"smtp.bz API: тело ответа: {response.text[:500]}")
                        elif response.status_code == 401:
                            # 401 означает, что эндпоинт существует, но нужна правильная аутентификация
                            logger.info(f"smtp.bz API: эндпоинт найден, но неавторизован: {url} (auth: {auth_method})")
                            logger.debug(f"smtp.bz API: тело ответа: {response.text[:200]}")
                        elif response.status_code == 404:
                            # 404 - эндпоинт не найден, не логируем каждый раз
                            pass
                        else:
                            logger.info(f"smtp.bz API: статус {response.status_code} с {url} (auth: {auth_method})")
                            logger.debug(f"smtp.bz API: тело ответа: {response.text[:200]}")
                    except requests.exceptions.Timeout:
                        logger.debug(f"smtp.bz API: таймаут при запросе {url}")
                    except requests.exceptions.ConnectionError as e:
                        logger.debug(f"smtp.bz API: ошибка подключения к {url}: {e}")
                    except requests.exceptions.RequestException as e:
                        logger.debug(f"smtp.bz API: ошибка при запросе {url}: {e}")
        
        logger.warning("smtp.bz API: не удалось получить данные ни с одного эндпоинта. Проверьте правильность API ключа в личном кабинете smtp.bz")
        return None
        
    except Exception as e:
        logger.error(f"smtp.bz API: неожиданная ошибка при получении квоты: {e}", exc_info=True)
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
