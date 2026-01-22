import re


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

