"""
Единый слой нормализации данных для компаний и контактов.

Этот модуль обеспечивает единообразную нормализацию данных во всех точках входа:
- Django Forms (UI)
- DRF Serializers (API)
- Model.save() методы
- Импорты и миграции
- Celery задачи

Все нормализаторы должны быть идемпотентными и безопасными (не терять данные).
"""
import re

# Импортируем существующие утилиты
from .inn_utils import normalize_inn_string as _normalize_inn_string
# Импорт из core/ для избежания циклических зависимостей
# (companies -> ui -> companies может вызвать цикл)
from core.work_schedule_utils import normalize_work_schedule as _normalize_work_schedule

# Константы для валидации телефонов (из amocrm/migrate.py)
MIN_PHONE_DIGITS = 10  # Минимум цифр для валидного телефона (для РФ номеров: 10 цифр без кода страны)
MAX_PHONE_DIGITS = 15  # Максимум цифр (E.164 стандарт)

# Ключевые фразы, которые указывают на инструкции, а не на телефон
PHONE_INSTRUCTION_KEYWORDS = [
    "только через", "приемн", "мини атс", "миниатс", "атс", "перевести",
    "доб.", "доб ", "внутр.", "внутр ", "extension", "ext ", "ext.",
    "затем", "доп.", "доп ", "через", "через ", "call", "звонок",
    "добавочный", "внутренний", "попросить", "соединить", "приемная",
    "вн.", "вн ", "добав.", "добав ",
]

# Паттерны для извлечения extension/доб из телефона
EXTENSION_PATTERNS = [
    r'доб\.?\s*(\d+)',
    r'доб\s+(\d+)',
    r'внутр\.?\s*(\d+)',
    r'внутр\s+(\d+)',
    r'ext\.?\s*(\d+)',
    r'ext\s+(\d+)',
    r'extension\s+(\d+)',
    r'затем\s+(\d+)',
    r'доп\.?\s*(\d+)',
    r'доп\s+(\d+)',
    r'#(\d+)',  # #123
    r'x(\d+)',  # x123
]


