"""
Утилиты для работы с регионами: нормализация названий, поиск по алиасам.
"""
from companies.models import Region


# Словарь алиасов для нормализации названий регионов из amoCRM
REGION_ALIASES = {
    "Республика Башкирия": "Республика Башкортостан",
    "Башкирия": "Республика Башкортостан",
    "Башкортостан": "Республика Башкортостан",
    "Республика Удмуртия": "Удмуртская Республика",
    "Удмуртия": "Удмуртская Республика",
    "Ненецкий-автономный округ": "Ненецкий автономный округ",
    "Ненецкий автономный округ": "Ненецкий автономный округ",
    # Аббревиатуры автономных округов
    "ХМАО": "Ханты-Мансийский автономный округ — Югра",
    "Ханты-Мансийский АО": "Ханты-Мансийский автономный округ — Югра",
    "Ханты-Мансийский автономный округ": "Ханты-Мансийский автономный округ — Югра",
    "ЯНАО": "Ямало-Ненецкий автономный округ",
    "Ямало-Ненецкий АО": "Ямало-Ненецкий автономный округ",
    "Ямало-Ненецкий автономный округ": "Ямало-Ненецкий автономный округ",
    # Можно добавить другие частые несовпадения по мере обнаружения
}


def normalize_region_name(label: str) -> str:
    """
    Нормализует название региона из amoCRM к стандартному названию в БД.
    """
    label = label.strip()
    # Сначала проверяем точное совпадение (с учётом регистра)
    if label in REGION_ALIASES:
        return REGION_ALIASES[label]
    # Проверяем без учёта регистра
    for alias, canonical in REGION_ALIASES.items():
        if alias.lower() == label.lower():
            return canonical
    return label


def find_region_by_name(label: str) -> Region | None:
    """
    Находит регион по названию с учётом нормализации и алиасов.
    Используется при импорте из amoCRM для корректного сопоставления регионов.
    """
    label = label.strip()
    if not label:
        return None
    
    # Сначала пробуем точное совпадение (case-insensitive)
    region = Region.objects.filter(name__iexact=label).first()
    if region:
        return region
    
    # Пробуем нормализованное название через алиасы
    normalized = normalize_region_name(label)
    if normalized != label:
        region = Region.objects.filter(name__iexact=normalized).first()
        if region:
            return region
    
    # Пробуем с заменой дефисов на пробелы и наоборот (для автономных округов)
    # "Ненецкий-автономный округ" -> "Ненецкий автономный округ"
    label_with_spaces = label.replace("-", " ")
    if label_with_spaces != label:
        region = Region.objects.filter(name__iexact=label_with_spaces).first()
        if region:
            return region
    
    label_with_dash = label.replace(" ", "-")
    if label_with_dash != label:
        region = Region.objects.filter(name__iexact=label_with_dash).first()
        if region:
            return region
    
    # Пробуем частичное совпадение (если label содержит часть названия региона)
    # Например, "ХМАО" -> "Ханты-Мансийский автономный округ — Югра"
    if len(label) < 15:  # Короткие названия могут быть аббревиатурами или неполными
        regions = Region.objects.filter(name__icontains=label)
        if regions.count() == 1:
            return regions.first()
        # Если несколько, пробуем более точное совпадение - начало названия
        regions = Region.objects.filter(name__istartswith=label)
        if regions.count() == 1:
            return regions.first()
    
    return None
