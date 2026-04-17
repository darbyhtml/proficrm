import re
from datetime import datetime, timedelta


RUS_TZ_CHOICES: list[tuple[str, str]] = [
    ("Europe/Kaliningrad", "Калининград (UTC+02)"),
    ("Europe/Moscow", "Москва / СПБ (UTC+03)"),
    ("Europe/Samara", "Самара (UTC+04)"),
    ("Asia/Yekaterinburg", "Екатеринбург / Тюмень (UTC+05)"),
    ("Asia/Omsk", "Омск (UTC+06)"),
    ("Asia/Novosibirsk", "Новосибирск (UTC+07)"),
    ("Asia/Krasnoyarsk", "Красноярск (UTC+07)"),
    ("Asia/Irkutsk", "Иркутск (UTC+08)"),
    ("Asia/Yakutsk", "Якутск (UTC+09)"),
    ("Asia/Vladivostok", "Владивосток (UTC+10)"),
    ("Asia/Sakhalin", "Сахалин (UTC+11)"),
    ("Asia/Magadan", "Магадан (UTC+11)"),
    ("Asia/Kamchatka", "Камчатка (UTC+12)"),
]


def guess_ru_timezone_from_address(address: str) -> str:
    """
    Эвристика определения часового пояса по адресу (Россия).
    Если не удалось — вернёт пустую строку (ничего не подставляем).
    """
    s = (address or "").strip().lower()
    if not s:
        return ""

    s = s.replace("ё", "е")
    s = re.sub(r"[\.,;:()\[\]{}]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()

    # Ключевые слова -> TZ. Проверяем "восток" первым.
    rules: list[tuple[str, list[str]]] = [
        ("Asia/Kamchatka", ["камчат", "петропавловск", "камчатский край"]),
        ("Asia/Magadan", ["магадан"]),
        ("Asia/Sakhalin", ["сахалин", "южно-сахалинск"]),
        ("Asia/Vladivostok", ["владивосток", "примор", "хабаров", "амур", "благовещенск", "еврейская автоном", "биробиджан"]),
        ("Asia/Yakutsk", ["якут", "саха", "нерюнгри", "алдан", "ленск"]),
        ("Asia/Irkutsk", ["иркут", "улан-удэ", "бурят", "чита", "забайкал", "прибайкал"]),
        ("Asia/Krasnoyarsk", ["краснояр", "хакас", "абакан", "тыва", "тува", "кызыл"]),
        ("Asia/Novosibirsk", ["новосибир", "томск", "кемеров", "кузбасс", "алтай", "барнаул", "горно-алтайск", "республика алтай"]),
        ("Asia/Omsk", ["омск"]),
        ("Asia/Yekaterinburg", ["екатеринбург", "свердлов", "тюмен", "ханты-мансий", "юмр", "ямало-ненец", "курган", "челябин", "перм", "удмурт", "ижевск", "оренбург"]),
        ("Europe/Samara", ["самар", "ульянов", "тольятти", "сызрань", "саратов", "татарстан", "казань"]),
        ("Europe/Kaliningrad", ["калининград"]),
    ]

    for tz, keys in rules:
        for k in keys:
            if k and k in s:
                return tz

    has_cyrillic = bool(re.search(r"[а-я]", s))
    if has_cyrillic:
        return "Europe/Moscow"
    return ""


# ---------------------------------------------------------------------------
# F2 cross-cutting: единый источник правды для «сегодня/завтра» в локальной TZ.
# Использовать ВМЕСТО timezone.now() для фильтров «просрочено/сегодня/неделя» —
# это устраняет TZ-рассогласование между Dashboard (локальное) и Tasks/Company
# (было UTC). См. F2-interconnections-2026-04-17.md раздел 2.3.
# ---------------------------------------------------------------------------

def local_today_start() -> datetime:
    """Начало текущего дня в локальной TZ пользователя.

    Возвращает tz-aware datetime (astimezone в активной TZ через Django).
    Эквивалент `timezone.localtime(timezone.now()).replace(hour=0, ...)`.

    Используется для фильтров `due_at < today_start` = "просрочено".
    """
    from django.utils import timezone as dj_tz
    local_now = dj_tz.localtime(dj_tz.now())
    return local_now.replace(hour=0, minute=0, second=0, microsecond=0)


def local_tomorrow_start() -> datetime:
    """Начало завтрашнего дня в локальной TZ. Для фильтра «сегодня» (< tomorrow_start)."""
    return local_today_start() + timedelta(days=1)


def local_week_range_start() -> datetime:
    """Начало 7-дневного диапазона «на неделю» — завтра 00:00."""
    return local_tomorrow_start()


def local_week_range_end() -> datetime:
    """Конец 7-дневного диапазона «на неделю» — через 7 дней после завтра."""
    return local_tomorrow_start() + timedelta(days=7)

