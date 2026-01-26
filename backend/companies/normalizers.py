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
    
    # Если номер начинается с 8 - заменяем на +7
    if phone_digits.startswith('8') and len(phone_digits) >= 11:
        phone_digits = '+7' + phone_digits[1:]
    # Если номер начинается с 7 и нет + - добавляем +
    elif phone_digits.startswith('7') and not phone_digits.startswith('+7'):
        phone_digits = '+' + phone_digits
    
    # Если есть "хвост" без слов ext/доб, но он короткий — считаем его extension и отбрасываем.
    # Пример: 7 999 123 45 67 8901 -> основной номер 11 цифр + ext 8901
    # Применяем после обработки 8->+7 и 7->+7, но до финальной проверки
    digits_only = ''.join(c for c in phone_digits if c.isdigit())
    if len(digits_only) > 11:
        if (digits_only.startswith("7") and len(digits_only) >= 11) or (digits_only.startswith("8") and len(digits_only) >= 11):
            main = digits_only[:11]
            tail = digits_only[11:]
            if 1 <= len(tail) <= 6:
                # Короткий хвост - считаем extension, отбрасываем
                # Пересобираем phone_digits с учетом + если был
                if phone_digits.startswith('+'):
                    phone_digits = '+' + main
                else:
                    phone_digits = main
                # Обновляем digits_only для дальнейшей обработки
                digits_only = main
    
    # Если номер не начинается с + и достаточно цифр - добавляем +7 для РФ
    phone_digit_count = len(digits_only)
    if not phone_digits.startswith('+') and 10 <= phone_digit_count <= 11:
        if phone_digit_count == 10:
            phone_digits = '+7' + phone_digits
        elif phone_digit_count == 11 and phone_digits[0] == '7':
            phone_digits = '+' + phone_digits
    
    # Проверяем финальную валидность
    final_digits = [c for c in phone_digits if c.isdigit()]
    if len(final_digits) < MIN_PHONE_DIGITS or len(final_digits) > MAX_PHONE_DIGITS:
        # Если невалидный номер - возвращаем исходную строку (обрезанную)
        return original[:50]
    
    # Возвращаем нормализованный номер (E.164 формат)
    result = phone_digits if phone_digits.startswith('+') else original[:50]
    
    # Обрезаем до 50 символов (максимальная длина поля phone в модели)
    return result[:50]


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