def normalize_phone(raw: str | None) -> str:
    """
    Нормализует номер телефона к формату E.164 (+7XXXXXXXXXX).
    
    Удаляет пробелы/скобки/дефисы, распознает +7/8 для РФ, приводит к E.164.
    Если после очистки цифр < MIN_PHONE_DIGITS - возвращает исходную строку (обрезанную до 50 символов).
    Если строка содержит валидный номер + дополнение ("доб. 4") - извлекает только номер.
    
    ВАЖНО: Extension (доб./ext.) извлекается из строки, но НЕ возвращается в результате.
    Текущая реализация хранит только основной номер в поле `phone` (max_length=50).
    Если нужно хранить extension отдельно, рассмотрите:
    - Добавление поля `phone_ext` в модель
    - Или хранение в JSON поле как `{e164: "...", ext: "..."}`
    - Или использование поля `phone_comment` для extension
    
    Безопасно обрабатывает None, пустые строки, не-строки.
    
    Args:
        raw: Исходная строка с телефоном (может быть None)
        
    Returns:
        str: Нормализованный номер в формате E.164 (без extension) или исходная строка (обрезанная до 50 символов)
    """
    if raw is None:
        return ""
    
    if not isinstance(raw, str):
        return str(raw)[:50] if raw else ""
    
    original = raw.strip()
    if not original:
        return ""
    
    # Проверяем на инструкции (если нет валидного номера)
    original_lower = original.lower()
    has_instruction_keywords = any(kw in original_lower for kw in PHONE_INSTRUCTION_KEYWORDS)
    
    # Извлекаем extension/доб из исходной строки
    ext_value = None
    note_parts = []
    cleaned_phone = original
    
    for pattern in EXTENSION_PATTERNS:
        match = re.search(pattern, original, re.IGNORECASE)
        if match:
            ext_value = match.group(1)
            # Удаляем extension из строки телефона
            cleaned_phone = re.sub(pattern, '', cleaned_phone, flags=re.IGNORECASE).strip()
            break
    
    # Если есть "затем" или другие инструкции после номера - извлекаем в note
    instruction_patterns = [
        r'(.+?)\s+(затем|доб\.?|доб|внутр\.?|внутр|ext\.?|ext|extension|доп\.?|доп)\s+(\d+)',
    ]
    for pattern in instruction_patterns:
        match = re.search(pattern, original, re.IGNORECASE)
        if match:
            phone_part = match.group(1).strip()
            instruction = match.group(2).strip()
            ext_num = match.group(3).strip()
            if not ext_value:
                ext_value = ext_num
            note_parts.append(f"{instruction} {ext_num}")
            cleaned_phone = phone_part
            break
    
    # Нормализуем номер телефона
    # Поддержка форматов: "+7 345 2540415", "8-816-565-49-58", "8923-...", "(38473)3-33-92"
    has_brackets = '(' in cleaned_phone and ')' in cleaned_phone
    
    if has_brackets:
        # Формат (38473)3-33-92 или (495)123-45-67
        bracket_match = re.search(r'\((\d+)\)(.+)', cleaned_phone)
        if bracket_match:
            city_code = bracket_match.group(1)
            number_part = bracket_match.group(2)
            phone_digits = city_code + ''.join(c for c in number_part if c.isdigit())
        else:
            phone_digits = ''.join(c for c in cleaned_phone if c.isdigit() or c == '+')
    else:
        phone_digits = ''.join(c for c in cleaned_phone if c.isdigit() or c == '+')
    
    # КРИТИЧНО: Обрезка "хвоста" ДО любых проверок валидности и преобразований
    # Если есть "хвост" без слов ext/доб, но он короткий — считаем его extension и отбрасываем.
    # Пример: 7 999 123 45 67 8901 -> основной номер 11 цифр + ext 8901
    digits_only = ''.join(c for c in phone_digits if c.isdigit())
    if len(digits_only) > 11:
        if digits_only[0] in ("7", "8"):
            main = digits_only[:11]
            tail = digits_only[11:]
            if 1 <= len(tail) <= 6:
                # Короткий хвост - считаем extension, отбрасываем
                digits_only = main
    
    # КРИТИЧНО: RU-нормализация (применяем правила после обрезки хвоста)
    # digits = только цифры (без форматирования)
    digits = digits_only
    
    if not digits:
        return original[:50]
    
    # 8XXXXXXXXXX... -> 7XXXXXXXXXX (берем первые 11)
    if digits.startswith("8") and len(digits) >= 11:
        digits = "7" + digits[1:11]
    
    # 7XXXXXXXXXX... -> берем первые 11
    elif digits.startswith("7") and len(digits) >= 11:
        digits = digits[:11]
    
    # 9XXXXXXXXX... (часто вводят без 7/8) -> берем первые 10 и префиксуем 7
    elif digits.startswith("9") and len(digits) >= 10:
        digits = "7" + digits[:10]
    
    # ровно 10 цифр -> префиксуем 7
    elif len(digits) == 10:
        digits = "7" + digits
    
    # Проверяем финальную валидность
    if len(digits) < MIN_PHONE_DIGITS or len(digits) > MAX_PHONE_DIGITS:
        # Если невалидный номер - возвращаем исходную строку (обрезанную)
        return original[:50]
    
    # КРИТИЧНО: Возвращаем только E.164 формат (+ и цифры, без форматирования)
    e164 = f"+{digits}"
    
    # Обрезаем до 50 символов (максимальная длина поля phone в модели)
    return e164[:50]


def normalize_inn(value: str | None) -> str:
    """
    Нормализует строку ИНН в формат хранения (несколько ИНН через " / ").
    
    Args:
        value: Исходная строка с ИНН (может быть None)
        
    Returns:
        str: Нормализованная строка ИНН
    """
    return _normalize_inn_string(value)


def normalize_work_schedule(text: str | None) -> str:
    """
    Приводит текст режима работы к читаемому каноническому виду.
    
    Args:
        text: Исходный текст режима работы (может быть None)
        
    Returns:
        str: Нормализованный текст режима работы
    """
    if not text:
        return ""
    return _normalize_work_schedule(text)
