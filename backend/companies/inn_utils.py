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

    # Сначала пробуем найти ИНН через regex (для случаев, когда ИНН уже отделены)
    inns: List[str] = []
    seen = set()
    for m in _INN_RE.finditer(s):
        inn = m.group(1)
        if inn not in seen:
            inns.append(inn)
            seen.add(inn)
    
    # Если не нашли через regex, убираем все нецифровые символы и ищем последовательности
    if not inns:
        digits_only = ''.join(c for c in s if c.isdigit())
        # Ищем последовательности из 10 или 12 цифр
        for length in [12, 10]:  # Сначала 12, потом 10 (чтобы не находить часть 12-значного как 10-значный)
            i = 0
            while i <= len(digits_only) - length:
                candidate = digits_only[i:i + length]
                if candidate not in seen:
                    inns.append(candidate)
                    seen.add(candidate)
                    i += length  # Пропускаем найденный ИНН
                else:
                    i += 1
        # Если ничего не нашли (например, 9 цифр "901000327"), но строка — только цифры
        # и длина типична для ИНН/кодов (8–12 цифр), сохраняем как одно значение, чтобы не терять ввод
        if not inns and digits_only and 8 <= len(digits_only) <= 12:
            inns.append(digits_only)
    
    return inns


def format_inns(inns: Iterable[str]) -> str:
    """
    Приводит список ИНН к строке хранения.
    Разделитель "/" для единообразия с КПП и удобства чтения.
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
    return " / ".join(cleaned)


def normalize_inn_string(value: str | None) -> str:
    """Нормализует строку ИНН в формат хранения (несколько ИНН через запятую)."""
    return format_inns(parse_inns(value))


def merge_inn_strings(existing: str | None, incoming: str | None) -> str:
    """Сливает ИНН из existing и incoming, сохраняя порядок: existing -> incoming."""
    a = parse_inns(existing)
    b = parse_inns(incoming)
    return format_inns([*a, *b])

