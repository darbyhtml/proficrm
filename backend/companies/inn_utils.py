import re
from typing import Iterable, List


# ИНН в РФ: чаще всего 10 (юрлица) или 12 (ИП/физлица) цифр.
_INN_RE = re.compile(r"\b(\d{10}|\d{12})\b")


def parse_inns(value: str | None) -> List[str]:
    """
    Извлекает ИНН из произвольной строки.
    Поддерживает ввод через пробелы/запятые/переносы и т.п.
    Возвращает список уникальных ИНН, сохраняя порядок.
    """
    if value is None:
        return []
    s = str(value).strip()
    if not s:
        return []

    inns: List[str] = []
    seen = set()
    for m in _INN_RE.finditer(s):
        inn = m.group(1)
        if inn not in seen:
            inns.append(inn)
            seen.add(inn)
    return inns


def format_inns(inns: Iterable[str]) -> str:
    """
    Приводит список ИНН к строке хранения.
    Разделитель выбран так, чтобы в UI читалось и легко копировалось.
    """
    cleaned: List[str] = []
    seen = set()
    for x in inns:
        v = str(x or "").strip()
        if not v:
            continue
        if v not in seen:
            cleaned.append(v)
            seen.add(v)
    return ", ".join(cleaned)


def normalize_inn_string(value: str | None) -> str:
    """Нормализует строку ИНН в формат хранения (несколько ИНН через запятую)."""
    return format_inns(parse_inns(value))


def merge_inn_strings(existing: str | None, incoming: str | None) -> str:
    """Сливает ИНН из existing и incoming, сохраняя порядок: existing -> incoming."""
    a = parse_inns(existing)
    b = parse_inns(incoming)
    return format_inns([*a, *b])

