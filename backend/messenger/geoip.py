"""
Определение региона по IP (GeoIP) для маршрутизации чатов.

Используется бесплатный API ip-api.com (лимит 45 запросов/мин с одного IP).
Для продакшена можно заменить на MaxMind GeoLite2 или платный сервис.
"""

from __future__ import annotations

import logging
from typing import Optional

from django.conf import settings

logger = logging.getLogger("messenger.widget")


def get_region_from_ip(ip: str) -> Optional["Region"]:
    """
    Определяет регион по IP-адресу.

    Сопоставляет с справочником companies.Region по полю name
    (regionName из API для России, иначе по имени региона).

    Args:
        ip: IPv4 или IPv6 адрес. Локальные (127.0.0.1, ::1) возвращают None.

    Returns:
        Region или None, если не удалось определить или сопоставить.
    """
    if not ip or not ip.strip():
        return None
    ip = ip.strip()
    if ip in ("127.0.0.1", "::1", "localhost"):
        return None

    # Опционально отключить GeoIP через настройки
    if not getattr(settings, "MESSENGER_GEOIP_ENABLED", True):
        return None

    try:
        import json
        import urllib.error
        import urllib.parse
        import urllib.request

        url = f"http://ip-api.com/json/{urllib.parse.quote(ip)}?fields=regionName,countryCode"
        req = urllib.request.Request(url, headers={"User-Agent": "ProfiCRM-Messenger/1.0"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        logger.warning("GeoIP request failed for %s: %s", ip[:20] + "..." if len(ip) > 20 else ip, e)
        return None

    region_name = (data.get("regionName") or "").strip()
    country_code = (data.get("countryCode") or "").strip().upper()
    if not region_name:
        return None

    from companies.models import Region

    # Совпадение по имени (без учёта регистра).
    # ip-api.com возвращает regionName на английском; в БД могут быть русские названия.
    # Для точного маппинга добавьте в справочник Region название на английском или настройте MESSENGER_GEOIP_REGION_MAPPING.
    region = Region.objects.filter(name__iexact=region_name).first()
    if region:
        return region
    # Опциональный маппинг из настроек: {"Sverdlovskaya Oblast": "Свердловская область", ...}
    mapping = getattr(settings, "MESSENGER_GEOIP_REGION_MAPPING", None)
    if isinstance(mapping, dict) and region_name in mapping:
        region = Region.objects.filter(name__iexact=mapping[region_name]).first()
        if region:
            return region
    return None
