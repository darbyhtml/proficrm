from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import json
import logging
import re
import time
from uuid import UUID

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime, parse_date
from datetime import date as dt_date, datetime, time as dt_time, timezone as dt_timezone

from accounts.models import User
from companies.models import Company, CompanyNote, CompanySphere, Contact, ContactEmail, ContactPhone, CompanyPhone, CompanyEmail
from tasksapp.models import Task

from .client import AmoClient, AmoApiError, RateLimitError

logger = logging.getLogger(__name__)

# Глобальный флаг поддержки bulk-получения заметок
# None = еще не проверяли, True = поддерживается, False = не поддерживается
_notes_bulk_supported: bool | None = None


def _mask_phone(phone: str) -> str:
    """Маскирует телефон для безопасного логирования (оставляет последние 2-3 цифры)."""
    if not phone or not isinstance(phone, str):
        return "***"
    digits = ''.join(c for c in phone if c.isdigit())
    if len(digits) <= 3:
        return "***"
    return "*" * (len(digits) - 3) + digits[-3:]


def _mask_email(email: str) -> str:
    """Маскирует email для безопасного логирования (оставляет первые 2 символа и домен)."""
    if not email or not isinstance(email, str):
        return "***"
    if "@" not in email:
        return "***"
    parts = email.split("@")
    if len(parts) != 2:
        return "***"
    local, domain = parts
    if len(local) <= 2:
        masked_local = "*" * len(local)
    else:
        masked_local = local[:2] + "*" * (len(local) - 2)
    return f"{masked_local}@{domain}"


# Константы для валидации телефонов
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

# Allowlist для enum_code телефонов (amoCRM)
# Поддерживаемые типы: WORK, MOB (MOBILE), HOME, OTHER
PHONE_ENUM_ALLOWLIST = {
    "WORK": ContactPhone.PhoneType.WORK,
    "WORKDD": ContactPhone.PhoneType.WORK,  # Work Direct Dial
    "WORK_DIRECT": ContactPhone.PhoneType.WORK,
    "MOBILE": ContactPhone.PhoneType.MOBILE,
    "MOB": ContactPhone.PhoneType.MOBILE,
    "CELL": ContactPhone.PhoneType.MOBILE,
    "HOME": ContactPhone.PhoneType.HOME,
    "FAX": ContactPhone.PhoneType.FAX,
    "OTHER": ContactPhone.PhoneType.OTHER,
}


def map_phone_enum_code(enum_code: str | None, field_name: str = "", result: AmoMigrateResult | None = None) -> ContactPhone.PhoneType:
    """
    Маппинг enum_code из amoCRM в PhoneType нашей CRM.
    
    Использует allowlist: WORK, MOB, HOME, OTHER.
    Неизвестные типы (например WORKDD) → маппятся в OTHER (не WORK, чтобы не портить аналитику), 
    и увеличивается счетчик unknown_phone_enum_code_count.
    
    Args:
        enum_code: enum_code из amoCRM (может быть None)
        field_name: название поля (для определения типа по названию)
        result: объект AmoMigrateResult для увеличения счетчика метрик (опционально)
        
    Returns:
        ContactPhone.PhoneType: Тип телефона
    """
    if enum_code:
        enum_code_upper = str(enum_code).upper().strip()
        if enum_code_upper in PHONE_ENUM_ALLOWLIST:
            return PHONE_ENUM_ALLOWLIST[enum_code_upper]
        else:
            # Неизвестный enum_code - логируем и увеличиваем счетчик
            if result is not None:
                result.unknown_phone_enum_code_count += 1
            logger.debug(f"Unknown phone enum_code '{enum_code}', mapping to OTHER")
            return ContactPhone.PhoneType.OTHER  # Дефолт для неизвестных (не WORK, чтобы не портить аналитику)
    
    # Fallback: определяем по названию поля
    field_name_lower = str(field_name).lower()
    if "раб" in field_name_lower:
        return ContactPhone.PhoneType.WORK
    elif "моб" in field_name_lower:
        return ContactPhone.PhoneType.MOBILE
    elif "дом" in field_name_lower:
        return ContactPhone.PhoneType.HOME
    elif "факс" in field_name_lower:
        return ContactPhone.PhoneType.FAX
    
    # По умолчанию → OTHER
    return ContactPhone.PhoneType.OTHER


def _norm(s: str) -> str:
    return (s or "").strip().lower()


@dataclass
class PhoneResult:
    """Результат нормализации телефона с детальной информацией."""
    digits: str = ""  # Только цифры номера (без + и форматирования)
    e164: str | None = None  # Номер в формате E.164 (+7XXXXXXXXXX)
    valid: bool = False  # Валидный ли телефон (>= 10 цифр для РФ)
    ext: str | None = None  # Дополнительный номер (доб.)
    reason: str | None = None  # Причина невалидности или дополнительная информация


@dataclass
class NormalizedPhone:
    """Результат нормализации телефона (обратная совместимость)."""
    phone_e164: str | None = None  # Номер в формате E.164 (+7XXXXXXXXXX)
    ext: str | None = None  # Дополнительный номер (доб.)
    note: str | None = None  # Дополнительная информация (инструкции)
    isValid: bool = False  # Валидный ли телефон


@dataclass
class ParsedPhoneValue:
    """Результат парсинга телефонного значения с извлечением номеров, extension и комментариев."""
    phones: list[str] = None  # Список нормализованных телефонов в E.164
    extension: str | None = None  # Дополнительный номер (доб./ext)
    comment: str | None = None  # Комментарий/инструкции (не номер)
    rejected_reason: str | None = None  # Причина отклонения (если номер не извлечён)
    
    def __post_init__(self):
        if self.phones is None:
            self.phones = []


def normalize_phone(raw: str | None) -> NormalizedPhone:
    """
    Валидация и нормализация телефона.
    
    Удаляет пробелы/скобки/дефисы, распознает +7/8 для РФ, приводит к E.164.
    Если после очистки цифр < MIN_PHONE_DIGITS - считает НЕ телефоном.
    Если строка содержит ключевые фразы-инструкции и НЕ содержит валидного номера - 
    возвращает isValid=False, note=исходная строка.
    Если строка содержит валидный номер + дополнение ("доб. 4", "затем 1") - 
    номер идёт в phone_e164, а дополнение в note/ext.
    
    Безопасно обрабатывает None, пустые строки, не-строки.
    
    Args:
        raw: Исходная строка с телефоном (может быть None)
        
    Returns:
        NormalizedPhone: Результат нормализации
    """
    if raw is None:
        return NormalizedPhone(isValid=False)
    
    if not isinstance(raw, str):
        return NormalizedPhone(isValid=False, note=str(raw) if raw else None)
    
    original = raw.strip()
    if not original:
        return NormalizedPhone(isValid=False)
    
    # Проверяем на инструкции (если нет валидного номера)
    original_lower = original.lower()
    has_instruction_keywords = any(kw in original_lower for kw in PHONE_INSTRUCTION_KEYWORDS)
    
    # Извлекаем только цифры и + (для международного формата)
    digits_only = ''.join(c for c in original if c.isdigit() or c == '+')
    
    # Если цифр меньше минимума - это не телефон
    digit_count = len([c for c in digits_only if c.isdigit()])
    if digit_count < MIN_PHONE_DIGITS:
        # Если есть ключевые слова инструкций - это точно инструкция
        if has_instruction_keywords:
            return NormalizedPhone(isValid=False, note=original)
        return NormalizedPhone(isValid=False)
    
    # Если слишком много цифр - тоже не телефон
    if digit_count > MAX_PHONE_DIGITS:
        return NormalizedPhone(isValid=False, note=original if has_instruction_keywords else None)
    
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
    # Паттерн: номер + пробел + "затем"/"доб"/"внутр" + число
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
    # Сначала извлекаем все цифры и +, сохраняя структуру для парсинга скобок
    # Формат (38473)3-33-92 означает: код города 38473, затем номер 3-33-92
    # Это российский номер, поэтому добавляем +7
    
    # Удаляем все нецифровые символы кроме + и скобок (для парсинга формата (38473)3-33-92)
    # Но сначала проверяем, есть ли скобки - это может быть формат (код)номер
    has_brackets = '(' in cleaned_phone and ')' in cleaned_phone
    
    if has_brackets:
        # Формат (38473)3-33-92 или (495)123-45-67
        # Извлекаем код города из скобок и номер после скобок
        bracket_match = re.search(r'\((\d+)\)(.+)', cleaned_phone)
        if bracket_match:
            city_code = bracket_match.group(1)
            number_part = bracket_match.group(2)
            # Объединяем код города и номер
            phone_digits = city_code + ''.join(c for c in number_part if c.isdigit())
        else:
            # Если не удалось распарсить - просто извлекаем цифры
            phone_digits = ''.join(c for c in cleaned_phone if c.isdigit() or c == '+')
    else:
        # Обычный формат - просто извлекаем цифры и +
        phone_digits = ''.join(c for c in cleaned_phone if c.isdigit() or c == '+')
    
    # Если номер начинается с 8 - заменяем на +7
    if phone_digits.startswith('8') and len(phone_digits) >= 11:
        phone_digits = '+7' + phone_digits[1:]
    # Если номер начинается с 7 и нет + - добавляем +
    elif phone_digits.startswith('7') and not phone_digits.startswith('+7'):
        phone_digits = '+' + phone_digits
    # Если номер не начинается с + и достаточно цифр - добавляем +7 для РФ
    phone_digit_count = len([c for c in phone_digits if c.isdigit()])
    if not phone_digits.startswith('+') and 10 <= phone_digit_count <= 11:
        # Предполагаем российский номер
        if phone_digit_count == 10:
            phone_digits = '+7' + phone_digits
        elif phone_digit_count == 11 and phone_digits[0] == '7':
            phone_digits = '+' + phone_digits
    
    # Проверяем финальную валидность
    final_digits = [c for c in phone_digits if c.isdigit()]
    if len(final_digits) < MIN_PHONE_DIGITS or len(final_digits) > MAX_PHONE_DIGITS:
        # Если есть ключевые слова инструкций - это инструкция
        if has_instruction_keywords:
            return NormalizedPhone(isValid=False, note=original)
        return NormalizedPhone(isValid=False)
    
    # Если номер валиден, но есть инструкции без extension - добавляем в note
    if has_instruction_keywords and not ext_value:
        # Ищем инструкции в исходной строке
        for kw in PHONE_INSTRUCTION_KEYWORDS:
            if kw in original_lower:
                # Извлекаем контекст вокруг ключевого слова
                idx = original_lower.find(kw)
                context_start = max(0, idx - 20)
                context_end = min(len(original), idx + len(kw) + 20)
                context = original[context_start:context_end].strip()
                if context and context not in note_parts:
                    note_parts.append(context)
                break
    
    note_text = '; '.join(note_parts) if note_parts else None
    
    # Извлекаем только цифры для digits
    digits_only = ''.join(c for c in phone_digits if c.isdigit())
    
    result = NormalizedPhone(
        phone_e164=phone_digits if phone_digits.startswith('+') else None,
        ext=ext_value,
        note=note_text,
        isValid=True
    )
    
    return result


def normalize_phone_enhanced(raw: str | None) -> PhoneResult:
    """
    Улучшенная версия normalize_phone с детальной информацией.
    
    Возвращает PhoneResult с digits, e164, valid, ext, reason.
    
    Args:
        raw: Исходная строка с телефоном (может быть None)
        
    Returns:
        PhoneResult: Результат нормализации с детальной информацией
    """
    if raw is None:
        return PhoneResult(reason="empty_input")
    
    if not isinstance(raw, str):
        return PhoneResult(reason=f"invalid_type_{type(raw).__name__}")
    
    original = raw.strip()
    if not original:
        return PhoneResult(reason="empty_string")
    
    # Проверяем на инструкции
    original_lower = original.lower()
    has_instruction_keywords = any(kw in original_lower for kw in PHONE_INSTRUCTION_KEYWORDS)
    
    # Извлекаем только цифры
    digits = ''.join(c for c in original if c.isdigit())
    digit_count = len(digits)
    
    # Если цифр меньше минимума - это не телефон
    if digit_count < MIN_PHONE_DIGITS:
        if has_instruction_keywords:
            return PhoneResult(digits=digits, reason="instruction_only_no_phone", ext=None)
        return PhoneResult(digits=digits, reason=f"too_short_{digit_count}_digits")
    
    # Если слишком много цифр - тоже не телефон
    if digit_count > MAX_PHONE_DIGITS:
        return PhoneResult(digits=digits, reason=f"too_long_{digit_count}_digits")
    
    # Извлекаем extension/доб
    ext_value = None
    cleaned_phone = original
    
    for pattern in EXTENSION_PATTERNS:
        match = re.search(pattern, original, re.IGNORECASE)
        if match:
            ext_value = match.group(1)
            cleaned_phone = re.sub(pattern, '', cleaned_phone, flags=re.IGNORECASE).strip()
            break
    
    # Нормализуем номер
    has_brackets = '(' in cleaned_phone and ')' in cleaned_phone
    
    if has_brackets:
        bracket_match = re.search(r'\((\d+)\)(.+)', cleaned_phone)
        if bracket_match:
            city_code = bracket_match.group(1)
            number_part = bracket_match.group(2)
            phone_digits = city_code + ''.join(c for c in number_part if c.isdigit())
        else:
            phone_digits = ''.join(c for c in cleaned_phone if c.isdigit() or c == '+')
    else:
        phone_digits = ''.join(c for c in cleaned_phone if c.isdigit() or c == '+')
    
    # Нормализация для РФ
    if phone_digits.startswith('8') and len(phone_digits) >= 11:
        phone_digits = '+7' + phone_digits[1:]
    elif phone_digits.startswith('7') and not phone_digits.startswith('+7'):
        phone_digits = '+' + phone_digits
    
    phone_digit_count = len([c for c in phone_digits if c.isdigit()])
    if not phone_digits.startswith('+') and 10 <= phone_digit_count <= 11:
        if phone_digit_count == 10:
            phone_digits = '+7' + phone_digits
        elif phone_digit_count == 11 and phone_digits[0] == '7':
            phone_digits = '+' + phone_digits
    
    # Проверяем финальную валидность
    final_digits = [c for c in phone_digits if c.isdigit()]
    if len(final_digits) < MIN_PHONE_DIGITS or len(final_digits) > MAX_PHONE_DIGITS:
        if has_instruction_keywords:
            return PhoneResult(digits=digits, reason="instruction_only_no_phone", ext=ext_value)
        return PhoneResult(digits=digits, reason=f"invalid_after_normalization_{len(final_digits)}_digits", ext=ext_value)
    
    # Валидный номер
    e164 = phone_digits if phone_digits.startswith('+') else None
    reason = None
    if has_instruction_keywords and not ext_value:
        reason = "has_instructions"
    
    return PhoneResult(
        digits=digits,
        e164=e164,
        valid=True,
        ext=ext_value,
        reason=reason
    )


def extract_phone_candidates(text: str) -> list[str]:
    """
    Извлекает кандидаты на телефоны из "грязной" строки.
    
    Ищет последовательности цифр длиной >= 7 символов, которые могут быть телефонами.
    
    Args:
        text: Исходный текст
        
    Returns:
        list[str]: Список кандидатов (сырые строки для последующей нормализации)
    """
    if not text or not isinstance(text, str):
        return []
    
    # Ищем последовательности цифр с возможными разделителями
    # Паттерн: цифры, возможно с разделителями (пробелы, дефисы, скобки, плюсы)
    patterns = [
        r'\+?[\d\s\-\(\)]{7,}',  # Общий паттерн для телефонов
        r'\(?\d{3,5}\)?[\s\-]?\d{2,4}[\s\-]?\d{2,4}',  # Формат (код)номер
        r'\d{10,11}',  # Просто 10-11 цифр подряд
    ]
    
    candidates = []
    for pattern in patterns:
        matches = re.finditer(pattern, text)
        for match in matches:
            candidate = match.group(0).strip()
            # Проверяем, что это не просто случайная последовательность цифр в тексте
            if len(''.join(c for c in candidate if c.isdigit())) >= 7:
                candidates.append(candidate)
    
    # Убираем дубликаты, сохраняя порядок
    seen = set()
    unique_candidates = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            unique_candidates.append(c)
    
    return unique_candidates


def clean_person_name_fields(name: str) -> tuple[str, str]:
    """
    Очищает имя/ФИО от "доб." и инструкций дозвона.
    
    Удаляет паттерны: доб.?, затем, ext.?, добавочн..., #, "перевести на …".
    Извлечённые инструкции возвращаются отдельно для добавления в note.
    
    Args:
        name: Исходное имя/ФИО
        
    Returns:
        tuple[str, str]: (очищенное_имя, извлеченные_инструкции_дозвона)
    """
    return sanitize_name(name)


def parse_phone_value(raw: str | None) -> ParsedPhoneValue:
    """
    Парсит телефонное значение, извлекая номера, extension и комментарии.
    
    Из raw пытается извлечь реальный номер (не просто reject по ключевым словам).
    Отдельно извлекает extension/инструкции: доб. N, ext N, внутр. N, затем N, "через …", "перевести …".
    
    Если номер найден и после нормализации имеет 10–15 цифр:
    - записывает в phones (E.164, для РФ +7XXXXXXXXXX)
    - инструкции/остаток → в comment
    
    Если номер не извлечён:
    - phones = []
    - весь текст переносится в comment с префиксом "Комментарий к телефону: ..."
    
    Args:
        raw: Исходная строка с телефоном (может быть None)
        
    Returns:
        ParsedPhoneValue: Результат парсинга с телефонами, extension и комментарием
    """
    if raw is None or not isinstance(raw, str) or not raw.strip():
        return ParsedPhoneValue(rejected_reason="empty_input")
    
    original = raw.strip()
    
    # Сначала пытаемся извлечь extension/инструкции
    extension = None
    comment_parts = []
    cleaned_phone = original
    
    # Извлекаем extension (доб./ext/внутр.)
    for pattern in EXTENSION_PATTERNS:
        matches = list(re.finditer(pattern, cleaned_phone, re.IGNORECASE))
        for match in matches:
            ext_value = match.group(1) if match.lastindex else None
            if ext_value:
                extension = ext_value
                cleaned_phone = cleaned_phone.replace(match.group(0), '', 1)
    
    # Извлекаем текстовые инструкции (через, перевести, мини АТС и т.п.)
    instruction_keywords_patterns = [
        r'только\s+через[^,]*',
        r'через\s+[^,]+',
        r'перевести\s+на\s+[^,]+',
        r'мини\s+атс[^,]*',
        r'миниатс[^,]*',
        r'приемн[^,]*',
        r'попросить[^,]*',
        r'соединить[^,]*',
    ]
    
    for pattern in instruction_keywords_patterns:
        matches = list(re.finditer(pattern, cleaned_phone, re.IGNORECASE))
        for match in matches:
            instruction = match.group(0).strip()
            if instruction:
                comment_parts.append(instruction)
                cleaned_phone = cleaned_phone.replace(match.group(0), '', 1)
    
    # Очищаем от лишних пробелов и разделителей
    cleaned_phone = re.sub(r'\s+', ' ', cleaned_phone).strip()
    cleaned_phone = re.sub(r'[,\s]+', ' ', cleaned_phone).strip()
    
    # Пытаемся нормализовать оставшийся текст как телефон
    normalized = normalize_phone(cleaned_phone)
    
    if normalized.isValid and normalized.phone_e164 and is_valid_phone(normalized.phone_e164):
        # Номер найден и валиден
        phones = [normalized.phone_e164]
        
        # Объединяем extension и комментарии
        if extension:
            comment_parts.insert(0, f"доб. {extension}")
        if normalized.note:
            comment_parts.append(normalized.note)
        
        comment = "; ".join(comment_parts) if comment_parts else None
        
        return ParsedPhoneValue(
            phones=phones,
            extension=extension or normalized.ext,
            comment=comment
        )
    else:
        # Номер не извлечён - весь текст в комментарий
        all_text = original
        if extension:
            all_text = f"{all_text} (доб. {extension})"
        
        return ParsedPhoneValue(
            phones=[],
            extension=extension,
            comment=f"Комментарий к телефону: {all_text}",
            rejected_reason=normalized.note or "no_valid_phone_found"
        )


def position_is_only_phone(value: str | None) -> bool:
    """
    Проверяет, является ли POSITION только телефоном (без текста должности).
    
    True, только если в строке нет букв/слов, а после очистки остаётся 10–15 цифр
    (допуская +()- пробелы).
    
    Args:
        value: Значение поля POSITION
        
    Returns:
        bool: True если POSITION содержит только телефон (без текста)
    """
    if not value or not isinstance(value, str):
        return False
    
    original = value.strip()
    if not original:
        return False
    
    # Проверяем наличие букв (русских или латинских)
    has_letters = bool(re.search(r'[а-яёА-ЯЁa-zA-Z]', original))
    if has_letters:
        return False  # Есть текст должности
    
    # Извлекаем только цифры, +, пробелы, дефисы, скобки
    cleaned = re.sub(r'[^\d+\s\-()]', '', original)
    digits = re.sub(r'[^\d]', '', cleaned)
    
    # Должно быть 10-15 цифр для валидного телефона
    return 10 <= len(digits) <= 15


def validate_email(email: str | None) -> bool:
    """
    Простая валидация email формата.
    
    Проверяет наличие @ и базовую структуру email.
    
    Args:
        email: Email для проверки
        
    Returns:
        bool: True если email валиден
    """
    if not email or not isinstance(email, str):
        return False
    
    email = email.strip()
    if not email:
        return False
    
    # Простая проверка: есть @ и есть точка после @
    if "@" not in email:
        return False
    
    parts = email.split("@")
    if len(parts) != 2:
        return False
    
    local, domain = parts
    if not local or not domain:
        return False
    
    # Должна быть точка в домене
    if "." not in domain:
        return False
    
    # Базовый regex для email (не строгий, но достаточный)
    email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(email_pattern, email))


def validate_cold_call_timestamp(epoch: int | None) -> bool:
    """
    Валидация epoch timestamp для поля "Холодный звонок".
    
    Проверяет диапазон 2000-2100.
    
    Args:
        epoch: Timestamp в секундах
        
    Returns:
        bool: True если timestamp в допустимом диапазоне
    """
    if epoch is None:
        return False
    
    min_epoch = 946684800  # 2000-01-01 00:00:00 UTC
    max_epoch = 4102444800  # 2100-01-01 00:00:00 UTC
    
    return min_epoch <= epoch <= max_epoch


def sanitize_name_extract_instructions(value: str) -> tuple[str, str | None]:
    """
    Очищает имя от инструкций дозвона (доб./затем/ext) и извлекает их.
    
    Удаляет из имени только паттерны инструкций: доб.\\s*\\d+, затем\\s*\\d+, 
    внутр.\\s*\\d+, ext\\s*\\d+, доп.\\s*\\d+.
    
    НЕ удаляет из имени "ОК", "ЛПР", "приемная" и т.п. (это не инструкции дозвона).
    
    Args:
        value: Исходное имя
        
    Returns:
        tuple[str, str | None]: (очищенное_имя, извлеченные_инструкции или None)
    """
    return sanitize_name(value)  # Используем существующую функцию


def sanitize_name(name: str) -> tuple[str, str]:
    """
    Очищает имя от "доб." и инструкций дозвона (обратная совместимость).
    
    Перед разбором ФИО удаляет хвосты вида: ", доб. 4", "доб.4", "затем 1", 
    "внутр. 123", "ext 12", "доп. 7", "тональный", "мини АТС" и т.п.
    
    Args:
        name: Исходное имя
        
    Returns:
        tuple[str, str]: (очищенное_имя, извлеченные_инструкции)
    """
    if not name or not isinstance(name, str):
        return ("", "")
    
    original = name.strip()
    if not original:
        return ("", "")
    
    # Расширенные паттерны для извлечения extension/инструкций из имени
    # Включает: доб\.?\s*\d+, затем\s*\d+, ext\.?\s*\d+, добавочн..., #\s*\d+, "перевести на …"
    # Используем raw strings для паттернов с обратными слешами
    extension_patterns = [
        r',\s*доб\.?\s*\d+',
        r',\s*доб\s+\d+',
        r',\s*внутр\.?\s*\d+',
        r',\s*внутр\s+\d+',
        r',\s*ext\.?\s*\d+',
        r',\s*ext\s+\d+',
        r',\s*extension\s+\d+',
        r',\s*затем\s+\d+',
        r',\s*после\s+\d+',
        r',\s*нажать\s+\d+',
        r',\s*доп\.?\s*\d+',
        r',\s*доп\s+\d+',
        r',\s*#\s*\d+',  # #123
        r',\s*x\s*\d+',  # x123
        r'\s+доб\.?\s*\d+',
        r'\s+доб\s+\d+',
        r'\s+внутр\.?\s*\d+',
        r'\s+внутр\s+\d+',
        r'\s+ext\.?\s*\d+',
        r'\s+ext\s+\d+',
        r'\s+затем\s+\d+',
        r'\s+после\s+\d+',
        r'\s+нажать\s+\d+',
        r'\s+доп\.?\s*\d+',
        r'\s+доп\s+\d+',
        r'\s+#\s*\d+',  # #123
        r'\s+x\s*\d+',  # x123
    ]
    
    # Паттерны для текстовых инструкций (без цифр): "перевести на ...", "тональный", "мини АТС"
    instruction_patterns = [
        r',\s*тональный',
        r',\s*мини\s+атс',
        r',\s*миниатс',
        r',\s*перевести\s+на\s+[^,]+',  # "перевести на ..."
        r',\s*добавочн[^,]*',  # "добавочный", "добавочная"
        r'\s+тональный',
        r'\s+мини\s+атс',
        r'\s+миниатс',
        r'\s+перевести\s+на\s+[^\s]+',  # "перевести на ..."
        r'\s+добавочн[^\s]*',  # "добавочный", "добавочная"
    ]
    
    extracted_parts = []
    cleaned = original
    
    # Извлекаем extension паттерны
    for pattern in extension_patterns:
        matches = re.finditer(pattern, cleaned, re.IGNORECASE)
        for match in matches:
            extracted = match.group(0).strip().lstrip(',').strip()
            if extracted:
                extracted_parts.append(extracted)
            cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE)
    
    # Извлекаем текстовые инструкции
    for pattern in instruction_patterns:
        matches = re.finditer(pattern, cleaned, re.IGNORECASE)
        for match in matches:
            extracted = match.group(0).strip().lstrip(',').strip()
            if extracted:
                extracted_parts.append(extracted)
            cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE)
    
    # Очищаем от лишних пробелов и запятых
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    cleaned = re.sub(r',+', ',', cleaned).strip(',').strip()
    
    extracted_text = ', '.join(extracted_parts) if extracted_parts else ""
    
    return (cleaned, extracted_text)


def is_valid_phone(value: str | None) -> bool:
    """
    Строгая проверка: является ли значение валидным телефоном.
    
    Использует normalize_phone для проверки. Валидным считается только номер,
    который после нормализации имеет >= MIN_PHONE_DIGITS цифр и проходит все проверки.
    
    Args:
        value: Значение для проверки
        
    Returns:
        bool: True если значение валидный телефон
    """
    if not value or not isinstance(value, str):
        return False
    
    normalized = normalize_phone(value)
    return normalized.isValid and normalized.phone_e164 is not None


def extract_phone_from_text(text: str) -> tuple[str | None, str]:
    """
    Извлекает телефон из текста, который может содержать и текст, и телефон.
    
    Если в тексте найден валидный телефон - возвращает нормализованный номер и очищенный текст.
    Если телефона нет - возвращает None и исходный текст.
    
    Args:
        text: Текст, который может содержать телефон
        
    Returns:
        tuple[phone_e164 | None, cleaned_text]: Нормализованный телефон (если найден) и очищенный текст
    """
    if not text or not isinstance(text, str):
        return None, text or ""
    
    normalized = normalize_phone(text)
    if normalized.isValid and normalized.phone_e164:
        # Удаляем телефон из текста
        cleaned = text
        # Удаляем нормализованный номер
        cleaned = cleaned.replace(normalized.phone_e164, "")
        # Удаляем исходный номер (если отличается от нормализованного)
        if text != normalized.phone_e164:
            # Пытаемся найти и удалить исходный номер
            digits_in_text = ''.join(c for c in text if c.isdigit() or c in ['+', '-', '(', ')', ' '])
            if len(digits_in_text) >= 7:
                # Удаляем все цифровые последовательности длиной >= 7
                cleaned = re.sub(r'[\d\+\-\(\)\s]{7,}', '', cleaned)
        
        # Очищаем от лишних пробелов
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        return normalized.phone_e164, cleaned
    
    return None, text


def looks_like_phone_for_position(value: str) -> bool:
    """
    Строгая проверка: похоже ли значение должности на телефон.
    
    Проверяет, что значение ПОСЛЕ ОЧИСТКИ содержит в основном телефонные символы
    (цифры, +, пробелы, дефисы, скобки) и проходит валидацию телефона.
    
    Если значение содержит много букв или других символов - это не телефон.
    
    Args:
        value: Значение для проверки
        
    Returns:
        bool: True если значение похоже на телефон (в основном телефонные символы + валидно)
    """
    if not value or not isinstance(value, str):
        return False
    
    value = value.strip()
    if not value:
        return False
    
    # Извлекаем все символы
    phone_chars = set(['+', '-', '(', ')', ' ', '.', '/'])
    phone_char_count = sum(1 for c in value if c.isdigit() or c in phone_chars)
    total_chars = len(value)
    
    # Если менее 70% символов - телефонные (цифры, +, -, скобки, пробелы) - это не телефон
    if total_chars > 0 and phone_char_count / total_chars < 0.7:
        return False
    
    # Извлекаем цифры
    digits = ''.join(c for c in value if c.isdigit())
    digit_count = len(digits)
    
    # Если цифр меньше 7 - точно не телефон
    if digit_count < 7:
        return False
    
    # Строгая проверка: используем normalize_phone для валидации
    normalized = normalize_phone(value)
    if not normalized.isValid:
        return False
    
    # Дополнительная проверка: если после удаления телефонных символов осталось много текста - это не телефон
    text_only = re.sub(r'[\d\+\-\(\)\s\.\/]', '', value)
    if len(text_only) > 3:  # Если осталось больше 3 букв - это не телефон
        return False
    
    return True
    
    # Если начинается с +, 8, 7 - похоже на телефон
    if value.startswith(('+', '8', '7')) and digit_count >= 6:
        return True
    
    # Если содержит телефонные разделители и достаточно цифр
    phone_separators = ['-', '(', ')', ' ', '.', '/']
    has_separators = any(sep in value for sep in phone_separators)
    if has_separators and digit_count >= 6:
        return True
    
    # Нормализуем и проверяем через normalize_phone
    normalized = normalize_phone(value)
    if normalized.isValid:
        return True
    
    return False


def _parse_fio(name_str: str, first_name_str: str = "", last_name_str: str = "") -> tuple[str, str]:
    """
    Парсит ФИО из строк amoCRM в (last_name, first_name).
    
    Логика:
    - Если есть и first_name и last_name - используем их как есть
    - Если есть только name - парсим "Фамилия Имя Отчество" -> (Фамилия, Имя Отчество)
    - Если есть name и first_name - парсим name как полное ФИО
    - Если есть name и last_name - парсим name как полное ФИО
    
    Args:
        name_str: Полное имя из поля "name"
        first_name_str: Имя из поля "first_name"
        last_name_str: Фамилия из поля "last_name"
    
    Returns:
        tuple[str, str]: (last_name, first_name)
    """
    first_name = (first_name_str or "").strip()
    last_name = (last_name_str or "").strip()
    name = (name_str or "").strip()
    
    # Если есть и first_name и last_name - используем их
    if first_name and last_name:
        return (last_name[:120], first_name[:120])
    
    # Если есть только name - парсим его
    if name and not first_name and not last_name:
        parts = [p for p in name.split() if p.strip()]
        if len(parts) >= 2:
            # "Фамилия Имя Отчество" -> last_name="Фамилия", first_name="Имя Отчество"
            return (parts[0][:120], " ".join(parts[1:])[:120])
        elif len(parts) == 1:
            # Только одно слово - считаем именем
            return ("", parts[0][:120])
    
    # Если есть name и first_name - парсим name как полное ФИО
    if name and first_name and not last_name:
        parts = [p for p in name.split() if p.strip()]
        if len(parts) >= 2:
            # Если name содержит больше слов, чем first_name - парсим name
            return (parts[0][:120], " ".join(parts[1:])[:120])
        else:
            # Иначе используем first_name
            return ("", first_name[:120])
    
    # Если есть name и last_name - парсим name как полное ФИО
    if name and last_name and not first_name:
        parts = [p for p in name.split() if p.strip()]
        if len(parts) >= 2:
            # Если name содержит больше слов, чем last_name - парсим name
            return (parts[0][:120], " ".join(parts[1:])[:120])
        else:
            # Иначе используем last_name
            return (last_name[:120], "")
    
    # Если есть только first_name
    if first_name and not last_name:
        return ("", first_name[:120])
    
    # Если есть только last_name
    if last_name and not first_name:
        return (last_name[:120], "")
    
    # Если ничего нет
    return ("", "")


def _map_amo_user_to_local(amo_user: dict[str, Any]) -> User | None:
    """
    Best-effort сопоставление пользователя amo -> локальный User по имени.
    В amo имя может быть "Иванова Юлия Олеговна", а у нас "Иванова Юлия".
    """
    name = (amo_user.get("name") or "").strip()
    if not name:
        return None
    parts = [p for p in name.split(" ") if p]
    if len(parts) >= 2:
        ln, fn = parts[0], parts[1]
        u = User.objects.filter(last_name__iexact=ln, first_name__iexact=fn, is_active=True).first()
        if u:
            return u
    # fallback: contains
    for u in User.objects.filter(is_active=True):
        if _norm(name) in _norm(str(u)) or _norm(str(u)) in _norm(name):
            return u
    return None


def _fmt_duration(seconds: Any) -> str:
    try:
        s = int(seconds or 0)
    except Exception:
        s = 0
    if s <= 0:
        return "0с"
    m, sec = divmod(s, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}ч {m}м"
    if m:
        return f"{m}м {sec}с"
    return f"{sec}с"


def _as_text(v: Any) -> str:
    try:
        return str(v or "").strip()
    except Exception:
        return ""


def _format_call_note(note_type: str, params: Any) -> str:
    p = params if isinstance(params, dict) else {}
    incoming = note_type.lower().endswith("_in") or bool(p.get("incoming"))
    direction = "Входящий" if incoming else "Исходящий"
    src = _as_text(p.get("source"))
    uniq = _as_text(p.get("uniq") or p.get("unique") or p.get("call_id"))
    dur = _fmt_duration(p.get("duration"))
    phone = _as_text(p.get("phone") or p.get("phone_number") or p.get("number") or p.get("to") or p.get("from"))
    result = _as_text(p.get("result") or p.get("status") or p.get("call_status"))
    link = _as_text(p.get("link") or p.get("record_link") or p.get("record_url"))

    lines = []
    lines.append(f"Звонок · {direction}")
    if phone:
        lines.append("Номер: " + phone)
    if dur:
        lines.append("Длительность: " + dur)
    if src:
        lines.append("Источник: " + src)
    if uniq:
        lines.append("ID: " + uniq)
    if result:
        lines.append("Статус: " + result)
    if link:
        lines.append("Запись: " + link)
    return "\n".join(lines) if lines else "Звонок"


def _extract_custom_values(company: dict[str, Any], field_id: int) -> list[dict[str, Any]]:
    vals = company.get("custom_fields_values") or []
    if not isinstance(vals, list):
        return []
    for cf in vals:
        if int(cf.get("field_id") or 0) == int(field_id):
            v = cf.get("values") or []
            return v if isinstance(v, list) else []
    return []


def _analyze_contact_completely(contact: dict[str, Any]) -> dict[str, Any]:
    """
    Полный анализ контакта из AmoCRM API.
    Извлекает ВСЕ возможные поля согласно документации:
    https://www.amocrm.ru/developers/content/crm_platform/api-reference
    
    Возвращает структурированный словарь со всеми найденными данными.
    """
    if not isinstance(contact, dict):
        return {"error": "Contact is not a dict", "raw": str(contact)[:500]}
    
    result = {
        "standard_fields": {},
        "custom_fields": [],
        "embedded_data": {},
        "all_keys": [],
        "extracted_data": {},
    }
    
    # 1. СТАНДАРТНЫЕ ПОЛЯ КОНТАКТА (согласно документации AmoCRM API v4)
    standard_field_names = [
        "id", "name", "first_name", "last_name",
        "responsible_user_id", "group_id", "created_by", "updated_by",
        "created_at", "updated_at", "is_deleted",
        "phone", "email", "company_id",
    ]
    
    for field_name in standard_field_names:
        if field_name in contact:
            value = contact.get(field_name)
            result["standard_fields"][field_name] = value
    
    # Сохраняем все ключи контакта для анализа
    result["all_keys"] = list(contact.keys())
    
    # 2. CUSTOM_FIELDS_VALUES - все кастомные поля
    custom_fields = contact.get("custom_fields_values") or []
    if isinstance(custom_fields, list):
        for cf_idx, cf in enumerate(custom_fields):
            if not isinstance(cf, dict):
                continue
            
            field_info = {
                "index": cf_idx,
                "field_id": cf.get("field_id"),
                "field_name": cf.get("field_name"),
                "field_code": cf.get("field_code"),
                "field_type": cf.get("field_type"),
                "values": [],
                "values_count": 0,
            }
            
            # Извлекаем все значения поля
            values_list = cf.get("values") or []
            if isinstance(values_list, list):
                field_info["values_count"] = len(values_list)
                for v_idx, v in enumerate(values_list):
                    value_info = {
                        "index": v_idx,
                        "raw": v,
                    }
                    
                    if isinstance(v, dict):
                        # Стандартная структура значения
                        value_info["value"] = v.get("value")
                        value_info["enum_id"] = v.get("enum_id")
                        value_info["enum_code"] = v.get("enum_code")
                        value_info["enum"] = v.get("enum")
                        
                        # Для файлов - дополнительная информация
                        if isinstance(v.get("value"), dict) and "file_uuid" in v.get("value", {}):
                            file_info = v.get("value", {})
                            value_info["file_info"] = {
                                "file_uuid": file_info.get("file_uuid"),
                                "file_name": file_info.get("file_name"),
                                "file_size": file_info.get("file_size"),
                            }
                    else:
                        # Простое значение (строка, число и т.д.)
                        value_info["value"] = v
                    
                    field_info["values"].append(value_info)
            
            result["custom_fields"].append(field_info)
    
    # 3. _EMBEDDED - вложенные связи
    embedded = contact.get("_embedded") or {}
    if isinstance(embedded, dict):
        # Tags (теги)
        if "tags" in embedded:
            tags_list = embedded.get("tags") or []
            if isinstance(tags_list, list):
                result["embedded_data"]["tags"] = [
                    {
                        "id": tag.get("id") if isinstance(tag, dict) else None,
                        "name": tag.get("name") if isinstance(tag, dict) else str(tag),
                    }
                    for tag in tags_list
                ]
        
        # Companies (компании)
        if "companies" in embedded:
            companies_list = embedded.get("companies") or []
            if isinstance(companies_list, list):
                result["embedded_data"]["companies"] = [
                    {
                        "id": comp.get("id") if isinstance(comp, dict) else None,
                        "name": comp.get("name") if isinstance(comp, dict) else str(comp),
                    }
                    for comp in companies_list
                ]
        
        # Leads (сделки)
        if "leads" in embedded:
            leads_list = embedded.get("leads") or []
            if isinstance(leads_list, list):
                result["embedded_data"]["leads"] = [
                    {
                        "id": lead.get("id") if isinstance(lead, dict) else None,
                        "name": lead.get("name") if isinstance(lead, dict) else str(lead),
                    }
                    for lead in leads_list
                ]
        
        # Customers (покупатели)
        if "customers" in embedded:
            customers_list = embedded.get("customers") or []
            if isinstance(customers_list, list):
                result["embedded_data"]["customers"] = [
                    {
                        "id": cust.get("id") if isinstance(cust, dict) else None,
                        "name": cust.get("name") if isinstance(cust, dict) else str(cust),
                    }
                    for cust in customers_list
                ]
        
        # Catalog elements (элементы каталога)
        if "catalog_elements" in embedded:
            catalog_elements_list = embedded.get("catalog_elements") or []
            if isinstance(catalog_elements_list, list):
                result["embedded_data"]["catalog_elements"] = [
                    {
                        "id": elem.get("id") if isinstance(elem, dict) else None,
                        "name": elem.get("name") if isinstance(elem, dict) else str(elem),
                    }
                    for elem in catalog_elements_list
                ]
        
        # Notes (заметки)
        if "notes" in embedded:
            notes_list = embedded.get("notes") or []
            if isinstance(notes_list, list):
                result["embedded_data"]["notes"] = [
                    {
                        "id": note.get("id") if isinstance(note, dict) else None,
                        "note_type": note.get("note_type") if isinstance(note, dict) else None,
                        "text": note.get("text") if isinstance(note, dict) else None,
                        "params": note.get("params") if isinstance(note, dict) else None,
                    }
                    for note in notes_list
                ]
    
    # 4. ИЗВЛЕЧЕННЫЕ ДАННЫЕ (телефоны, email, должность, примечания)
    # Это данные, которые мы используем для импорта
    extracted = {
        "phones": [],
        "emails": [],
        "position": None,
        "note_text": None,
        "cold_call_timestamp": None,
    }
    
    # Телефоны из стандартного поля
    if contact.get("phone"):
        phone_str = str(contact.get("phone"))
        for pv in _split_multi(phone_str):
            if pv:
                extracted["phones"].append({
                    "value": pv,
                    "type": "OTHER",
                    "source": "standard_field",
                })
    
    # Email из стандартного поля
    if contact.get("email"):
        email_str = str(contact.get("email")).strip()
        if email_str:
            extracted["emails"].append({
                "value": email_str,
                "type": "OTHER",
                "source": "standard_field",
            })
    
    # Извлекаем данные из custom_fields
    for cf in result["custom_fields"]:
        field_code = str(cf.get("field_code") or "").upper()
        field_name = str(cf.get("field_name") or "").lower()
        field_type = str(cf.get("field_type") or "").lower()
        
        # Телефоны
        is_phone = (field_code == "PHONE" or "телефон" in field_name)
        if is_phone:
            for val_info in cf.get("values", []):
                val = val_info.get("value")
                if val:
                    enum_code = val_info.get("enum_code") or val_info.get("enum") or ""
                    extracted["phones"].append({
                        "value": str(val),
                        "type": str(enum_code).upper() if enum_code else "OTHER",
                        "source": f"custom_field_id={cf.get('field_id')}",
                        "field_name": cf.get("field_name"),
                    })
        
        # Email
        is_email = (field_code == "EMAIL" or "email" in field_name or "почта" in field_name)
        if is_email:
            for val_info in cf.get("values", []):
                val = val_info.get("value")
                if val and "@" in str(val):
                    enum_code = val_info.get("enum_code") or val_info.get("enum") or ""
                    extracted["emails"].append({
                        "value": str(val),
                        "type": str(enum_code).upper() if enum_code else "OTHER",
                        "source": f"custom_field_id={cf.get('field_id')}",
                        "field_name": cf.get("field_name"),
                    })
        
        # Должность
        is_position = (field_code == "POSITION" or "должность" in field_name or "позиция" in field_name)
        if is_position and not extracted["position"]:
            first_val = cf.get("values", [{}])[0].get("value") if cf.get("values") else None
            if first_val:
                extracted["position"] = {
                    "value": str(first_val),
                    "source": f"custom_field_id={cf.get('field_id')}",
                    "field_name": cf.get("field_name"),
                }
        
        # Примечание
        is_note = (
            any(k in field_name for k in ["примеч", "комментар", "коммент", "заметк"]) or
            any(k in field_code for k in ["NOTE", "COMMENT", "REMARK"])
        )
        if is_note and not extracted["note_text"]:
            first_val = cf.get("values", [{}])[0].get("value") if cf.get("values") else None
            if first_val:
                extracted["note_text"] = {
                    "value": str(first_val),
                    "source": f"custom_field_id={cf.get('field_id')}",
                    "field_name": cf.get("field_name"),
                }
        
        # Холодный звонок
        is_cold_call = (field_type == "date" and "холодный" in field_name and "звонок" in field_name)
        if is_cold_call and not extracted["cold_call_timestamp"]:
            first_val = cf.get("values", [{}])[0].get("value") if cf.get("values") else None
            if first_val:
                try:
                    extracted["cold_call_timestamp"] = {
                        "value": int(float(first_val)),
                        "source": f"custom_field_id={cf.get('field_id')}",
                        "field_name": cf.get("field_name"),
                    }
                except (ValueError, TypeError):
                    pass
    
    # Примечания из _embedded.notes
    if not extracted["note_text"] and "notes" in result["embedded_data"]:
        for note in result["embedded_data"]["notes"]:
            note_type = str(note.get("note_type") or "").lower()
            note_text_val = note.get("text") or ""
            
            # Берем заметки типа "common", "text" (не служебные)
            if note_type in ["common", "text", "common_message"] and note_text_val:
                extracted["note_text"] = {
                    "value": str(note_text_val),
                    "source": f"_embedded.notes (note_type={note_type})",
                }
                break
    
    result["extracted_data"] = extracted
    
    return result


def _build_field_meta(fields: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    for f in fields or []:
        try:
            fid = int(f.get("id") or 0)
        except Exception:
            fid = 0
        if not fid:
            continue
        out[fid] = {"id": fid, "name": str(f.get("name") or ""), "code": str(f.get("code") or ""), "type": f.get("type")}
    return out


def _custom_values_text(company: dict[str, Any], field_id: int) -> list[str]:
    vals = _extract_custom_values(company, field_id)
    out = []
    for v in vals:
        s = str(v.get("value") or "").strip()
        if s:
            out.append(s)
    return out


def _looks_like_phone(value: str) -> bool:
    """
    Проверяет, похоже ли значение на номер телефона.
    Использует normalize_phone для более точной проверки.
    ВАЖНО: для строгой валидации используйте normalize_phone() напрямую.
    """
    if not value or not isinstance(value, str):
        return False
    normalized = normalize_phone(value)
    return normalized.isValid


def _split_multi(s: str) -> list[str]:
    """
    В amo часто телефоны/почты лежат в одной строке через запятую/точку с запятой/переносы.
    """
    if not s:
        return []
    raw = str(s).replace("\r", "\n")
    parts: list[str] = []
    for chunk in raw.split("\n"):
        for p in chunk.replace(";", ",").split(","):
            v = p.strip()
            if v:
                parts.append(v)
    out: list[str] = []
    seen = set()
    for v in parts:
        k = v.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(v)
    return out


def parse_skynet_phones(text: str | None) -> tuple[list[str], int, list[str]]:
    """
    Парсит строку с телефонами из поля «Список телефонов (Скайнет)» (field_id 309609).
    Разделяет по \\n, запятой, точке с запятой. Каждый фрагмент нормализует к E.164.
    Игнорирует мусорные строки (не номера).

    Returns:
        (phones_e164, rejected_count, rejected_examples)
    """
    if not text or not isinstance(text, str):
        return ([], 0, [])
    parts = _split_multi(text)
    phones: list[str] = []
    seen: set[str] = set()
    rejected = 0
    examples: list[str] = []
    for p in parts:
        norm = normalize_phone(p)
        if norm.isValid and norm.phone_e164 and is_valid_phone(norm.phone_e164):
            e164 = norm.phone_e164
            if e164 not in seen:
                seen.add(e164)
                phones.append(e164)
        else:
            rejected += 1
            if len(examples) < 5:
                examples.append((p.strip() or p)[:80])
    return (phones, rejected, examples)


def _find_field_id(field_meta: dict[int, dict[str, Any]], *, codes: list[str] | None = None, name_contains: list[str] | None = None) -> int | None:
    codes_l = [c.lower() for c in (codes or [])]
    name_l = [n.lower() for n in (name_contains or [])]
    for fid, m in field_meta.items():
        code = str(m.get("code") or "").lower()
        name = str(m.get("name") or "").lower()
        if codes_l and code and any(code == c for c in codes_l):
            return fid
        if name_l and name and any(n in name for n in name_l):
            return fid
    return None


def _extract_company_fields(amo_company: dict[str, Any], field_meta: dict[int, dict[str, Any]]) -> dict[str, Any]:
    """
    Best-effort извлечение полей компании из custom_fields_values.
    """
    def first(fid: int | None) -> str:
        if not fid:
            return ""
        vals = _custom_values_text(amo_company, fid)
        return (vals[0] if vals else "")[:500]  # обрезаем до разумного максимума (для дальнейшей обрезки по полям)

    def list_vals(fid: int | None) -> list[str]:
        if not fid:
            return []
        vals = _custom_values_text(amo_company, fid)
        out: list[str] = []
        for s in vals:
            out.extend(_split_multi(s))
        return out

    fid_inn = _find_field_id(field_meta, codes=["inn"], name_contains=["инн"])
    fid_kpp = _find_field_id(field_meta, codes=["kpp"], name_contains=["кпп"])
    # Юридическое название: пытаемся найти по разным вариантам названия поля
    fid_legal = _find_field_id(
        field_meta,
        name_contains=[
            "юрид",
            "юр.",
            "юр ",
            "полное наимен",
            "полное название",
            "Полное название",
            "наименование юр",
            "название юр",
            "юрлицо",
        ],
    )
    fid_addr = _find_field_id(field_meta, codes=["address"], name_contains=["адрес"])
    # Ищем все поля с телефонами: основное поле телефона и поле "Список телефонов (Скайнет)"
    fid_phone = _find_field_id(field_meta, codes=["phone"], name_contains=["телефон"])
    # Поле 309609 = «Список телефонов (Скайнет)»; 291409 — иное поле, по имени не выбираем один «список»
    SKYNET_PHONE_FIELD_ID = 309609
    fid_phone_skynet = (SKYNET_PHONE_FIELD_ID if (field_meta and SKYNET_PHONE_FIELD_ID in field_meta) else None) or _find_field_id(field_meta, name_contains=["скайнет", "список телефонов"])
    
    # Логируем все поля с телефонами для диагностики
    if field_meta:
        phone_fields = []
        for fid, m in field_meta.items():
            name = str(m.get("name") or "").lower()
            code = str(m.get("code") or "").lower()
            if "телефон" in name or "phone" in code or "скайнет" in name or "список" in name:
                phone_fields.append(f"ID:{fid} name:'{m.get('name')}' code:'{m.get('code')}'")
        if phone_fields:
            logger.debug(f"_extract_company_fields: phone-related fields found: {phone_fields}")
    
    # Если найдено поле Скайнет и оно отличается от основного поля телефона, объединяем телефоны из обоих полей
    fid_email = _find_field_id(field_meta, codes=["email"], name_contains=["email", "e-mail", "почта"])
    fid_web = _find_field_id(field_meta, codes=["web"], name_contains=["сайт", "web"])
    fid_director = _find_field_id(field_meta, name_contains=["руководитель", "директор", "генеральный"])
    fid_activity = _find_field_id(field_meta, name_contains=["вид деятельности", "вид деят", "деятельност"])
    fid_employees = _find_field_id(field_meta, name_contains=["численность", "сотрудник", "штат"])
    fid_worktime = _find_field_id(field_meta, name_contains=["рабочее время", "часы работы", "режим работы", "работа с"])
    fid_tz = _find_field_id(field_meta, name_contains=["часовой пояс", "таймзона", "timezone"])
    fid_note = _find_field_id(field_meta, name_contains=["примеч", "комментар", "коммент", "заметк"])

    # Телефоны из основного поля. Скайнет (309609) — отдельно в skynet_phones, идут только в CompanyPhone с comment=SKYNET.
    phones_list = list_vals(fid_phone)
    skynet_phones: list[str] = []
    skynet_rejected = 0
    skynet_rejected_example: str | None = None
    if fid_phone_skynet and fid_phone_skynet != fid_phone:
        skynet_raws = _custom_values_text(amo_company, fid_phone_skynet)
        seen_skynet: set[str] = set()
        for s in skynet_raws:
            if not s or not isinstance(s, str):
                continue
            phs, rej, exs = parse_skynet_phones(s)
            for ph in phs:
                if ph not in seen_skynet:
                    seen_skynet.add(ph)
                    skynet_phones.append(ph)
            skynet_rejected += rej
            if exs and skynet_rejected_example is None:
                skynet_rejected_example = exs[0]
        if skynet_phones or skynet_rejected > 0:
            logger.info(
                f"_extract_company_fields: Skynet phone field (ID: {fid_phone_skynet}): "
                f"extracted {len(skynet_phones)} valid phone(s), rejected {skynet_rejected} non-phone value(s)"
            )
    elif fid_phone_skynet:
        logger.debug(f"_extract_company_fields: Skynet phone field found but same as main phone field (ID: {fid_phone_skynet})")
    else:
        logger.debug(f"_extract_company_fields: Skynet phone field (309609 or by name) not found")
    
    # ИНН может приходить как строка с несколькими значениями (через /, запятую, пробел)
    # Используем list_vals для извлечения всех значений, затем нормализуем через inn_utils
    inn_vals = list_vals(fid_inn)
    if inn_vals:
        # Объединяем все значения в одну строку для парсинга
        inn_raw = " / ".join(inn_vals)
        from companies.inn_utils import normalize_inn_string
        inn_combined = normalize_inn_string(inn_raw)
    else:
        inn_combined = first(fid_inn)
    
    result: dict[str, Any] = {
        "inn": inn_combined,
        "kpp": first(fid_kpp),
        "legal_name": first(fid_legal),
        "address": first(fid_addr),
        "phones": phones_list,
        "emails": list_vals(fid_email),
        "website": first(fid_web),
        "director": first(fid_director),
        "activity_kind": first(fid_activity),
        "employees_count": first(fid_employees),
        "worktime": first(fid_worktime),
        "work_timezone": first(fid_tz),
        "note": first(fid_note),
    }
    result["skynet_phones"] = skynet_phones
    result["skynet_phone_values_rejected"] = skynet_rejected
    result["skynet_rejected_example"] = skynet_rejected_example
    return result


def _json_sanitize(obj: Any) -> Any:
    """
    Рекурсивно заменяет datetime/date/time на str для надёжной записи в JSONField.
    Устраняет «Object of type time is not JSON serializable» при любом источнике.
    """
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, (dt_time, dt_date, datetime)):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _json_sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_sanitize(v) for v in obj]
    return obj


def _parse_amo_due(ts: Any) -> timezone.datetime | None:
    """
    amo может отдавать дедлайн как:
    - unix seconds int
    - unix ms int
    - строка с цифрами
    - ISO datetime string
    - ISO date string
    """
    if ts is None:
        return None
    UTC = getattr(timezone, "UTC", dt_timezone.utc)
    # dict wrapper
    if isinstance(ts, dict):
        for k in ("timestamp", "ts", "value"):
            if k in ts:
                return _parse_amo_due(ts.get(k))
        return None

    # numeric string / int
    if isinstance(ts, (int, float)) or (isinstance(ts, str) and ts.strip().isdigit()):
        try:
            ts_int = int(str(ts).strip())
        except Exception:
            ts_int = 0
        if ts_int <= 0:
            return None
        if ts_int > 10**12:
            ts_int = int(ts_int / 1000)
        try:
            return timezone.datetime.fromtimestamp(ts_int, tz=UTC)
        except Exception:
            return None

    # datetime string
    if isinstance(ts, str):
        s = ts.strip()
        if not s:
            return None
        dt = parse_datetime(s)
        if dt:
            if timezone.is_naive(dt):
                dt = timezone.make_aware(dt, timezone=UTC)
            return dt
        d = parse_date(s)
        if d:
            dt2 = datetime.combine(d, dt_time(12, 0))
            return timezone.make_aware(dt2, timezone=UTC)
    return None

def _custom_has_value(company: dict[str, Any], field_id: int, *, option_id: int | None = None, label: str | None = None) -> bool:
    values = _extract_custom_values(company, field_id)
    if option_id is not None:
        for v in values:
            if int(v.get("enum_id") or 0) == int(option_id):
                return True
    if label:
        lab = _norm(label)
        for v in values:
            if _norm(str(v.get("value") or "")) == lab:
                return True
    return False


@dataclass
class AmoMigrateResult:
    companies_seen: int = 0
    companies_matched: int = 0  # всего по фильтру
    companies_batch: int = 0  # обработано в этой пачке
    companies_offset: int = 0
    companies_next_offset: int = 0
    companies_has_more: bool = False
    companies_created: int = 0
    companies_updated: int = 0

    tasks_seen: int = 0
    tasks_created: int = 0
    tasks_skipped_existing: int = 0
    tasks_skipped_old: int = 0
    tasks_updated: int = 0
    tasks_preview: list[dict] | None = None

    notes_seen: int = 0
    notes_processed: int = 0  # дошли до create/update/skip; сумма: would_add+would_update+skipped_existing (dry) или created+updated+skipped_existing (real)
    notes_created: int = 0
    notes_skipped_existing: int = 0
    notes_skipped_no_changes: int = 0  # существующая заметка, обновление не требуется (= skipped_existing)
    notes_updated: int = 0
    notes_preview: list[dict] | None = None

    contacts_seen: int = 0
    contacts_created: int = 0
    contacts_updated: int = 0
    contacts_skipped: int = 0  # пропущенные контакты
    contacts_preview: list[dict] | None = None  # для dry-run отладки

    companies_updates_preview: list[dict] | None = None  # diff изменений компаний при dry-run
    contacts_updates_preview: list[dict] | None = None  # diff изменений контактов при dry-run

    preview: list[dict] | None = None
    
    error: str | None = None  # ошибка миграции (если была)
    error_traceback: str | None = None  # полный traceback ошибки
    
    # Для структурированного dry-run отчёта
    warnings: list[str] = None  # предупреждения (например, контакт связан с несколькими компаниями)
    
    # Метрики для валидации данных
    phones_rejected_as_note: int = 0  # сколько "телефонных" строк ушло в NOTE
    phones_rejected_invalid: int = 0  # не прошло порог валидации
    phones_extracted_with_extension: int = 0  # сколько телефонов извлечено с extension
    position_phone_detected: int = 0  # сколько раз телефон обнаружен в POSITION
    position_rejected_as_phone: int = 0  # сколько должностей распознано как телефон (устаревшее, используем position_phone_detected)
    name_cleaned_extension_moved_to_note: int = 0  # сколько раз "доб./ext" вынесено из имени
    name_instructions_moved_to_note: int = 0  # алиас к name_cleaned_extension_moved_to_note (инструкции из имени в note)
    unknown_phone_enum_code_count: int = 0  # сколько раз встречен неизвестный enum_code телефона
    emails_rejected_invalid_format: int = 0  # сколько email отклонено из-за невалидного формата
    fields_skipped_to_prevent_blank_overwrite: int = 0  # сколько полей пропущено, чтобы не затереть непустые значения
    skynet_phone_values_rejected: int = 0  # Skynet-поле: значения, не распознанные как телефон (произвольный текст)
    skynet_phones_added: int = 0  # CompanyPhone: добавлено номеров из поля 309609 с comment=SKYNET
    company_phones_rejected_invalid: int = 0  # CompanyPhone: пропущено добавление из-за невалидного номера (не E.164)
    
    # Детальные причины для dry-run логирования
    skip_reasons: list[dict[str, Any]] = None  # список причин пропуска/изменений: [{"type": "skip_POSITION", "reason": "looks like phone", "value": "...", "contact_id": ...}, ...]
    
    # Метаданные пагинации
    companies_fetch_truncated: bool = False  # была ли обрезана выборка компаний
    companies_pages_fetched: int = 0  # сколько страниц получено
    companies_elements_fetched: int = 0  # сколько элементов получено
    
    # Статистика по заметкам
    notes_bulk_supported: bool | None = None  # поддерживается ли bulk endpoint для заметок
    notes_fetch_mode: str = "unknown"  # "bulk" или "per_company"
    
    # Счетчики для dry-run (would_* вместо created/updated)
    companies_would_create: int = 0
    companies_would_update: int = 0
    contacts_would_create: int = 0
    contacts_would_update: int = 0
    notes_would_add: int = 0
    notes_would_update: int = 0
    tasks_would_create: int = 0
    tasks_would_update: int = 0
    skipped_writes_dry_run: int = 0  # счетчик пропущенных write-операций в dry-run
    
    def get_dry_run_report(self) -> dict[str, Any]:
        """
        Возвращает структурированный dry-run отчёт в формате JSON.
        
        Формат:
        {
          "companies": {
            "total": 10,
            "created": 10,
            "updated": 0
          },
          "contacts": {
            "total": 27,
            "new": 25,
            "skipped": 2
          },
          "fields": {
            "company": ["рабочее время", "дни недели", "перерыв"],
            "contact": ["должность", "примечание", "день рождения", "холодный звонок"]
          },
          "warnings": [
            "Контакт 123456 связан с несколькими компаниями — использована первая"
          ],
          "skip_reasons": [
            {"type": "skip_POSITION", "reason": "looks like phone", "value": "...", "contact_id": 123456},
            {"type": "skip_PHONE", "reason": "invalid after normalization", "value": "...", "contact_id": 123456},
            {"type": "move_PHONE_text_to_NOTE", "reason": "contains instruction keywords", "value": "...", "contact_id": 123456},
            {"type": "dedup_PHONE", "reason": "already exists", "value": "...", "contact_id": 123456}
          ],
          "metrics": {
            "phones_rejected_as_note": 0,
            "phones_rejected_invalid": 0,
            "phones_extracted_with_extension": 0,
            "position_phone_detected": 0,
            "position_rejected_as_phone": 0,
            "name_cleaned_extension_moved_to_note": 0,
            "unknown_phone_enum_code_count": 0,
            "emails_rejected_invalid_format": 0,
            "fields_skipped_to_prevent_blank_overwrite": 0
          }
        }
        """
        # Собираем уникальные поля компаний из custom_fields_values
        company_fields = set()
        if self.companies_updates_preview:
            for update in self.companies_updates_preview:
                if isinstance(update, dict):
                    amo_data = update.get("amo_data") or {}
                    custom_fields = amo_data.get("custom_fields_values") or []
                    for cf in custom_fields:
                        if isinstance(cf, dict):
                            field_name = str(cf.get("field_name") or "").strip()
                            if field_name:
                                company_fields.add(field_name)
        
        # Собираем уникальные поля контактов из custom_fields_values
        contact_fields = set()
        if self.contacts_preview:
            for contact_preview in self.contacts_preview:
                if isinstance(contact_preview, dict):
                    all_custom_fields = contact_preview.get("all_custom_fields") or []
                    for cf in all_custom_fields:
                        if isinstance(cf, dict):
                            field_name = str(cf.get("field_name") or "").strip()
                            if field_name:
                                contact_fields.add(field_name)
        
        # Определяем, dry-run это или real-run
        is_dry_run = (self.companies_would_create > 0 or self.companies_would_update > 0 or 
                     self.contacts_would_create > 0 or self.contacts_would_update > 0 or
                     self.notes_would_add > 0 or self.notes_would_update > 0 or
                     self.tasks_would_create > 0 or self.tasks_would_update > 0)
        
        result = {
            "companies": {
                "total": self.companies_batch,
            },
            "contacts": {
                "total": self.contacts_seen,
                "skipped": self.contacts_skipped,
            },
            "notes": {
                "found": self.notes_seen,
            },
            "tasks": {
                "found": self.tasks_seen,
            },
            "fields": {
                "company": sorted(list(company_fields)),
                "contact": sorted(list(contact_fields)),
            },
            "warnings": self.warnings or [],
            "skip_reasons": self.skip_reasons or [],
            "metrics": {
                "phones_rejected_as_note": self.phones_rejected_as_note,
                "phones_rejected_invalid": self.phones_rejected_invalid,
                "position_rejected_as_phone": self.position_rejected_as_phone,
                "name_cleaned_extension_moved_to_note": self.name_cleaned_extension_moved_to_note,
                "name_instructions_moved_to_note": self.name_instructions_moved_to_note,
                "skynet_phone_values_rejected": self.skynet_phone_values_rejected,
                "skynet_phones_added": self.skynet_phones_added,
                "company_phones_rejected_invalid": self.company_phones_rejected_invalid,
                "skipped_writes_dry_run": self.skipped_writes_dry_run,
            },
            "pagination": {
                "companies_fetch_truncated": self.companies_fetch_truncated,
                "companies_pages_fetched": self.companies_pages_fetched,
                "companies_elements_fetched": self.companies_elements_fetched,
            },
            "notes": {
                "bulk_supported": self.notes_bulk_supported,
                "fetch_mode": self.notes_fetch_mode,
            },
        }
        
        if is_dry_run:
            # Dry-run: показываем would_*
            result["companies"]["would_create"] = self.companies_would_create
            result["companies"]["would_update"] = self.companies_would_update
            result["contacts"]["would_create"] = self.contacts_would_create
            result["contacts"]["would_update"] = self.contacts_would_update
            result["notes"]["processed"] = self.notes_processed
            result["notes"]["would_add"] = self.notes_would_add
            result["notes"]["would_update"] = self.notes_would_update
            result["notes"]["skipped_already_present"] = self.notes_skipped_existing
            result["notes"]["skipped_no_changes"] = self.notes_skipped_no_changes
            result["tasks"]["would_create"] = self.tasks_would_create
            result["tasks"]["would_update"] = self.tasks_would_update
        else:
            # Real-run: показываем created/updated
            result["companies"]["created"] = self.companies_created
            result["companies"]["updated"] = self.companies_updated
            result["contacts"]["created"] = self.contacts_created
            result["contacts"]["updated"] = self.contacts_updated
            result["notes"]["processed"] = self.notes_processed
            result["notes"]["added"] = self.notes_created
            result["notes"]["updated"] = self.notes_updated
            result["notes"]["skipped_already_present"] = self.notes_skipped_existing
            result["notes"]["skipped_no_changes"] = self.notes_skipped_no_changes
            result["tasks"]["created"] = self.tasks_created
            result["tasks"]["updated"] = self.tasks_updated
        
        return result


def fetch_amo_users(client: AmoClient) -> list[dict[str, Any]]:
    """
    Получает список пользователей из AmoCRM.
    Если long-lived token не имеет прав на /api/v4/users (403), возвращает пустой список.
    Rate limiting применяется автоматически в AmoClient.
    """
    try:
        return client.get_all_pages("/api/v4/users", embedded_key="users", limit=50, max_pages=20)
    except AmoApiError as e:
        # Если 403 Forbidden - long-lived token не имеет прав на доступ к пользователям
        if "403" in str(e) or "Forbidden" in str(e):
            logger.warning(
                "Long-lived token не имеет прав на доступ к /api/v4/users. "
                "Для доступа к списку пользователей используйте OAuth токен. "
                "Продолжаем без списка пользователей."
            )
            return []
        # Для других ошибок пробрасываем исключение
        raise


def fetch_company_custom_fields(client: AmoClient) -> list[dict[str, Any]]:
    data = client.get("/api/v4/companies/custom_fields") or {}
    emb = data.get("_embedded") or {}
    fields = emb.get("custom_fields") or []
    return fields if isinstance(fields, list) else []


def _field_options(field: dict[str, Any]) -> list[dict[str, Any]]:
    # мультиселекты обычно имеют enums
    enums = field.get("enums") or {}
    out = []
    if isinstance(enums, dict):
        for k, v in enums.items():
            try:
                out.append({"id": int(k), "value": str(v)})
            except Exception:
                pass
    return out


def fetch_companies_by_responsible(
    client: AmoClient, 
    responsible_user_id: int, 
    *, 
    limit_pages: int | None = None,  # None = безлимитно (с safety cap), int = ограничение
    with_contacts: bool = False,
    return_meta: bool = False
) -> list[dict[str, Any]] | tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Получает компании по ответственному пользователю.
    Rate limiting применяется автоматически в AmoClient.
    ВСЕГДА запрашиваем БЕЗ контактов (with_contacts=False) - контакты получаем отдельно.
    
    Args:
        limit_pages: Максимальное количество страниц. None = безлимитно (с safety cap 10_000).
        return_meta: Если True, возвращает tuple (companies, pagination_meta).
        
    Returns:
        list[dict] или tuple[list[dict], dict]: Список компаний или (список, метаданные)
        Метаданные содержат: pages_fetched, elements_fetched, truncated, limit
    """
    params = {f"filter[responsible_user_id]": responsible_user_id, "with": "custom_fields"}
    # НЕ запрашиваем contacts здесь - это создает огромные ответы и вызывает 504
    # Контакты получаем отдельно через filter[company_id][]
    
    # Для компаний используем увеличенный max_pages или None (безлимитно с safety cap)
    # Safety cap: 10_000 страниц (примерно 250_000 компаний при limit=25)
    max_pages_value = limit_pages if limit_pages is not None else 10_000
    
    result = client.get_all_pages(
        "/api/v4/companies",
        params=params,
        embedded_key="companies",
        limit=25,  # Оптимальный размер: не слишком большой (504), не слишком маленький
        max_pages=max_pages_value,
        return_meta=return_meta,
    )
    
    # Tolerant unpack: обрабатываем как tuple, так и list для обратной совместимости
    if return_meta:
        if isinstance(result, tuple) and len(result) == 2:
            companies, pagination_meta = result
        else:
            # Если пришел list вместо tuple - возвращаем с пустой мета
            companies, pagination_meta = result, {}
        # Логируем метаданные пагинации
        if pagination_meta.get("truncated"):
            logger.warning(
                f"fetch_companies_by_responsible: пагинация обрезана (truncated=True). "
                f"Страниц: {pagination_meta['pages_fetched']}, элементов: {pagination_meta['elements_fetched']}, "
                f"лимит: {pagination_meta['limit']}"
            )
        return companies, pagination_meta
    else:
        # return_meta == False: если пришел tuple, берем только список
        if isinstance(result, tuple) and len(result) == 2:
            companies, _meta = result
            return companies
        return result


def fetch_tasks_for_companies(client: AmoClient, company_ids: list[int]) -> list[dict[str, Any]]:
    """
    Получает задачи для компаний.
    Rate limiting применяется автоматически в AmoClient.
    ОПТИМИЗАЦИЯ: увеличен размер батча до 20 для уменьшения количества запросов.
    
    Согласно официальной документации amoCRM API v4:
    https://www.amocrm.ru/developers/content/crm_platform/tasks-api
    Endpoint /api/v4/tasks поддерживает фильтры filter[entity_type] и filter[entity_id][]
    """
    if not company_ids:
        logger.debug("fetch_tasks_for_companies: company_ids пуст, возвращаем []")
        return []
    
    out: list[dict[str, Any]] = []
    batch_size = 50  # ОПТИМИЗАЦИЯ: увеличен до максимума API (50) для ускорения (меньше запросов)
    
    logger.info(f"fetch_tasks_for_companies: запрашиваем задачи для {len(company_ids)} компаний (batch_size={batch_size})")
    
    for i in range(0, len(company_ids), batch_size):
        ids = company_ids[i : i + batch_size]
        logger.debug(f"fetch_tasks_for_companies: батч {i//batch_size + 1}, компаний: {len(ids)}, IDs: {ids[:5]}...")
        
        try:
            # Согласно документации: filter[entity_type]=companies, filter[entity_id][]=[id1, id2, ...]
            # AmoClient должен обработать filter[entity_id] как filter[entity_id][]
            tasks_batch = client.get_all_pages(
                "/api/v4/tasks",
                params={
                    "filter[entity_type]": "companies",
                    "filter[entity_id]": ids,  # AmoClient обработает как filter[entity_id][]=...
                },
                embedded_key="tasks",
                limit=50,
                max_pages=20,
            )
            out.extend(tasks_batch)
            logger.debug(f"fetch_tasks_for_companies: получено {len(tasks_batch)} задач для батча {i//batch_size + 1}")
        except Exception as e:
            logger.warning(
                f"fetch_tasks_for_companies: ошибка при получении задач для батча компаний ({len(ids)}): {e}",
                exc_info=False,
            )
            continue
    
    logger.info(f"fetch_tasks_for_companies: всего получено {len(out)} задач для {len(company_ids)} компаний")
    return out


def fetch_notes_for_companies_bulk(client: AmoClient, company_ids: list[int], *, batch_size: int = 200) -> list[dict[str, Any]]:
    """
    ОПТИМИЗИРОВАННАЯ версия получения заметок компаний через bulk-запросы.

    Использует /api/v4/notes с фильтром по entity_type=companies и entity_id[] (батчами),
    аналогично fetch_notes_for_contacts_bulk.

    Важно: в некоторых аккаунтах/тарифах/правах endpoint может быть недоступен —
    тогда вызывающая сторона должна сделать fallback на fetch_notes_for_companies (legacy).
    
    Согласно официальной документации amoCRM API v4:
    https://www.amocrm.ru/developers/content/crm_platform/events-and-notes
    Endpoint /api/v4/notes поддерживает фильтры filter[entity_type] и filter[entity_id][]
    """
    global _notes_bulk_supported
    
    if not company_ids:
        return []

    out: list[dict[str, Any]] = []
    for i in range(0, len(company_ids), batch_size):
        batch_ids = company_ids[i : i + batch_size]
        try:
            # Добавляем extra_delay=0.2s между страницами для снижения нагрузки на API
            # Согласно документации: filter[entity_type]=companies, filter[entity_id][]=[id1, id2, ...]
            notes = client.get_all_pages(
                "/api/v4/notes",
                params={
                    "filter[entity_type]": "companies",
                    "filter[entity_id]": batch_ids,  # AmoClient обработает как filter[entity_id][]=...
                },
                embedded_key="notes",
                limit=250,
                max_pages=100,
                extra_delay=0.2,  # Дополнительная задержка 0.2s между страницами заметок
                return_meta=False,
            )
            out.extend([n for n in notes if isinstance(n, dict)])
            # Если успешно получили данные - bulk поддерживается
            if _notes_bulk_supported is None:
                _notes_bulk_supported = True
        except RateLimitError as e:
            # Rate limit после всех retry - поднимаем исключение, не пропускаем тихо
            logger.error(
                f"fetch_notes_for_companies_bulk: Rate limit исчерпан для батча компаний ({len(batch_ids)}). "
                f"Получено заметок: {len(out)}. Импорт заметок компаний прерван."
            )
            raise
        except AmoApiError as e:
            # Проверяем, это 404 или 405? (endpoint не поддерживается)
            error_str = str(e)
            is_404_or_405 = "404" in error_str or "405" in error_str or "Not Found" in error_str or "Method Not Allowed" in error_str
            
            if is_404_or_405:
                # Bulk endpoint недоступен - устанавливаем флаг и логируем один раз
                if _notes_bulk_supported is None:
                    _notes_bulk_supported = False
                    logger.warning(
                        f"fetch_notes_for_companies_bulk: bulk notes endpoint недоступен (404/405), "
                        f"переключаюсь на per-company endpoint. Батч компаний: {len(batch_ids)}"
                    )
                # Не поднимаем исключение - продолжаем с fallback
                break
            else:
                # Другие ошибки API - логируем и продолжаем для следующих батчей
                logger.warning(
                    f"fetch_notes_for_companies_bulk: API ошибка для батча компаний ({len(batch_ids)}): {e}",
                    exc_info=False,  # Не спамим traceback, только сообщение
                )
                continue
        except Exception as e:
            # Неожиданные ошибки - логируем без traceback (только в debug режиме)
            logger.warning(
                f"fetch_notes_for_companies_bulk: ошибка для батча компаний ({len(batch_ids)}): {e}",
                exc_info=False,
            )
            continue
    return out


def fetch_notes_for_companies(client: AmoClient, company_ids: list[int]) -> list[dict[str, Any]]:
    """
    Получает заметки для компаний через per-company endpoint.
    
    Согласно официальной документации amoCRM API v4:
    https://www.amocrm.ru/developers/content/crm_platform/events-and-notes
    Endpoint GET /api/v4/{entity_type}/{entity_id}/notes возвращает заметки для конкретной сущности.
    
    ВАЖНО: Endpoint /api/v4/notes с фильтрами НЕ поддерживается (возвращает 404).
    Используем только per-company endpoint согласно документации.
    """
    global _notes_bulk_supported
    
    if not company_ids:
        return []
    
    # Согласно документации и практике: /api/v4/notes с фильтрами не работает (404)
    # Используем только per-company endpoint
    if _notes_bulk_supported is None:
        # Первый запуск: пробуем bulk для проверки, но сразу переключаемся на per-company
        logger.info(
            f"fetch_notes_for_companies: endpoint /api/v4/notes не поддерживается (404 по документации), "
            f"используем per-company endpoint /api/v4/companies/{{id}}/notes для {len(company_ids)} компаний"
        )
        _notes_bulk_supported = False  # Помечаем как недоступный, чтобы не пытаться снова
    elif _notes_bulk_supported is False:
        logger.debug("fetch_notes_for_companies: bulk notes недоступен, используем per-company endpoint")
    
    # Всегда используем per-company endpoint (согласно документации)
    return _fetch_notes_per_company(client, company_ids)


def _fetch_notes_per_company(client: AmoClient, company_ids: list[int]) -> list[dict[str, Any]]:
    """
    Получает заметки для компаний поштучно через per-company endpoint.
    
    Используется как fallback когда bulk endpoint недоступен.
    
    Согласно официальной документации amoCRM API v4:
    https://www.amocrm.ru/developers/content/crm_platform/events-and-notes
    Endpoint GET /api/v4/{entity_type}/{entity_id}/notes возвращает заметки для конкретной сущности.
    """
    out: list[dict[str, Any]] = []
    logger.info(f"_fetch_notes_per_company: получаем заметки для {len(company_ids)} компаний через per-company endpoint")
    
    for cid in company_ids:
        try:
            # Согласно документации: GET /api/v4/companies/{id}/notes
            notes = client.get_all_pages(
                f"/api/v4/companies/{int(cid)}/notes",
                params={},
                embedded_key="notes",
                limit=50,
                max_pages=10,
            )
            notes_list = [n for n in notes if isinstance(n, dict)]
            out.extend(notes_list)
            if notes_list:
                logger.debug(f"_fetch_notes_per_company: получено {len(notes_list)} заметок для компании {cid}")
        except Exception as e:
            logger.warning(
                f"_fetch_notes_per_company: ошибка при получении заметок для компании {cid}: {e}",
                exc_info=False,
            )
            continue
    
    logger.info(f"_fetch_notes_per_company: всего получено {len(out)} заметок для {len(company_ids)} компаний")
    return out


def fetch_contacts_for_companies(client: AmoClient, company_ids: list[int]) -> list[dict[str, Any]]:
    """
    Получает контакты компаний из amoCRM.
    Согласно документации AmoCRM API v4:
    1. Можно использовать filter[company_id]=ID для одного ID (не массив!)
    2. Или запрашивать компании с with=contacts и извлекать _embedded.contacts
    
    Используем оба способа для надежности.
    Rate limiting применяется автоматически в AmoClient.
    """
    if not company_ids:
        logger.info("fetch_contacts_for_companies: company_ids пуст, возвращаем []")
        return []
    out: list[dict[str, Any]] = []
    
    logger.info(f"fetch_contacts_for_companies: начинаем поиск контактов для {len(company_ids)} компаний: {company_ids[:5]}...")
    
    # Способ 1: Запрашиваем каждую компанию с with=contacts
    # Это самый надежный способ согласно документации
    # ОПТИМИЗАЦИЯ: уменьшаем логирование для ускорения
    method1_contacts_count = 0
    for idx, company_id in enumerate(company_ids):
        try:
            # Получаем компанию с контактами
            # Логируем только каждую 10-ю компанию для ускорения
            if idx % 10 == 0 or idx == len(company_ids) - 1:
                logger.info(f"fetch_contacts_for_companies: запрашиваем компанию {company_id} с with=contacts ({idx + 1}/{len(company_ids)})")
            company_data = client.get(
                f"/api/v4/companies/{company_id}",
                params={"with": "custom_fields,contacts"}  # Только custom_fields и contacts, БЕЗ notes
            )
            
            if isinstance(company_data, dict):
                embedded = company_data.get("_embedded") or {}
                contacts = embedded.get("contacts") or []
                if isinstance(contacts, list) and contacts:
                    # ОПТИМИЗАЦИЯ: логируем только при необходимости
                    if idx % 10 == 0 or len(contacts) > 0:
                        logger.debug(f"fetch_contacts_for_companies: компания {company_id}: найдено {len(contacts)} контактов через with=contacts")
                    # Добавляем company_id к каждому контакту для удобства
                    for contact in contacts:
                        if isinstance(contact, dict):
                            # Сохраняем связь с компанией
                            # ВАЖНО: у контакта может уже быть _embedded.companies, нужно добавить нашу компанию в список
                            if "_embedded" not in contact:
                                contact["_embedded"] = {}
                            
                            # Получаем существующий список компаний или создаем новый
                            existing_companies = contact["_embedded"].get("companies") or []
                            if not isinstance(existing_companies, list):
                                existing_companies = []
                            
                            # Проверяем, есть ли уже эта компания в списке
                            company_already_present = False
                            for comp_ref in existing_companies:
                                if isinstance(comp_ref, dict) and int(comp_ref.get("id") or 0) == company_id:
                                    company_already_present = True
                                    break
                                elif isinstance(comp_ref, int) and comp_ref == company_id:
                                    company_already_present = True
                                    break
                            
                            # Добавляем компанию, если её еще нет
                            if not company_already_present:
                                existing_companies.append({"id": company_id})
                            
                            contact["_embedded"]["companies"] = existing_companies
                            
                            # ОТЛАДКА: логируем структуру первого контакта
                            if method1_contacts_count == 0:
                                contact_id_debug = contact.get("id")
                                logger.info(f"fetch_contacts_for_companies: структура первого контакта (id={contact_id_debug}):")
                                logger.info(f"  - has _embedded: {'_embedded' in contact}")
                                logger.info(f"  - _embedded.companies: {contact.get('_embedded', {}).get('companies', [])}")
                                logger.info(f"  - contact keys: {list(contact.keys())[:10]}")
                    out.extend(contacts)
                    method1_contacts_count += len(contacts)
                else:
                    # ОПТИМИЗАЦИЯ: убрано избыточное логирование структуры ответа
                    pass
            else:
                logger.warning(f"fetch_contacts_for_companies: компания {company_id}: неожиданный тип ответа: {type(company_data)}")
        except Exception as e:
            logger.warning(f"fetch_contacts_for_companies: ошибка при получении компании {company_id} с контактами: {e}", exc_info=True)
            # Продолжаем для следующих компаний
            continue
    
    logger.info(f"fetch_contacts_for_companies: способ 1 (with=contacts): найдено {method1_contacts_count} контактов из {len(company_ids)} компаний")
    
    # КРИТИЧЕСКИ ВАЖНО: контакты из _embedded.contacts могут быть в упрощенном формате без custom_fields_values
    # Нужно запросить полные данные контактов отдельно, если они есть
    if out:
        logger.info(f"fetch_contacts_for_companies: проверяем, нужны ли полные данные для {len(out)} контактов...")
        
        # Проверяем, есть ли у контактов custom_fields_values
        contacts_need_full_data = []
        contact_ids = []
        for contact in out:
            if not isinstance(contact, dict):
                continue
            contact_id = int(contact.get("id") or 0)
            if not contact_id:
                continue
            
            # Проверяем, есть ли custom_fields_values
            has_custom_fields = bool(contact.get("custom_fields_values"))
            if not has_custom_fields:
                contacts_need_full_data.append(contact)
                contact_ids.append(contact_id)
                logger.debug(f"fetch_contacts_for_companies: контакт {contact_id} не имеет custom_fields_values, нужны полные данные")
            else:
                custom_fields = contact.get('custom_fields_values') or []
                custom_fields_count = len(custom_fields) if isinstance(custom_fields, list) else 0
                logger.debug(f"fetch_contacts_for_companies: контакт {contact_id} уже имеет custom_fields_values ({custom_fields_count} полей)")
        
        # Запрашиваем полные данные только для контактов без custom_fields_values
        if contact_ids:
            logger.info(f"fetch_contacts_for_companies: запрашиваем полные данные для {len(contact_ids)} контактов без custom_fields_values...")
            full_contacts_map: dict[int, dict[str, Any]] = {}
            batch_size = 50  # Лимит AmoCRM API (максимальный размер батча)
            for i in range(0, len(contact_ids), batch_size):
                batch_ids = contact_ids[i:i + batch_size]
                try:
                    # Запрашиваем контакты с полными данными через filter[id][]
                    # AmoClient._request обрабатывает списки правильно
                    contacts_batch_data = client.get(
                        "/api/v4/contacts",
                        params={
                            "filter[id]": batch_ids,  # Массив ID контактов - AmoClient обработает как filter[id][]=...
                            "with": "custom_fields",  # Получаем custom_fields
                        }
                    )
                    
                    if isinstance(contacts_batch_data, dict):
                        embedded_batch = contacts_batch_data.get("_embedded") or {}
                        contacts_batch = embedded_batch.get("contacts") or []
                        if isinstance(contacts_batch, list):
                            for full_contact in contacts_batch:
                                if isinstance(full_contact, dict):
                                    full_contact_id = int(full_contact.get("id") or 0)
                                    if full_contact_id:
                                        full_contacts_map[full_contact_id] = full_contact
                                        # ОПТИМИЗАЦИЯ: убираем избыточное логирование для каждого контакта
                                        pass  # Логирование убрано для ускорения
                    
                except Exception as e:
                    logger.warning(f"fetch_contacts_for_companies: ошибка при получении полных данных контактов (batch {i//batch_size + 1}): {e}", exc_info=True)
                    continue
            
            # Заменяем упрощенные контакты на полные, сохраняя _embedded.companies
            logger.info(f"fetch_contacts_for_companies: получено полных данных для {len(full_contacts_map)} контактов из {len(contact_ids)} запрошенных")
            updated_out = []
            for contact in out:
                if not isinstance(contact, dict):
                    updated_out.append(contact)
                    continue
                
                contact_id = int(contact.get("id") or 0)
                if contact_id and contact_id in full_contacts_map:
                    # Берем полный контакт, но сохраняем _embedded.companies из упрощенного
                    full_contact = full_contacts_map[contact_id]
                    embedded_from_simple = contact.get("_embedded") or {}
                    companies_from_simple = embedded_from_simple.get("companies") or []
                    
                    # Сохраняем _embedded.companies в полном контакте
                    if not isinstance(full_contact.get("_embedded"), dict):
                        full_contact["_embedded"] = {}
                    if companies_from_simple:
                        full_contact["_embedded"]["companies"] = companies_from_simple
                    
                    updated_out.append(full_contact)
                    # ОПТИМИЗАЦИЯ: убираем избыточное логирование для каждого контакта
                    # Логируем только итоговую статистику
                else:
                    # Если полных данных нет, оставляем как есть (возможно, уже есть custom_fields_values)
                    updated_out.append(contact)
            
            out = updated_out
            logger.info(f"fetch_contacts_for_companies: обновлено {len(out)} контактов с полными данными")
        else:
            logger.info(f"fetch_contacts_for_companies: все контакты уже имеют custom_fields_values, дополнительный запрос не нужен")
    
    # Если через with=contacts ничего не нашли, пробуем способ 2: filter[company_id] для каждого ID
    if not out:
        logger.info("fetch_contacts_for_companies: через with=contacts контакты не найдены, пробуем filter[company_id] для каждой компании...")
        method2_contacts_count = 0
        for idx, company_id in enumerate(company_ids):
            try:
                # Согласно документации: filter[company_id]=ID (без [])
                # ОПТИМИЗАЦИЯ: логируем только каждую 10-ю компанию
                if idx % 10 == 0:
                    logger.info(f"fetch_contacts_for_companies: запрашиваем контакты через filter[company_id]={company_id} ({idx + 1}/{len(company_ids)})")
                # ОПТИМИЗАЦИЯ: используем get_all_pages для получения всех контактов компании
                contacts = client.get_all_pages(
                    "/api/v4/contacts",
                    params={
                        "filter[company_id]": company_id,  # БЕЗ [] - для одного ID
                        "with": "custom_fields",
                    },
                    embedded_key="contacts",
                    limit=250,
                    max_pages=10,  # Обычно у компании не более 10 страниц контактов
                )
                contacts_data = {"_embedded": {"contacts": contacts}} if contacts else {}
                
                if isinstance(contacts_data, dict):
                    embedded = contacts_data.get("_embedded") or {}
                    contacts = embedded.get("contacts") or []
                    if isinstance(contacts, list) and contacts:
                        # ОПТИМИЗАЦИЯ: убираем избыточное логирование
                        logger.debug(f"fetch_contacts_for_companies: компания {company_id}: найдено {len(contacts)} контактов через filter[company_id]")
                        # Добавляем company_id к каждому контакту
                        for contact in contacts:
                            if isinstance(contact, dict):
                                if "_embedded" not in contact:
                                    contact["_embedded"] = {}
                                if "companies" not in contact["_embedded"]:
                                    contact["_embedded"]["companies"] = [{"id": company_id}]
                        out.extend(contacts)
                        method2_contacts_count += len(contacts)
                    else:
                        logger.info(f"fetch_contacts_for_companies: компания {company_id}: контакты не найдены через filter[company_id] (пустой список)")
                else:
                    logger.warning(f"fetch_contacts_for_companies: компания {company_id}: неожиданный тип ответа через filter[company_id]: {type(contacts_data)}")
            except Exception as e:
                logger.warning(f"fetch_contacts_for_companies: ошибка при получении контактов через filter[company_id]={company_id}: {e}", exc_info=True)
                continue
        
        logger.info(f"fetch_contacts_for_companies: способ 2 (filter[company_id]): найдено {method2_contacts_count} контактов из {len(company_ids)} компаний")
    
    logger.info(f"fetch_contacts_for_companies: ИТОГО найдено {len(out)} контактов из {len(company_ids)} компаний")
    return out


def fetch_contacts_per_company_precise(client: AmoClient, company_ids: list[int]) -> tuple[list[dict[str, Any]], dict[int, int]]:
    """
    Получает контакты компаний через точный запрос для каждой компании.
    Использует filter[company_id]=ID для каждой компании отдельно.
    
    ОПТИМИЗАЦИЯ: Используется для небольших батчей (≤10 компаний).
    Возвращает только релевантные контакты, не требует фильтрации.
    
    Согласно документации AmoCRM API v4:
    - filter[company_id]=ID возвращает полные данные контакта сразу (с custom_fields_values)
    - Максимальный limit = 250
    
    Args:
        client: AmoClient для запросов
        company_ids: список ID компаний, для которых нужны контакты
        
    Returns:
        tuple[list[dict], dict[int, int]]: 
            - список контактов с полными данными (custom_fields, _embedded.companies)
            - словарь contact_id -> company_id для маппинга
    """
    if not company_ids:
        logger.info("fetch_contacts_per_company_precise: company_ids пуст, возвращаем []")
        return [], {}
    
    all_contacts: list[dict[str, Any]] = []
    contact_id_to_company_map: dict[int, int] = {}
    
    logger.info(f"fetch_contacts_per_company_precise: начинаем точное получение контактов для {len(company_ids)} компаний")
    
    for idx, company_id in enumerate(company_ids):
        try:
            # ОПТИМИЗАЦИЯ: используем filter[company_id]=ID (БЕЗ []) для точного запроса
            # Согласно документации: это возвращает только контакты для этой конкретной компании
            # И возвращает полные данные сразу (с custom_fields_values), в отличие от with=contacts
            contacts = client.get_all_pages(
                "/api/v4/contacts",
                params={
                    "filter[company_id]": company_id,  # БЕЗ [] - для одного ID (документация)
                    "with": "custom_fields",  # Получаем custom_fields сразу
                },
                embedded_key="contacts",
                limit=250,  # Максимальный limit согласно документации
                max_pages=10,  # Обычно у компании не более 10 страниц контактов
            )
            
            if contacts:
                logger.info(f"fetch_contacts_per_company_precise: компания {company_id}: найдено {len(contacts)} контактов")
                # Добавляем company_id к каждому контакту
                for contact in contacts:
                    if isinstance(contact, dict):
                        contact_id = int(contact.get("id") or 0)
                        if contact_id:
                            # Убеждаемся, что _embedded.companies заполнен
                            if "_embedded" not in contact:
                                contact["_embedded"] = {}
                            if "companies" not in contact["_embedded"] or not contact["_embedded"]["companies"]:
                                contact["_embedded"]["companies"] = [{"id": company_id}]
                            
                            all_contacts.append(contact)
                            contact_id_to_company_map[contact_id] = company_id
            else:
                logger.debug(f"fetch_contacts_per_company_precise: компания {company_id}: контакты не найдены")
                
        except Exception as e:
            logger.warning(f"fetch_contacts_per_company_precise: ошибка при получении контактов для компании {company_id}: {e}", exc_info=True)
            continue
    
    logger.info(f"fetch_contacts_per_company_precise: ИТОГО найдено {len(all_contacts)} контактов для {len(company_ids)} компаний")
    return all_contacts, contact_id_to_company_map


def fetch_contacts_medium_batch(client: AmoClient, company_ids: list[int]) -> tuple[list[dict[str, Any]], dict[int, int]]:
    """
    ОПТИМИЗИРОВАННОЕ получение контактов для средних батчей (11-30 компаний).
    
    Стратегия (на основе документации AmoCRM API v4):
    1. Запрашиваем компании с with=contacts (быстро, только ID контактов)
    2. Собираем все ID контактов
    3. Запрашиваем полные данные контактов батчами через filter[id][] (50 ID за раз)
    
    Преимущества:
    - Для 20 компаний: 1 запрос компаний + 1-2 запроса контактов = ~1-2 сек (вместо 3 сек)
    - Не требует фильтрации (контакты уже привязаны к компаниям)
    
    Args:
        client: AmoClient для запросов
        company_ids: список ID компаний, для которых нужны контакты
        
    Returns:
        tuple[list[dict], dict[int, int]]: 
            - список контактов с полными данными (custom_fields, _embedded.companies)
            - словарь contact_id -> company_id для маппинга
    """
    if not company_ids:
        logger.info("fetch_contacts_medium_batch: company_ids пуст, возвращаем []")
        return [], {}
    
    logger.info(f"fetch_contacts_medium_batch: начинаем оптимизированное получение контактов для {len(company_ids)} компаний")
    
    # Шаг 1: Запрашиваем компании с with=contacts (быстро, только ID контактов)
    # Согласно документации: _embedded[contacts] возвращает только id контакта
    all_contact_ids: set[int] = set()
    contact_id_to_company_map: dict[int, int] = {}
    
    for idx, company_id in enumerate(company_ids):
        try:
            company_data = client.get(
                f"/api/v4/companies/{company_id}",
                params={"with": "contacts"}  # Только contacts, без custom_fields (быстрее)
            )
            
            if isinstance(company_data, dict):
                embedded = company_data.get("_embedded") or {}
                contacts = embedded.get("contacts") or []
                if isinstance(contacts, list):
                    for contact in contacts:
                        if isinstance(contact, dict):
                            contact_id = int(contact.get("id") or 0)
                            if contact_id:
                                all_contact_ids.add(contact_id)
                                contact_id_to_company_map[contact_id] = company_id
        except Exception as e:
            logger.warning(f"fetch_contacts_medium_batch: ошибка при получении компании {company_id}: {e}", exc_info=True)
            continue
    
    if not all_contact_ids:
        logger.info(f"fetch_contacts_medium_batch: контакты не найдены для {len(company_ids)} компаний")
        return [], {}
    
    logger.info(f"fetch_contacts_medium_batch: найдено {len(all_contact_ids)} уникальных контактов, запрашиваем полные данные...")
    
    # Шаг 2: Запрашиваем полные данные контактов батчами через filter[id][]
    # Согласно документации: максимальный размер батча = 50
    all_contacts: list[dict[str, Any]] = []
    contact_ids_list = list(all_contact_ids)
    batch_size = 50
    
    for i in range(0, len(contact_ids_list), batch_size):
        batch_ids = contact_ids_list[i:i + batch_size]
        try:
            contacts_data = client.get(
                "/api/v4/contacts",
                params={
                    "filter[id]": batch_ids,  # Массив ID - AmoClient обработает как filter[id][]=...
                    "with": "custom_fields",  # Получаем полные данные
                }
            )
            
            if isinstance(contacts_data, dict):
                embedded = contacts_data.get("_embedded") or {}
                contacts = embedded.get("contacts") or []
                if isinstance(contacts, list):
                    for contact in contacts:
                        if isinstance(contact, dict):
                            contact_id = int(contact.get("id") or 0)
                            if contact_id and contact_id in contact_id_to_company_map:
                                company_id = contact_id_to_company_map[contact_id]
                                # Убеждаемся, что _embedded.companies заполнен
                                if "_embedded" not in contact:
                                    contact["_embedded"] = {}
                                if "companies" not in contact["_embedded"] or not contact["_embedded"]["companies"]:
                                    contact["_embedded"]["companies"] = [{"id": company_id}]
                                all_contacts.append(contact)
        except Exception as e:
            logger.warning(f"fetch_contacts_medium_batch: ошибка при получении полных данных контактов (batch {i//batch_size + 1}): {e}", exc_info=True)
            continue
    
    logger.info(f"fetch_contacts_medium_batch: ИТОГО получено {len(all_contacts)} контактов с полными данными для {len(company_ids)} компаний")
    return all_contacts, contact_id_to_company_map


def fetch_contacts_via_links(client: AmoClient, company_ids: list[int]) -> tuple[list[dict[str, Any]], dict[int, int], list[str]]:
    """
    Получает контакты компаний через Entity Links API (/api/v4/companies/links).
    
    Алгоритм (обязателен):
    1. Получить пачку компаний (например 10 / 50)
    2. За 1–2 запроса получить связи company → contact через companies/links
    3. Собрать уникальные contact_id
    4. Получить ТОЛЬКО эти контакты через GET /api/v4/contacts?filter[id][]=...
    
    Контакт всегда принадлежит только одной компании:
    - если встречается несколько — использовать первую
    - остальные игнорировать (можно логировать warning)
    
    Никаких fallback-веток и глобальных сканов.
    
    Args:
        client: AmoClient для запросов
        company_ids: список ID компаний, для которых нужны контакты
        
    Returns:
        tuple[list[dict], dict[int, int]]: 
            - список контактов с полными данными (custom_fields)
            - словарь contact_id -> company_id для маппинга (первая компания для каждого контакта)
    """
    if not company_ids:
        logger.info("fetch_contacts_via_links: company_ids пуст, возвращаем []")
        return [], {}
    
    logger.info(f"fetch_contacts_via_links: начинаем получение контактов для {len(company_ids)} компаний через Entity Links API")
    
    # Шаг 1: Получаем связи company → contact через /api/v4/companies/links
    # Разбиваем на батчи по 50 компаний (максимальный размер фильтра)
    contact_id_to_company_map: dict[int, int] = {}
    all_contact_ids: set[int] = set()
    warnings: list[str] = []
    
    batch_size = 50
    for i in range(0, len(company_ids), batch_size):
        batch_company_ids = company_ids[i:i + batch_size]
        logger.info(f"fetch_contacts_via_links: запрашиваем связи для батча компаний {i//batch_size + 1} ({len(batch_company_ids)} компаний)")
        
        try:
            # Используем только /api/v4/companies/links
            # ВАЖНО: ключ filter[entity_id] БЕЗ [] - AmoClient сам добавит [] при сериализации списка
            # Если передать filter[entity_id][], получится filter[entity_id][][] и amo не видит фильтр
            links_data = client.get(
                "/api/v4/companies/links",
                params={
                    "filter[entity_id]": batch_company_ids,  # Массив ID компаний (БЕЗ [] в ключе!)
                }
            )
            
            if isinstance(links_data, dict):
                embedded = links_data.get("_embedded") or {}
                links = embedded.get("links") or []
                if isinstance(links, list):
                    # Логирование для отладки (до стабилизации)
                    import json
                    logger.info(f"fetch_contacts_via_links: links count={len(links)}")
                    if links:
                        try:
                            links_sample = links[:3]
                            links_json = json.dumps(links_sample, ensure_ascii=False, indent=2)
                            # Ограничиваем размер до ~2000 символов
                            links_json_limited = links_json[:2000]
                            if len(links_json) > 2000:
                                links_json_limited += "... (truncated)"
                            logger.info(f"fetch_contacts_via_links: LINKS SAMPLE={links_json_limited}")
                        except Exception as e:
                            logger.warning(f"fetch_contacts_via_links: ошибка при логировании links: {e}")
                    
                    # Обрабатываем связи с устойчивым парсингом
                    links_processed = 0
                    links_skipped_no_company = 0
                    links_skipped_no_contact = 0
                    links_skipped_wrong_type = 0
                    skipped_types: dict[str, int] = {}  # Счетчик типов, которые пропускаем
                    
                    for link in links:
                        if not isinstance(link, dict):
                            continue
                        
                        # Устойчивый парсинг company_id: может быть entity_id или from_entity_id
                        company_id = (
                            int(link.get("entity_id") or 0) or
                            int(link.get("from_entity_id") or 0)
                        )
                        
                        # Устойчивый парсинг contact_id: может быть to_entity_id или to_entity.id
                        contact_id = 0
                        to_entity = link.get("to_entity")
                        if isinstance(to_entity, dict):
                            contact_id = int(to_entity.get("id") or 0)
                        if not contact_id:
                            contact_id = int(link.get("to_entity_id") or 0)
                        
                        # Устойчивый парсинг типа: может быть to_entity_type или to_entity.type
                        entity_type = ""
                        if isinstance(to_entity, dict):
                            entity_type = str(to_entity.get("type") or "").lower()
                        if not entity_type:
                            entity_type = str(link.get("to_entity_type") or "").lower()
                        
                        # Проверяем company_id
                        if not company_id:
                            links_skipped_no_company += 1
                            continue
                        
                        # Проверяем, что компания в нашем списке
                        if company_id not in batch_company_ids:
                            links_skipped_no_company += 1
                            continue
                        
                        # Проверяем contact_id
                        if not contact_id:
                            links_skipped_no_contact += 1
                            continue
                        
                        # Фильтрация по типу: допускаем "contacts" и "contact"
                        # Если to_entity_type отсутствует, но есть to_entity_id - допускаем запись (возможно это контакт)
                        if entity_type:
                            if entity_type not in ("contact", "contacts"):
                                links_skipped_wrong_type += 1
                                # Логируем тип, который пропускаем
                                if entity_type not in skipped_types:
                                    skipped_types[entity_type] = 0
                                skipped_types[entity_type] += 1
                                continue
                        # Если тип отсутствует, но есть contact_id - считаем что это контакт
                        # (допускаем запись, если удаётся извлечь to_entity_id)
                        
                        # Если контакт уже связан с другой компанией - используем первую (логируем warning)
                        if contact_id in contact_id_to_company_map:
                            existing_company_id = contact_id_to_company_map[contact_id]
                            if existing_company_id != company_id:
                                warning_msg = f"Контакт {contact_id} связан с несколькими компаниями ({existing_company_id}, {company_id}) — использована первая ({existing_company_id})"
                                if warning_msg not in warnings:
                                    warnings.append(warning_msg)
                                    logger.warning(warning_msg)
                        else:
                            # Первая компания для этого контакта
                            contact_id_to_company_map[contact_id] = company_id
                            all_contact_ids.add(contact_id)
                            links_processed += 1
                    
                    logger.info(f"fetch_contacts_via_links: обработано связей: processed={links_processed}, skipped_no_company={links_skipped_no_company}, skipped_no_contact={links_skipped_no_contact}, skipped_wrong_type={links_skipped_wrong_type}")
                    if skipped_types:
                        logger.info(f"fetch_contacts_via_links: пропущенные типы связей: {dict(skipped_types)}")
                else:
                    logger.warning(f"fetch_contacts_via_links: неожиданный тип links в ответе: {type(links)}")
            else:
                logger.warning(f"fetch_contacts_via_links: неожиданный тип ответа: {type(links_data)}")
                
        except Exception as e:
            logger.error(f"fetch_contacts_via_links: ошибка при получении связей для батча компаний: {e}", exc_info=True)
            # НЕ делаем fallback - просто пропускаем этот батч
            continue
    
    if not all_contact_ids:
        logger.info(f"fetch_contacts_via_links: связи не найдены для {len(company_ids)} компаний")
        return [], {}, []
    
    logger.info(f"fetch_contacts_via_links: найдено {len(all_contact_ids)} уникальных контактов для {len(company_ids)} компаний")
    
    # Шаг 2: Получаем полные данные контактов через filter[id]
    # ВАЖНО: ключ filter[id] БЕЗ [] - AmoClient сам добавит [] при сериализации списка
    # Разбиваем на батчи по 50 контактов (максимальный размер фильтра)
    all_contacts: list[dict[str, Any]] = []
    contact_ids_list = list(all_contact_ids)
    contact_batch_size = 50
    
    logger.info(f"fetch_contacts_via_links: запрашиваем полные данные для {len(contact_ids_list)} контактов (разбито на {(len(contact_ids_list) + contact_batch_size - 1) // contact_batch_size} батчей)")
    
    for i in range(0, len(contact_ids_list), contact_batch_size):
        batch_contact_ids = contact_ids_list[i:i + contact_batch_size]
        logger.info(f"fetch_contacts_via_links: запрашиваем полные данные для батча контактов {i//contact_batch_size + 1} ({len(batch_contact_ids)} контактов)")
        
        try:
            # ВАЖНО: ключ filter[id] БЕЗ [] - AmoClient сам добавит [] при сериализации списка
            contacts_data = client.get(
                "/api/v4/contacts",
                params={
                    "filter[id]": batch_contact_ids,  # Массив ID контактов (БЕЗ [] в ключе!)
                    "with": "custom_fields",  # Получаем полные данные с custom_fields
                }
            )
            
            if isinstance(contacts_data, dict):
                embedded = contacts_data.get("_embedded") or {}
                contacts = embedded.get("contacts") or []
                if isinstance(contacts, list):
                    logger.info(f"fetch_contacts_via_links: получено {len(contacts)} контактов для батча {i//contact_batch_size + 1}")
                    
                    # Добавляем информацию о компании в _embedded.companies для каждого контакта
                    for contact in contacts:
                        if not isinstance(contact, dict):
                            continue
                        
                        contact_id = int(contact.get("id") or 0)
                        if not contact_id or contact_id not in contact_id_to_company_map:
                            continue
                        
                        company_id = contact_id_to_company_map[contact_id]
                        
                        # Убеждаемся, что _embedded.companies заполнен
                        if "_embedded" not in contact:
                            contact["_embedded"] = {}
                        if "companies" not in contact["_embedded"] or not contact["_embedded"]["companies"]:
                            contact["_embedded"]["companies"] = [{"id": company_id}]
                        
                        all_contacts.append(contact)
                else:
                    logger.warning(f"fetch_contacts_via_links: неожиданный тип contacts в ответе: {type(contacts)}")
            else:
                logger.warning(f"fetch_contacts_via_links: неожиданный тип ответа для контактов: {type(contacts_data)}")
                
        except Exception as e:
            logger.error(f"fetch_contacts_via_links: ошибка при получении полных данных контактов (batch {i//contact_batch_size + 1}): {e}", exc_info=True)
            # НЕ делаем fallback - просто пропускаем этот батч
            continue
    
    logger.info(f"fetch_contacts_via_links: contacts fetched by ids={len(all_contacts)} из {len(contact_ids_list)} запрошенных")
    
    logger.info(f"fetch_contacts_via_links: ИТОГО получено {len(all_contacts)} контактов с полными данными для {len(company_ids)} компаний")
    if warnings:
        logger.info(f"fetch_contacts_via_links: предупреждений: {len(warnings)}")
    
    return all_contacts, contact_id_to_company_map, warnings


def fetch_contacts_bulk(client: AmoClient, company_ids: list[int]) -> tuple[list[dict[str, Any]], dict[int, int], list[str]]:
    """
    ОПТИМИЗИРОВАННАЯ версия получения контактов компаний через Entity Links API.
    
    Использует fetch_contacts_via_links для получения контактов через Entity Links API.
    Никаких fallback-веток и глобальных сканов.
    
    Args:
        client: AmoClient для запросов
        company_ids: список ID компаний, для которых нужны контакты
        
    Returns:
        tuple[list[dict], dict[int, int], list[str]]: 
            - список контактов с полными данными (custom_fields, _embedded.companies)
            - словарь contact_id -> company_id для маппинга
            - список предупреждений (warnings)
    """
    # Используем новый метод через Entity Links API
    return fetch_contacts_via_links(client, company_ids)
    # - ≤10 компаний: filter[company_id]=ID для каждой (точный запрос, полные данные сразу)
    # - 11-30 компаний: with=contacts для компаний + filter[id][] для контактов (оптимизировано)
    # - 31-100 компаний: разбиваем на подбатчи по 10 и используем точный запрос (быстрее bulk)
    # - >100 компаний: bulk-запрос с ранним прерыванием (для очень больших батчей)
    if len(company_ids) <= 10:
        logger.info(f"fetch_contacts_bulk: используем точный запрос для {len(company_ids)} компаний (небольшой батч)")
        return fetch_contacts_per_company_precise(client, company_ids)
    elif len(company_ids) <= 30:
        logger.info(f"fetch_contacts_bulk: используем оптимизированный запрос для {len(company_ids)} компаний (средний батч)")
        return fetch_contacts_medium_batch(client, company_ids)
    elif len(company_ids) <= 100:
        # ОПТИМИЗАЦИЯ: для 31-100 компаний разбиваем на подбатчи по 10 и используем точный запрос
        # Это намного быстрее bulk-метода, т.к. не получаем лишние контакты
        logger.info(f"fetch_contacts_bulk: разбиваем {len(company_ids)} компаний на подбатчи по 10 (оптимизированный подход)")
        all_contacts: list[dict[str, Any]] = []
        all_contact_id_to_company_map: dict[int, int] = {}
        
        batch_size = 10
        for i in range(0, len(company_ids), batch_size):
            batch_company_ids = company_ids[i:i + batch_size]
            logger.info(f"fetch_contacts_bulk: обрабатываем подбатч {i//batch_size + 1} ({len(batch_company_ids)} компаний)")
            
            batch_contacts, batch_map = fetch_contacts_per_company_precise(client, batch_company_ids)
            all_contacts.extend(batch_contacts)
            all_contact_id_to_company_map.update(batch_map)
        
        logger.info(f"fetch_contacts_bulk: ИТОГО получено {len(all_contacts)} контактов для {len(company_ids)} компаний (разбито на {len(company_ids)//batch_size + 1} подбатчей)")
        return all_contacts, all_contact_id_to_company_map
    
    company_ids_set = set(company_ids)
    all_contacts: list[dict[str, Any]] = []
    contact_id_to_company_map: dict[int, int] = {}
    
    logger.info(f"fetch_contacts_bulk: начинаем bulk-получение контактов для {len(company_ids)} компаний")
    
    # Получаем все контакты через пагинацию с максимальным limit
    # Используем filter[company_id][] для фильтрации по компаниям (если API поддерживает)
    # Если нет - получаем все и фильтруем локально
    
    # ОПТИМИЗАЦИЯ: отслеживаем, для каких компаний уже нашли контакты, чтобы прервать пагинацию раньше
    found_company_ids_during_pagination: set[int] = set()
    
    # Способ 1: Пробуем получить контакты через filter[company_id][] (массив)
    # AmoCRM API v4 может поддерживать filter[company_id][]=id1&filter[company_id][]=id2
    try:
        # Разбиваем на батчи по 50 компаний (лимит API)
        batch_size = 50
        for i in range(0, len(company_ids), batch_size):
            batch_company_ids = company_ids[i:i + batch_size]
            batch_company_ids_set = set(batch_company_ids)
            found_company_ids_during_pagination.clear()  # Сбрасываем для каждого батча
            
            logger.info(f"fetch_contacts_bulk: запрашиваем контакты для батча компаний {i//batch_size + 1} ({len(batch_company_ids)} компаний)")
            
            # ОПТИМИЗАЦИЯ: функция для раннего прерывания пагинации
            def should_stop_pagination(current_contacts: list[dict]) -> bool:
                """
                Проверяет, нужно ли прервать пагинацию.
                
                Логика:
                1. Проверяем ВСЕ накопленные контакты (не только последние 250)
                2. Отслеживаем, для каких компаний уже нашли контакты
                3. Прерываем, если:
                   - Нашли контакты для ВСЕХ компаний (100%) И получили >= 100 контактов
                   - ИЛИ получили >= 2000 контактов И нашли для 80%+ компаний
                   - ИЛИ получили >= 5000 контактов И < 10% из них релевантны (для наших компаний)
                """
                # ОПТИМИЗАЦИЯ: проверяем ВСЕ накопленные контакты, а не только последние 250
                # Это позволяет учитывать контакты, найденные на предыдущих страницах
                relevant_contacts_count = 0
                for contact in current_contacts:  # ✅ Проверяем все контакты
                    if not isinstance(contact, dict):
                        continue
                    contact_is_relevant = False
                    embedded = contact.get("_embedded") or {}
                    companies_in_contact = embedded.get("companies") or []
                    if isinstance(companies_in_contact, list):
                        for comp_ref in companies_in_contact:
                            comp_id = None
                            if isinstance(comp_ref, dict):
                                comp_id = int(comp_ref.get("id") or 0)
                            elif isinstance(comp_ref, int):
                                comp_id = comp_ref
                            if comp_id and comp_id in batch_company_ids_set:
                                found_company_ids_during_pagination.add(comp_id)
                                contact_is_relevant = True
                    # Fallback: проверяем company_id напрямую
                    if not contact_is_relevant:
                        comp_id_direct = int(contact.get("company_id") or 0)
                        if comp_id_direct and comp_id_direct in batch_company_ids_set:
                            found_company_ids_during_pagination.add(comp_id_direct)
                            contact_is_relevant = True
                    if contact_is_relevant:
                        relevant_contacts_count += 1
                
                # Условие 1: Нашли контакты для ВСЕХ компаний (100%) И получили достаточно контактов
                # ВАЖНО: Это работает только если у ВСЕХ компаний есть контакты (редкий случай)
                if len(current_contacts) >= 100 and len(found_company_ids_during_pagination) == len(batch_company_ids_set):
                    logger.info(f"fetch_contacts_bulk: прерываем пагинацию - найдены контакты для всех {len(batch_company_ids_set)} компаний (получено {len(current_contacts)} контактов)")
                    return True
                
                # Условие 2: Получили много контактов (2000+), но нашли для большинства компаний (80%+)
                # ВАЖНО: Это работает только если у большинства компаний есть контакты
                if len(current_contacts) >= 2000:
                    found_percentage = len(found_company_ids_during_pagination) / len(batch_company_ids_set) if batch_company_ids_set else 0
                    if found_percentage >= 0.8:  # 80% компаний
                        logger.info(f"fetch_contacts_bulk: прерываем пагинацию - получено {len(current_contacts)} контактов, найдено для {len(found_company_ids_during_pagination)}/{len(batch_company_ids_set)} компаний ({found_percentage*100:.1f}%)")
                        return True
                
                # Условие 3: Получили слишком много контактов (5000+), но < 10% из них релевантны
                # ВАЖНО: Это основное условие для случая, когда у большинства компаний НЕТ контактов
                # Если после 5000 контактов релевантность < 10%, значит API возвращает все контакты,
                # а не только для наших компаний. Дальше получать не нужно - все релевантные уже получены.
                # КРИТИЧНО: НЕ прерываем, если не нашли НИ ОДНОГО релевантного контакта - они могут быть дальше!
                if len(current_contacts) >= 5000:
                    relevance_percentage = relevant_contacts_count / len(current_contacts) if current_contacts else 0
                    # Прерываем только если нашли хотя бы ОДИН релевантный контакт И релевантность < 10%
                    # Если relevant_contacts_count == 0, значит контакты могут быть дальше в пагинации
                    if relevant_contacts_count > 0 and relevance_percentage < 0.1:  # Нашли хотя бы 1 И меньше 10% релевантных
                        logger.warning(f"fetch_contacts_bulk: прерываем пагинацию - получено {len(current_contacts)} контактов, найдено {relevant_contacts_count} релевантных ({relevance_percentage*100:.1f}%). API возвращает все контакты, дальше получать не нужно.")
                        return True
                    elif relevant_contacts_count == 0:
                        # Не нашли ни одного релевантного контакта - продолжаем получать
                        # Возможно, контакты находятся дальше в пагинации
                        logger.debug(f"fetch_contacts_bulk: получено {len(current_contacts)} контактов, но релевантных не найдено. Продолжаем получать контакты...")
                
                return False
            
            # Пробуем использовать filter[company_id][] с массивом
            # ОПТИМИЗАЦИЯ: получаем контакты с прерыванием, если уже нашли достаточно
            result = client.get_all_pages(
                "/api/v4/contacts",
                params={
                    "filter[company_id]": batch_company_ids,  # Массив - AmoClient обработает как filter[company_id][]=...
                    "with": "custom_fields",  # Получаем custom_fields сразу
                },
                embedded_key="contacts",
                limit=250,  # Максимальный limit для уменьшения числа запросов
                max_pages=100,
                early_stop_callback=should_stop_pagination,  # ОПТИМИЗАЦИЯ: раннее прерывание
                return_meta=False,  # Не нужны метаданные для контактов
            )
            contacts_batch = result
            
            if contacts_batch:
                logger.info(f"fetch_contacts_bulk: получено {len(contacts_batch)} контактов для батча компаний (найдено контактов для {len(found_company_ids_during_pagination)}/{len(batch_company_ids)} компаний)")
                all_contacts.extend(contacts_batch)
    except Exception as e:
        logger.warning(f"fetch_contacts_bulk: ошибка при bulk-запросе контактов: {e}, пробуем альтернативный способ")
        # Fallback: получаем все контакты и фильтруем локально
        try:
            all_contacts = client.get_all_pages(
                "/api/v4/contacts",
                params={
                    "with": "custom_fields",
                },
                embedded_key="contacts",
                limit=250,
                max_pages=200,  # Увеличиваем для больших объемов
                return_meta=False,
            )
            logger.info(f"fetch_contacts_bulk: получено {len(all_contacts)} контактов (без фильтра), фильтруем локально")
        except Exception as e2:
            logger.error(f"fetch_contacts_bulk: критическая ошибка при получении контактов: {e2}")
            return [], {}
    
    # ОПТИМИЗАЦИЯ: фильтруем контакты по принадлежности к компаниям из списка
    # Используем множества для быстрой проверки
    filtered_contacts: list[dict[str, Any]] = []
    found_company_ids: set[int] = set()  # Отслеживаем, для каких компаний уже нашли контакты
    
    # ОПТИМИЗАЦИЯ: если получили слишком много контактов (25000+), но нашли контакты для всех компаний,
    # можно прервать фильтрацию раньше (но не прерываем, т.к. контакт может быть связан с несколькими компаниями)
    
    for contact in all_contacts:
        if not isinstance(contact, dict):
            continue
        
        contact_id = int(contact.get("id") or 0)
        if not contact_id:
            continue
        
        # Проверяем связь с компаниями через _embedded.companies
        found_company_id = None
        embedded = contact.get("_embedded") or {}
        companies_in_contact = embedded.get("companies") or []
        
        if isinstance(companies_in_contact, list):
            for comp_ref in companies_in_contact:
                comp_id = None
                if isinstance(comp_ref, dict):
                    comp_id = int(comp_ref.get("id") or 0)
                elif isinstance(comp_ref, int):
                    comp_id = comp_ref
                
                if comp_id and comp_id in company_ids_set:
                    found_company_id = comp_id
                    break
        
        # Fallback: проверяем company_id напрямую
        if not found_company_id:
            comp_id_direct = int(contact.get("company_id") or 0)
            if comp_id_direct and comp_id_direct in company_ids_set:
                found_company_id = comp_id_direct
        
        # Добавляем контакт только если он связан с одной из наших компаний
        if found_company_id:
            # Убеждаемся, что _embedded.companies заполнен
            if "_embedded" not in contact:
                contact["_embedded"] = {}
            if "companies" not in contact["_embedded"] or not contact["_embedded"]["companies"]:
                contact["_embedded"]["companies"] = [{"id": found_company_id}]
            
            filtered_contacts.append(contact)
            contact_id_to_company_map[contact_id] = found_company_id
            found_company_ids.add(found_company_id)
    
    logger.info(f"_fetch_contacts_bulk_old_unused: отфильтровано {len(filtered_contacts)} контактов из {len(all_contacts)} полученных для {len(company_ids)} компаний")
    return filtered_contacts, contact_id_to_company_map


def fetch_notes_for_contacts(client: AmoClient, contact_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    """
    Получает заметки контактов из amoCRM (старая версия - по одному контакту).
    Оставлена для обратной совместимости.
    Используйте fetch_notes_for_contacts_bulk для оптимизированного получения.
    """
    return fetch_notes_for_contacts_bulk(client, contact_ids)


def fetch_notes_for_contacts_bulk(client: AmoClient, contact_ids: list[int], *, batch_size: int = 200) -> dict[int, list[dict[str, Any]]]:
    """
    ОПТИМИЗИРОВАННАЯ версия получения заметок контактов через bulk-запросы.
    
    Получает заметки через /api/v4/notes с фильтром по entity_type=contacts и entity_id[].
    Использует батчинг для уменьшения числа запросов.
    
    Args:
        client: AmoClient для запросов
        contact_ids: список ID контактов
        batch_size: размер батча для фильтрации (по умолчанию 200)
        
    Returns:
        dict[int, list[dict]]: словарь contact_id -> список заметок
    """
    if not contact_ids:
        return {}
    
    out: dict[int, list[dict[str, Any]]] = {}
    
    logger.info(f"fetch_notes_for_contacts_bulk: начинаем bulk-получение заметок для {len(contact_ids)} контактов")
    
    # Разбиваем на батчи для избежания слишком длинных URL
    for i in range(0, len(contact_ids), batch_size):
        batch_ids = contact_ids[i:i + batch_size]
        batch_num = i // batch_size + 1
        total_batches = (len(contact_ids) + batch_size - 1) // batch_size
        
        logger.info(f"fetch_notes_for_contacts_bulk: обрабатываем батч {batch_num}/{total_batches} ({len(batch_ids)} контактов)")
        
        try:
            # Получаем заметки через /api/v4/notes с фильтром по entity_type и entity_id[]
            result = client.get_all_pages(
                "/api/v4/notes",
                params={
                    "filter[entity_type]": "contacts",
                    "filter[entity_id]": batch_ids,  # Массив - AmoClient обработает как filter[entity_id][]=...
                },
                embedded_key="notes",
                limit=250,  # Максимальный limit
                max_pages=100,
            )
            
            # Группируем заметки по contact_id (entity_id в заметке)
            for note in notes:
                if not isinstance(note, dict):
                    continue
                
                entity_id = int(note.get("entity_id") or 0)
                if entity_id and entity_id in batch_ids:
                    if entity_id not in out:
                        out[entity_id] = []
                    out[entity_id].append(note)
            
            logger.info(f"fetch_notes_for_contacts_bulk: получено {len(notes)} заметок для батча {batch_num}")
            
        except RateLimitError as e:
            # Rate limit после всех retry - поднимаем исключение, не пропускаем тихо
            logger.error(
                f"fetch_notes_for_contacts_bulk: Rate limit исчерпан для батча {batch_num}/{total_batches}. "
                f"Получено заметок для {len(out)} контактов из {len(contact_ids)}. "
                f"Импорт заметок контактов прерван."
            )
            raise
        except Exception as e:
            # ОПТИМИЗАЦИЯ: 404 ошибка для заметок - это нормально (API может не поддерживать этот endpoint)
            # Не логируем как критическую ошибку, просто пропускаем
            error_str = str(e)
            if "404" in error_str or "Not Found" in error_str:
                logger.debug(f"fetch_notes_for_contacts_bulk: заметки недоступны для батча {batch_num} (404 - это нормально для некоторых аккаунтов AmoCRM)")
            else:
                logger.warning(f"fetch_notes_for_contacts_bulk: ошибка при получении заметок для батча {batch_num}: {e}")
            # Продолжаем для следующих батчей
            continue
    
    logger.info(f"fetch_notes_for_contacts_bulk: ИТОГО получено заметок для {len(out)} контактов из {len(contact_ids)} запрошенных")
    return out


def _upsert_company_from_amo(
    *,
    amo_company: dict[str, Any],
    actor: User,
    responsible: User | None,
    dry_run: bool,
) -> tuple[Company, bool]:
    amo_id = int(amo_company.get("id") or 0)
    name = str(amo_company.get("name") or "").strip()[:255] or "(без названия)"  # обрезаем name сразу
    company = Company.objects.filter(amocrm_company_id=amo_id).first()
    created = False
    if company is None:
        company = Company(name=name, created_by=actor, responsible=responsible, amocrm_company_id=amo_id, raw_fields={"source": "amo_api"})
        created = True
    else:
        if name and company.name != name:
            company.name = name[:255]  # обрезаем name при обновлении
    # сохраняем raw_fields (не ломаем существующие)
    try:
        rf = dict(company.raw_fields or {})
    except Exception:
        rf = {}
    rf["amo_api_last"] = amo_company
    # Сохраняем все custom_fields_values в raw_fields["amo"]
    if "amo" not in rf:
        rf["amo"] = {}
    rf["amo"]["custom_fields_values"] = amo_company.get("custom_fields_values") or []
    company.raw_fields = _json_sanitize(rf)
    if responsible and company.responsible_id != responsible.id:
        company.responsible = responsible
    
    # Извлекаем данные о холодном звонке из custom_fields компании
    cold_call_timestamp = None
    custom_fields = amo_company.get("custom_fields_values") or []
    for cf in custom_fields:
        if not isinstance(cf, dict):
            continue
        field_name = str(cf.get("field_name") or "").lower()
        field_type = str(cf.get("field_type") or "").lower()
        # Проверяем поле "Холодный звонок" с типом "date"
        if field_type == "date" and ("холодный" in field_name and "звонок" in field_name):
            values = cf.get("values") or []
            if values and isinstance(values, list):
                for v in values:
                    if isinstance(v, dict):
                        val = v.get("value")
                    else:
                        val = v
                    if val:
                        try:
                            cold_call_timestamp = int(float(val))
                            break  # Берем первое значение
                        except (ValueError, TypeError):
                            pass
    
    # Устанавливаем данные о холодном звонке для компании
    if cold_call_timestamp:
        try:
            UTC = getattr(timezone, "UTC", dt_timezone.utc)
            cold_marked_at_dt = timezone.datetime.fromtimestamp(cold_call_timestamp, tz=UTC)
            company.primary_contact_is_cold_call = True
            company.primary_cold_marked_at = cold_marked_at_dt
            company.primary_cold_marked_by = responsible or company.created_by or actor
            # primary_cold_marked_call оставляем NULL, т.к. в amoCRM нет связи с CallRequest
        except Exception:
            pass  # Если не удалось распарсить timestamp - пропускаем
    
    if not dry_run:
        try:
            company.save()
        except Exception as e:
            # Если ошибка при сохранении - логируем, но не падаем (company уже создан в памяти)
            logger.error(f"Failed to save company in _upsert_company_from_amo (amo_id={amo_id}): {e}", exc_info=True)
            # Продолжаем - company уже создан в памяти, просто не сохранен в БД
    return company, created


def _apply_spheres_from_custom(
    *,
    amo_company: dict[str, Any],
    company: Company,
    field_id: int,
    dry_run: bool,
    exclude_label: str | None = None,
) -> None:
    """
    Применяет сферы из кастомного поля amoCRM к компании.
    exclude_label: если указано, исключает эту сферу из импорта (например "Новая CRM").
    """
    values = _extract_custom_values(amo_company, field_id)
    labels = []
    exclude_norm = _norm(exclude_label) if exclude_label else ""
    for v in values:
        lab = str(v.get("value") or "").strip()
        if lab and _norm(lab) != exclude_norm:
            labels.append(lab)
    if not labels or dry_run:
        return
    objs = []
    for lab in labels:
        obj, _ = CompanySphere.objects.get_or_create(name=lab)
        objs.append(obj)
    if objs:
        company.spheres.set(objs)


def migrate_filtered(
    *,
    client: AmoClient,
    actor: User,
    responsible_user_id: int,
    sphere_field_id: int,
    sphere_option_id: int | None,
    sphere_label: str | None,
    limit_companies: int = 0,  # размер пачки
    offset: int = 0,
    dry_run: bool = True,
    import_tasks: bool = True,
    import_notes: bool = True,
    import_contacts: bool = False,  # по умолчанию выключено, т.к. может быть медленно
    company_fields_meta: list[dict[str, Any]] | None = None,
    skip_field_filter: bool = False,  # если True, мигрируем все компании ответственного без фильтра по полю
) -> AmoMigrateResult:
    import time
    start_time = time.time()
    
    # Сбрасываем метрики клиента для нового этапа импорта
    client.reset_metrics()
    
    res = AmoMigrateResult(
        preview=[],
        tasks_preview=[],
        notes_preview=[],
        contacts_preview=[],
        companies_updates_preview=[] if dry_run else None,
        contacts_updates_preview=[] if dry_run else None,
    )

    amo_users = fetch_amo_users(client)
    amo_user_by_id = {int(u.get("id") or 0): u for u in amo_users if int(u.get("id") or 0)}
    # Если список пользователей пуст (например, из-за 403), используем пустой словарь
    responsible_local = _map_amo_user_to_local(amo_user_by_id.get(int(responsible_user_id)) or {}) if amo_user_by_id else None
    field_meta = _build_field_meta(company_fields_meta or [])

    # КРИТИЧЕСКИ: ВСЕГДА запрашиваем компании БЕЗ контактов
    # Контакты получаем отдельно через filter[company_id][] - это надежнее и легче
    # Запрос компаний с with=contacts создает огромные ответы и вызывает 504
    # Получаем компании с метаданными пагинации
    companies, pagination_meta = fetch_companies_by_responsible(client, responsible_user_id, with_contacts=False, return_meta=True)
    res.companies_seen = len(companies)
    
    # Сохраняем метаданные пагинации компаний
    res.companies_fetch_truncated = pagination_meta.get("truncated", False)
    res.companies_pages_fetched = pagination_meta.get("pages_fetched", 0)
    res.companies_elements_fetched = pagination_meta.get("elements_fetched", 0)
    matched_all = []
    if skip_field_filter:
        # Мигрируем все компании ответственного без фильтра по полю
        matched_all = companies
    else:
        # Фильтруем по кастомному полю (как раньше)
        for c in companies:
            if _custom_has_value(c, sphere_field_id, option_id=sphere_option_id, label=sphere_label):
                matched_all.append(c)
    res.companies_matched = len(matched_all)

    off = max(int(offset or 0), 0)
    batch_size = int(limit_companies or 0)
    if batch_size <= 0:
        batch_size = 50
    # Защита от offset за пределами списка
    if off >= len(matched_all):
        batch = []
        res.companies_offset = off
        res.companies_batch = 0
        res.companies_next_offset = off
        res.companies_has_more = False
    else:
        batch = matched_all[off : off + batch_size]
        res.companies_offset = off
        res.companies_batch = len(batch)
        res.companies_next_offset = off + len(batch)
        res.companies_has_more = res.companies_next_offset < len(matched_all)

    # ОПТИМИЗАЦИЯ: разбиваем на под-транзакции по 10-20 компаний для уменьшения блокировок БД
    # Это позволяет быстрее коммитить изменения и уменьшает риск таймаутов
    SUB_TRANSACTION_SIZE = 15  # Размер под-транзакции (компаний)
    
    def _run():
        # Защита от пустого batch (когда offset за пределами списка)
        if not batch:
            return res
        
        local_companies: list[Company] = []
        
        # ОПТИМИЗАЦИЯ: обрабатываем компании под-транзакциями
        for sub_batch_start in range(0, len(batch), SUB_TRANSACTION_SIZE):
            sub_batch = batch[sub_batch_start:sub_batch_start + SUB_TRANSACTION_SIZE]
            
            # Каждая под-транзакция обрабатывает до SUB_TRANSACTION_SIZE компаний
            with transaction.atomic():
                for amo_c in sub_batch:
                    extra = _extract_company_fields(amo_c, field_meta) if field_meta else {}
                    n_skynet = int(extra.get("skynet_phone_values_rejected") or 0)
                    res.skynet_phone_values_rejected += n_skynet
                    if n_skynet > 0:
                        ex = (extra.get("skynet_rejected_example") or "")[:80]
                        logger.warning(
                            "Skynet phone field: rejected %s non-phone value(s), company_id=%s name=%s%s",
                            n_skynet, amo_c.get("id"), (amo_c.get("name") or "")[:80],
                            f", example={ex!r}" if ex else "",
                        )
                    comp, created = _upsert_company_from_amo(amo_company=amo_c, actor=actor, responsible=responsible_local, dry_run=dry_run)
                    # заполнение "Данные" (только если поле пустое, чтобы не затереть уже заполненное вручную)
                    # ВАЖНО: всегда обрезаем значения до max_length, даже если поле уже заполнено (защита от длинных значений)
                    changed = False
                    
                    # Для dry-run: собираем diff изменений
                    company_updates_diff = {} if dry_run else None
                    
                    # Мягкий режим update: если поле уже меняли руками, не перезаписываем.
                    try:
                        rf = dict(comp.raw_fields or {})
                    except Exception:
                        rf = {}
                    prev = rf.get("amo_values") or {}
                    if not isinstance(prev, dict):
                        prev = {}
                    
                    # Сохраняем старые значения для diff (только при dry_run)
                    if dry_run:
                        old_values = {
                            "legal_name": comp.legal_name or "",
                            "inn": comp.inn or "",
                            "kpp": comp.kpp or "",
                            "address": comp.address or "",
                            "phone": comp.phone or "",
                            "email": comp.email or "",
                            "website": comp.website or "",
                            "contact_name": comp.contact_name or "",
                            "activity_kind": comp.activity_kind or "",
                            "employees_count": comp.employees_count,
                            "workday_start": str(comp.workday_start) if comp.workday_start else "",
                            "workday_end": str(comp.workday_end) if comp.workday_end else "",
                            "work_timezone": comp.work_timezone or "",
                        }

                    def can_update(field: str) -> bool:
                        cur = getattr(comp, field)
                        if cur in ("", None):
                            return True
                        if field not in prev:
                            return False
                        p = prev.get(field)
                        # workday_start/end в prev хранятся как str (JSON не поддерживает datetime.time)
                        if field in ("workday_start", "workday_end"):
                            return (str(cur) if cur else "") == (str(p) if p is not None else "")
                        if p == cur:
                            return True
                        return False
                    if extra.get("legal_name"):
                        new_legal = str(extra["legal_name"]).strip()[:255]  # сначала strip, потом обрезка до max_length=255
                        old_legal = (comp.legal_name or "").strip()
                        if not old_legal:
                            comp.legal_name = new_legal
                            changed = True
                            if dry_run and new_legal:
                                company_updates_diff["legal_name"] = {"old": "", "new": new_legal}
                        elif len(comp.legal_name) > 255:  # защита: если уже заполнено, но слишком длинное
                            comp.legal_name = comp.legal_name.strip()[:255]
                            changed = True
                            if dry_run:
                                company_updates_diff["legal_name"] = {"old": old_legal, "new": comp.legal_name}
                    if extra.get("inn"):
                        from companies.inn_utils import merge_inn_strings

                        old_inn = (comp.inn or "").strip()
                        incoming = str(extra["inn"])

                        # Импорт из amoCRM: не затираем вручную внесённые ИНН,
                        # но если в amo пришли новые — аккуратно добавляем (уникально).
                        merged = merge_inn_strings(old_inn, incoming)[:255]
                        if merged and merged != old_inn:
                            comp.inn = merged
                            changed = True
                            if dry_run:
                                company_updates_diff["inn"] = {"old": old_inn, "new": merged}
                    if extra.get("kpp"):
                        new_kpp = str(extra["kpp"]).strip()[:20]  # сначала strip, потом обрезка до max_length=20
                        old_kpp = (comp.kpp or "").strip()
                        if not old_kpp:
                            comp.kpp = new_kpp
                            changed = True
                            if dry_run and new_kpp:
                                company_updates_diff["kpp"] = {"old": "", "new": new_kpp}
                        elif len(comp.kpp) > 20:  # защита: если уже заполнено, но слишком длинное
                            comp.kpp = comp.kpp.strip()[:20]
                            changed = True
                            if dry_run:
                                company_updates_diff["kpp"] = {"old": old_kpp, "new": comp.kpp}
                    if extra.get("address"):
                        new_addr = str(extra["address"]).strip()[:500]  # сначала strip, потом обрезка до max_length=500
                        old_addr = (comp.address or "").strip()
                        if not old_addr:
                            comp.address = new_addr
                            changed = True
                            if dry_run and new_addr:
                                company_updates_diff["address"] = {"old": "", "new": new_addr}
                        elif len(comp.address) > 500:  # защита: если уже заполнено, но слишком длинное
                            comp.address = comp.address.strip()[:500]
                            changed = True
                            if dry_run:
                                company_updates_diff["address"] = {"old": old_addr, "new": comp.address}
                    phones = extra.get("phones") or []
                    emails = extra.get("emails") or []
                    company_note = str(extra.get("note") or "").strip()[:255]
                    
                    # ВАЖНО: сохраняем исходное значение основного телефона ДО обработки
                    # чтобы правильно определить, какие телефоны идут в CompanyPhone
                    original_main_phone = (comp.phone or "").strip()
                    first_phone_comment = None  # комментарий/доб. из разбора первого телефона для comp.phone_comment
                    
                    # основной телефон/почта — в "Данные". comp.phone — только E.164; доб./комментарий — в phone_comment.
                    if phones and not original_main_phone:
                        raw_first = str(phones[0]).strip()
                        parsed = parse_phone_value(raw_first)
                        if parsed.phones and is_valid_phone(parsed.phones[0]):
                            new_phone = parsed.phones[0][:50]
                            parts = []
                            if parsed.extension:
                                parts.append(f"доб. {parsed.extension}")
                            if parsed.comment:
                                parts.append(parsed.comment)
                            first_phone_comment = "; ".join(parts)[:255] if parts else None
                        else:
                            norm = normalize_phone(raw_first)
                            if norm.isValid and norm.phone_e164 and is_valid_phone(norm.phone_e164):
                                new_phone = norm.phone_e164[:50]
                                parts = []
                                if norm.ext:
                                    parts.append(f"доб. {norm.ext}")
                                if norm.note:
                                    parts.append(norm.note)
                                first_phone_comment = "; ".join(parts)[:255] if parts else None
                                comp.phone = new_phone
                                changed = True
                                if dry_run:
                                    company_updates_diff["phone"] = {"old": "", "new": new_phone}
                            else:
                                # Не записываем в comp.phone (требование: только E.164); текст — в comment
                                first_phone_comment = (parsed.comment or ("Комментарий к телефону: " + raw_first))[:255]
                        if parsed.phones and is_valid_phone(parsed.phones[0]):
                            comp.phone = new_phone
                            changed = True
                            if dry_run:
                                company_updates_diff["phone"] = {"old": "", "new": new_phone}
                    if emails and not (comp.email or "").strip():
                        new_email = str(emails[0])[:254]
                        comp.email = new_email
                        changed = True
                        if dry_run:
                            company_updates_diff["email"] = {"old": "", "new": new_email}
                    if extra.get("website") and not (comp.website or "").strip():
                        new_website = extra["website"][:255]
                        comp.website = new_website
                        changed = True
                        if dry_run:
                            company_updates_diff["website"] = {"old": "", "new": new_website}
                    # Комментарий к основному телефону: первый телефон (доб./комментарий) + общее примечание компании.
                    # Не затираем вручную внесённый phone_comment.
                    if not (comp.phone_comment or "").strip():
                        if (comp.phone or "").strip() or (phones and str(phones[0]).strip()):
                            parts = [p for p in [first_phone_comment, company_note] if p]
                            if parts:
                                comp.phone_comment = "; ".join(parts)[:255]
                                changed = True
                                if dry_run:
                                    company_updates_diff["phone_comment"] = {"old": "", "new": comp.phone_comment}
                    if extra.get("activity_kind") and can_update("activity_kind"):
                        ak = str(extra.get("activity_kind") or "").strip()[:255]
                        old_ak = (comp.activity_kind or "").strip()
                        if ak and comp.activity_kind != ak:
                            comp.activity_kind = ak
                            changed = True
                            if dry_run:
                                company_updates_diff["activity_kind"] = {"old": old_ak, "new": ak}
                    if extra.get("employees_count") and can_update("employees_count"):
                        try:
                            ec = int("".join(ch for ch in str(extra.get("employees_count") or "") if ch.isdigit()) or "0")
                            # PositiveIntegerField в PostgreSQL имеет максимум 2147483647
                            # Ограничиваем значение, чтобы избежать ошибки "integer out of range"
                            MAX_EMPLOYEES_COUNT = 2147483647
                            if ec > MAX_EMPLOYEES_COUNT:
                                logger.warning(f"Company {comp.name}: employees_count {ec} exceeds maximum {MAX_EMPLOYEES_COUNT}, capping to maximum")
                                ec = MAX_EMPLOYEES_COUNT
                            old_ec = comp.employees_count
                            if ec > 0 and comp.employees_count != ec:
                                comp.employees_count = ec
                                changed = True
                                if dry_run:
                                    company_updates_diff["employees_count"] = {"old": str(old_ec) if old_ec else "", "new": str(ec)}
                        except (ValueError, OverflowError) as e:
                            logger.warning(f"Company {comp.name}: failed to parse employees_count '{extra.get('employees_count')}': {e}")
                            pass
                    if extra.get("work_timezone") and can_update("work_timezone"):
                        tzv = str(extra.get("work_timezone") or "").strip()[:64]
                        old_tz = (comp.work_timezone or "").strip()
                        if tzv and comp.work_timezone != tzv:
                            comp.work_timezone = tzv
                            changed = True
                            if dry_run:
                                company_updates_diff["work_timezone"] = {"old": old_tz, "new": tzv}
                    if extra.get("worktime"):
                        # поддерживаем форматы: "09:00-18:00", "09:00–18:00", "с 9:00 до 18:00"
                        import re
                        s = str(extra.get("worktime") or "").replace("–", "-").strip()
                        m = re.search(r"(\d{1,2})[:.](\d{2})\s*-\s*(\d{1,2})[:.](\d{2})", s)
                        if m:
                            try:
                                h1, m1, h2, m2 = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
                                if 0 <= h1 <= 23 and 0 <= h2 <= 23 and 0 <= m1 <= 59 and 0 <= m2 <= 59:
                                    old_start = str(comp.workday_start) if comp.workday_start else ""
                                    old_end = str(comp.workday_end) if comp.workday_end else ""
                                    if can_update("workday_start") and comp.workday_start != dt_time(h1, m1):
                                        comp.workday_start = dt_time(h1, m1)
                                        changed = True
                                        if dry_run:
                                            company_updates_diff["workday_start"] = {"old": old_start, "new": str(dt_time(h1, m1))}
                                    if can_update("workday_end") and comp.workday_end != dt_time(h2, m2):
                                        comp.workday_end = dt_time(h2, m2)
                                        changed = True
                                        if dry_run:
                                            company_updates_diff["workday_end"] = {"old": old_end, "new": str(dt_time(h2, m2))}
                            except Exception:
                                pass
                    # Руководитель (contact_name) — заполняем из amo, если пусто
                    if extra.get("director") and not (comp.contact_name or "").strip():
                        new_director = extra["director"][:255]
                        comp.contact_name = new_director
                        changed = True
                        if dry_run:
                            company_updates_diff["contact_name"] = {"old": "", "new": new_director}

                    if changed:
                        prev.update(
                            {
                                "legal_name": comp.legal_name,
                                "inn": comp.inn,
                                "kpp": comp.kpp,
                                "address": comp.address,
                                "phone": comp.phone,
                                "email": comp.email,
                                "website": comp.website,
                                "director": comp.contact_name,
                                "activity_kind": comp.activity_kind,
                                "employees_count": comp.employees_count,
                                # time → str, иначе json.dumps в JSONField падает: Object of type time is not JSON serializable
                                "workday_start": str(comp.workday_start) if comp.workday_start else None,
                                "workday_end": str(comp.workday_end) if comp.workday_end else None,
                                "work_timezone": comp.work_timezone,
                            }
                        )
                        rf["amo_values"] = prev
                        comp.raw_fields = _json_sanitize(rf)
                    
                    # Сохраняем diff изменений для dry-run
                    if dry_run and company_updates_diff:
                        if res.companies_updates_preview is None:
                            res.companies_updates_preview = []
                        res.companies_updates_preview.append({
                            "company_name": comp.name,
                            "company_id": comp.id if comp.id else None,
                            "amo_id": comp.amocrm_company_id,
                            "is_new": created,
                            "updates": company_updates_diff,
                        })
                    
                    if changed and not dry_run:
                        try:
                            comp.save()
                        except Exception as e:
                            # Если ошибка при сохранении - логируем и пропускаем эту компанию
                            logger.error(f"Failed to save company {comp.name} (amo_id={comp.amocrm_company_id}): {e}", exc_info=True)
                            # Пропускаем эту компанию, продолжаем со следующей
                            continue

                    # Нормализация уже заполненных значений (часто там "номер1, номер2"):
                    # оставляем в "Данные" только первый, остальные переносим в служебный контакт.
                    norm_phone_parts = _split_multi(comp.phone or "")
                    norm_email_parts = _split_multi(comp.email or "")
                    if len(norm_phone_parts) > 1 and not dry_run:
                        try:
                            comp.phone = norm_phone_parts[0][:50]
                            comp.save(update_fields=["phone"])
                            # добавим остальные как контактные телефоны
                            phones = list(dict.fromkeys([*phones, *norm_phone_parts]))
                        except Exception as e:
                            logger.error(f"Failed to save phone for company {comp.name}: {e}", exc_info=True)
                    if len(norm_email_parts) > 1 and not dry_run:
                        try:
                            comp.email = norm_email_parts[0][:254]
                            comp.save(update_fields=["email"])
                            emails = list(dict.fromkeys([*emails, *norm_email_parts]))
                        except Exception as e:
                            logger.error(f"Failed to save email for company {comp.name}: {e}", exc_info=True)

                    # Дополнительные телефоны сохраняем в CompanyPhone (а не в ContactPhone)
                    # ВАЖНО: используем original_main_phone (до обработки), чтобы правильно определить логику
                    # Если основной телефон уже был заполнен ДО импорта, все телефоны из phones идут в CompanyPhone
                    # Если основной телефон был пустой и мы его заполнили из phones[0], то остальные (phones[1:]) идут в CompanyPhone
                    if original_main_phone:
                        # Основной телефон уже был заполнен ДО импорта - все телефоны из phones идут в CompanyPhone
                        extra_phones = [p for p in phones if str(p).strip()]
                    else:
                        # Основной телефон был пустой - первый телефон уже в comp.phone, остальные в CompanyPhone
                        extra_phones = [p for p in phones[1:] if str(p).strip()]
                    
                    # ОПТИМИЗАЦИЯ: bulk операции для CompanyPhone (основные телефоны + Скайнет 309609)
                    skynet_phones = extra.get("skynet_phones") or []
                    if (extra_phones or skynet_phones) and not dry_run:
                        from ui.forms import _normalize_phone
                        from django.db.models import Max
                        
                        # Получаем максимальный order для существующих телефонов
                        max_order = CompanyPhone.objects.filter(company=comp).aggregate(m=Max("order")).get("m")
                        next_order = int(max_order) + 1 if max_order is not None else 0
                        
                        # ОПТИМИЗАЦИЯ: загружаем все существующие телефоны одним запросом
                        existing_phones_raw = list(CompanyPhone.objects.filter(company=comp).values_list('value', flat=True))
                        existing_phones_normalized = set()
                        for existing in existing_phones_raw:
                            existing_norm = _normalize_phone(existing) if existing else ""
                            if existing_norm:
                                existing_phones_normalized.add(existing_norm)
                        
                        # Нормализуем основной телефон один раз
                        main_phone_normalized = _normalize_phone(comp.phone) if (comp.phone or "").strip() else ""
                        
                        # Собираем телефоны для bulk_create. value — только E.164; доб./комментарий — в comment.
                        phones_to_create = []
                        for p in extra_phones:
                            v = str(p).strip()[:50]
                            if not v:
                                continue
                            parsed = parse_phone_value(p)
                            if parsed.phones and is_valid_phone(parsed.phones[0]):
                                normalized = parsed.phones[0][:50]
                                c_parts = []
                                if parsed.extension:
                                    c_parts.append(f"доб. {parsed.extension}")
                                if parsed.comment:
                                    c_parts.append(parsed.comment)
                                ph_comment = "; ".join(c_parts)[:255] if c_parts else ""
                            else:
                                normalized = _normalize_phone(v) if v else ""
                                if not normalized:
                                    normalized = v
                                ph_comment = ""
                            if not is_valid_phone(normalized):
                                res.company_phones_rejected_invalid += 1
                                if res.company_phones_rejected_invalid <= 3:
                                    logger.debug(
                                        "company_phones_rejected_invalid: пример value (не E.164), company=%s: %s",
                                        comp.name, (v[:60] + "…" if len(v) > 60 else v),
                                    )
                                continue
                            # Проверяем, что такого телефона еще нет (ни в основном, ни в дополнительных)
                            if main_phone_normalized and main_phone_normalized == normalized:
                                logger.debug(f"Company {comp.name}: skipping phone {v} (normalized: {normalized}) - same as main phone")
                                continue
                            if normalized in existing_phones_normalized:
                                logger.debug(f"Company {comp.name}: skipping phone {v} (normalized: {normalized}) - duplicate")
                                continue
                            phones_to_create.append(CompanyPhone(company=comp, value=normalized, order=next_order, comment=ph_comment))
                            existing_phones_normalized.add(normalized)  # Предотвращаем дубликаты в этом батче
                            next_order += 1
                        
                        # Скайнет (поле 309609): только в CompanyPhone с comment=SKYNET, без дубликатов
                        skynet_added = 0
                        skynet_skipped_dup = 0
                        for p in skynet_phones:
                            if not is_valid_phone(p):  # защита: parse_skynet_phones уже отфильтровал
                                res.company_phones_rejected_invalid += 1
                                continue
                            norm = _normalize_phone(p) if p else ""
                            if not norm:
                                norm = p
                            if main_phone_normalized and main_phone_normalized == norm:
                                skynet_skipped_dup += 1
                                continue
                            if norm in existing_phones_normalized:
                                skynet_skipped_dup += 1
                                continue
                            phones_to_create.append(CompanyPhone(company=comp, value=norm, order=next_order, comment="SKYNET"))
                            existing_phones_normalized.add(norm)
                            next_order += 1
                            skynet_added += 1
                        if skynet_added:
                            res.skynet_phones_added += skynet_added
                        if skynet_added or skynet_skipped_dup:
                            logger.info(
                                f"Company {comp.name} (amo_id={comp.amocrm_company_id}, inn={comp.inn or '-'}): "
                                f"skynet_phones: added={skynet_added}, skipped_duplicate={skynet_skipped_dup}"
                            )
                        
                        # Bulk создание телефонов
                        if phones_to_create:
                            try:
                                CompanyPhone.objects.bulk_create(phones_to_create, ignore_conflicts=True)
                                suf = f" ({skynet_added} from Skynet)" if skynet_added else ""
                                logger.info(f"Company {comp.name}: bulk created {len(phones_to_create)} CompanyPhone records{suf}")
                            except Exception as e:
                                logger.error(f"Failed to bulk_create CompanyPhone for company {comp.name}: {e}", exc_info=True)
                    
                    # Дополнительные email сохраняем в CompanyEmail
                    extra_emails = [e for e in emails[1:] if str(e).strip()]
                    # ОПТИМИЗАЦИЯ: bulk операции для CompanyEmail
                    if extra_emails and not dry_run:
                        # ОПТИМИЗАЦИЯ: загружаем все существующие email одним запросом
                        existing_emails = set(
                            CompanyEmail.objects.filter(company=comp)
                            .values_list('value', flat=True)
                        )
                        main_email_lower = (comp.email or "").strip().lower()
                        
                        # Собираем email для bulk_create
                        emails_to_create = []
                        for e in extra_emails:
                            v = str(e).strip()[:254]
                            if not v:
                                continue
                            v_lower = v.lower()
                            # Проверяем, что такого email еще нет (ни в основном, ни в дополнительных)
                            if main_email_lower and main_email_lower == v_lower:
                                continue  # Пропускаем, если это основной email
                            if v_lower in existing_emails:
                                continue  # Пропускаем дубликаты
                            
                            # Добавляем в список для bulk_create
                            emails_to_create.append(CompanyEmail(company=comp, value=v))
                            existing_emails.add(v_lower)  # Предотвращаем дубликаты в этом батче
                        
                        # Bulk создание email
                        if emails_to_create:
                            try:
                                CompanyEmail.objects.bulk_create(emails_to_create, ignore_conflicts=True)
                                logger.info(f"Company {comp.name}: bulk created {len(emails_to_create)} CompanyEmail records")
                            except Exception as e:
                                logger.error(f"Failed to bulk_create CompanyEmail for company {comp.name}: {e}", exc_info=True)
                    
                    # Остальные телефоны/почты, которые не удалось сохранить в CompanyPhone/CompanyEmail,
                    # сохраняем в "Контакты" отдельной записью (stub) - это fallback для совместимости
                    # (оставляем эту логику для обратной совместимости, но приоритет - CompanyPhone/CompanyEmail)
                    if created:
                        res.companies_created += 1
                    else:
                        res.companies_updated += 1
                    # Сферы: исключаем "Новая CRM" (она только для фильтра), но ставим остальные
                    _apply_spheres_from_custom(amo_company=amo_c, company=comp, field_id=sphere_field_id, dry_run=dry_run, exclude_label="Новая CRM")
                    local_companies.append(comp)
                    if res.preview is not None and len(res.preview) < 15:
                        res.preview.append({"company": comp.name, "amo_id": comp.amocrm_company_id})
            
            # Логируем прогресс под-транзакций
            if not dry_run:
                logger.info(f"migrate_filtered: обработано {len(sub_batch)} компаний в под-транзакции ({sub_batch_start + 1}/{len(batch)})")

        amo_ids = [int(c.get("id") or 0) for c in batch if int(c.get("id") or 0)]
        # ОПТИМИЗАЦИЯ (без изменения результата):
        # - заранее подгружаем компании из БД для текущей пачки, чтобы убрать N+1 в задачах/заметках
        # Важно: в dry-run новые компании НЕ сохранены, поэтому мапа из БД может быть неполной — это
        # сохраняет старое поведение (задачи/заметки для новых компаний в dry-run не привяжутся).
        companies_db_by_amo_id: dict[int, Company] = {}
        if amo_ids:
            for cobj in Company.objects.filter(amocrm_company_id__in=amo_ids):
                if cobj.amocrm_company_id:
                    companies_db_by_amo_id[int(cobj.amocrm_company_id)] = cobj

        # Задачи: запрашиваем только если нужно импортировать
        # ВАЖНО: в dry-run запрашиваем задачи для диагностики, даже если import_tasks=False
        if import_tasks and amo_ids and not (dry_run and not import_tasks):
            logger.info(f"migrate_filtered: запрашиваем задачи для {len(amo_ids)} компаний (import_tasks={import_tasks}, dry_run={dry_run})")
            tasks = fetch_tasks_for_companies(client, amo_ids)
            res.tasks_seen = len(tasks)
            logger.info(f"migrate_filtered: получено задач из API: {res.tasks_seen} для {len(amo_ids)} компаний")
            # ОПТИМИЗАЦИЯ: убираем N+1 запросы к Task (проверка существования/обновление)
            task_uids = {str(int(t.get("id") or 0)) for t in tasks if int(t.get("id") or 0)}
            existing_tasks_by_uid: dict[str, Task] = {}
            if task_uids:
                for tsk in Task.objects.filter(external_source="amo_api", external_uid__in=task_uids):
                    if tsk.external_uid:
                        existing_tasks_by_uid[str(tsk.external_uid)] = tsk
            tasks_processed = 0
            tasks_skipped_no_company = 0
            tasks_skipped_old_count = 0
            logger.info(f"migrate_filtered: обрабатываем {len(tasks)} задач (до фильтрации по дедлайну/компании)")
            for t in tasks:
                tid = int(t.get("id") or 0)
                existing = existing_tasks_by_uid.get(str(tid)) if tid else None
                entity_id = int((t.get("entity_id") or 0) or 0)
                company = companies_db_by_amo_id.get(entity_id) if entity_id else None
                if not company:
                    tasks_skipped_no_company += 1
                    continue
                
                title = str(t.get("text") or t.get("result") or t.get("name") or "Задача (amo)").strip()[:255]
                due_at = None
                # важно: не используем "or", потому что 0/"" могут скрыть реальные значения
                ts = t.get("complete_till", None)
                if ts in (None, "", 0, "0"):
                    ts = t.get("complete_till_at", None)
                if ts in (None, "", 0, "0"):
                    ts = t.get("due_at", None)
                due_at = _parse_amo_due(ts)
                
                # Фильтрация: импортируем только задачи с дедлайном на 2026 год и позже
                if due_at and due_at.year < 2026:
                    res.tasks_skipped_old += 1
                    tasks_skipped_old_count += 1
                    continue
                
                if res.tasks_preview is not None and len(res.tasks_preview) < 5:
                    res.tasks_preview.append(
                        {
                            "id": tid,
                            "raw_ts": ts,
                            "parsed_due": str(due_at) if due_at else "",
                            "keys": sorted(list(t.keys()))[:30],
                        }
                    )
                assigned_to = None
                rid = int(t.get("responsible_user_id") or 0)
                if rid:
                    assigned_to = _map_amo_user_to_local(amo_user_by_id.get(rid) or {})
                if existing:
                    # апдейтим то, что у вас сейчас выглядит "криво": дедлайн + убрать мусорный id в описании
                    upd = False
                    if title and (existing.title or "").strip() != title:
                        existing.title = title
                        upd = True
                    if existing.description and "[Amo task id:" in existing.description:
                        existing.description = ""
                        upd = True
                    if due_at and (existing.due_at is None or existing.due_at != due_at):
                        existing.due_at = due_at
                        upd = True
                    if company and existing.company_id is None:
                        existing.company = company
                        upd = True
                    if assigned_to and existing.assigned_to_id != assigned_to.id:
                        existing.assigned_to = assigned_to
                        upd = True
                    if upd:
                        if dry_run:
                            res.tasks_would_update += 1
                            res.skipped_writes_dry_run += 1
                            logger.debug(f"DRY-RUN: would update task {tid} for company {company.id}")
                        else:
                            existing.save()
                            res.tasks_updated += 1
                    else:
                        # Задача уже существует и не требует обновления
                        res.tasks_skipped_existing += 1
                    continue

                if dry_run:
                    res.tasks_would_create += 1
                    res.skipped_writes_dry_run += 1
                    logger.debug(f"DRY-RUN: would create task {tid} for company {company.id}")
                else:
                    task = Task(
                        title=title,
                        description="",
                        due_at=due_at,
                        company=company,
                        created_by=actor,
                        assigned_to=assigned_to or actor,
                        external_source="amo_api",
                        external_uid=str(tid),
                        status=Task.Status.NEW,
                    )
                    task.save()
                    res.tasks_created += 1
                
                tasks_processed += 1
            # Логируем статистику обработки задач (в dry-run: would_create, would_update вместо created/updated)
            logger.info(
                f"migrate_filtered: задачи обработаны: processed={tasks_processed}, "
                f"skipped_no_company={tasks_skipped_no_company}, skipped_old={tasks_skipped_old_count}, "
                f"created={res.tasks_created}, updated={res.tasks_updated}, "
                f"would_create={res.tasks_would_create}, would_update={res.tasks_would_update}, "
                f"skipped_existing={res.tasks_skipped_existing}"
            )
        else:
            if dry_run:
                logger.info(f"migrate_filtered: задачи НЕ запрашиваются (dry_run={dry_run}, import_tasks={import_tasks})")
            res.tasks_seen = 0

        # Заметки: запрашиваем только если нужно импортировать
        # ВАЖНО: в dry-run запрашиваем заметки для диагностики, даже если import_notes=False
        # Инициализируем notes_error до веток, чтобы elif notes_error: не вызывал UnboundLocalError,
        # когда блок if import_notes and amo_ids... не выполняется (import_notes=False или not amo_ids).
        notes_error = None
        if import_notes and amo_ids and not (dry_run and not import_notes):
            logger.info(f"migrate_filtered: запрашиваем заметки для {len(amo_ids)} компаний (import_notes={import_notes}, dry_run={dry_run})")
            try:
                notes = fetch_notes_for_companies(client, amo_ids)
                res.notes_seen = len(notes)
                # Сохраняем режим получения заметок
                global _notes_bulk_supported
                if _notes_bulk_supported is False:
                    res.notes_bulk_supported = False
                    res.notes_fetch_mode = "per_company"
                elif _notes_bulk_supported is True:
                    res.notes_bulk_supported = True
                    res.notes_fetch_mode = "bulk"
                else:
                    res.notes_bulk_supported = None
                    res.notes_fetch_mode = "unknown"
                logger.info(f"migrate_filtered: получено заметок из API: {res.notes_seen} для {len(amo_ids)} компаний (режим: {res.notes_fetch_mode})")
                notes_error = None  # Успешно получено, ошибок нет
            except RateLimitError as e:
                # Rate limit при получении заметок - останавливаем импорт заметок с явной ошибкой
                logger.error(
                    f"migrate_filtered: Rate limit исчерпан при получении заметок компаний. "
                    f"Импорт заметок прерван. Ошибка: {e}"
                )
                res.notes_seen = 0
                res.notes_processed = 0
                res.notes_created = 0
                res.notes_skipped_existing = 0
                res.notes_skipped_no_changes = 0
                # Не поднимаем исключение дальше - продолжаем импорт компаний/контактов
                # Но явно помечаем, что заметки не импортированы из-за rate limit
                if res.warnings is None:
                    res.warnings = []
                if res.skip_reasons is None:
                    res.skip_reasons = []
                res.warnings.append(f"Импорт заметок компаний прерван из-за rate limit (429): {e}")
                notes = []  # Продолжаем с пустым списком заметок
                notes_error = f"Rate limit (429): {e}"
            except Exception as e:
                # Любая другая ошибка при получении заметок - логируем, но продолжаем миграцию
                notes_error = f"{type(e).__name__}: {e}"
                logger.error(
                    f"migrate_filtered: ошибка при получении заметок компаний: {e}. "
                    f"Импорт заметок пропущен, продолжаем миграцию контактов.",
                    exc_info=True,  # Полный traceback для диагностики
                )
                res.notes_seen = 0
                res.notes_processed = 0
                res.notes_created = 0
                res.notes_skipped_existing = 0
                res.notes_skipped_no_changes = 0
                if res.warnings is None:
                    res.warnings = []
                if res.skip_reasons is None:
                    res.skip_reasons = []
                res.warnings.append(f"Импорт заметок компаний пропущен из-за ошибки: {notes_error}")
                notes = []  # Продолжаем с пустым списком заметок
        else:
            # В dry-run без import_notes или если import_notes=False - не запрашиваем, но логируем
            if dry_run:
                logger.info(f"migrate_filtered: заметки НЕ запрашиваются (dry_run={dry_run}, import_notes={import_notes})")
            res.notes_seen = 0
            notes = []
        
        # ОПТИМИЗАЦИЯ: убираем N+1 запросы к CompanyNote и Company
        # ВАЖНО: обрабатываем заметки только если они были запрошены и получены
        if notes and not notes_error:
            # Инициализируем debug_count_for_extraction для безопасного использования в debug-логике
            debug_count_for_extraction = 0
            
            note_uids = {str(int(n.get("id") or 0)) for n in notes if int(n.get("id") or 0)}
            existing_notes_by_uid: dict[str, CompanyNote] = {}
            if note_uids:
                for nn in CompanyNote.objects.filter(external_source="amo_api", external_uid__in=note_uids):
                    if nn.external_uid:
                        existing_notes_by_uid[str(nn.external_uid)] = nn
            
            notes_processed = 0
            notes_skipped_amomail = 0
            notes_skipped_no_text = 0
            notes_skipped_no_company = 0
            
            for n in notes:
                    nid = int(n.get("id") or 0)
                    existing_note = existing_notes_by_uid.get(str(nid)) if nid else None

                    # В карточечных notes entity_id часто = id компании в amo
                    entity_id = int((n.get("entity_id") or 0) or 0)
                    company = companies_db_by_amo_id.get(entity_id) if entity_id else None
                    if not company:
                        notes_skipped_no_company += 1
                        continue

                    # В разных типах notes текст может лежать по-разному
                    params = n.get("params") or {}
                    note_type = str(n.get("note_type") or n.get("type") or "").strip()
                    text = str(
                        n.get("text")
                        or params.get("text")
                        or params.get("comment")
                        or params.get("note")
                        or n.get("note")
                        or ""
                    ).strip()
                    if not text:
                        try:
                            text = json.dumps(params, ensure_ascii=False)[:1200] if params else ""
                        except Exception:
                            text = ""
                        if not text:
                            text = f"(без текста) note_type={note_type}"
                            # В dry-run не скипаем: обрабатываем с подставленным текстом для диагностики
                            if not dry_run:
                                notes_skipped_no_text += 1
                                try:
                                    if debug_count_for_extraction < 3:
                                        logger.debug(f"      -> Skipped note {nid} (no text, note_type={note_type})")
                                except (NameError, UnboundLocalError):
                                    pass
                                continue

                    # автор заметки (если можем определить)
                    author = None
                    author_amo_name = ""
                    creator_id = int(n.get("created_by") or n.get("created_by_id") or n.get("responsible_user_id") or 0)
                    if creator_id:
                        au = amo_user_by_id.get(creator_id) or {}
                        author_amo_name = str(au.get("name") or "")
                        author = _map_amo_user_to_local(au)

                    created_ts = n.get("created_at") or n.get("created_at_ts") or None
                    created_label = ""
                    try:
                        if created_ts:
                            ct = int(str(created_ts))
                            if ct > 10**12:
                                ct = int(ct / 1000)
                            created_label = timezone.datetime.fromtimestamp(ct, tz=timezone.utc).strftime("%d.%m.%Y %H:%M")
                    except Exception:
                        created_label = ""

                    prefix = "Импорт из amo"
                    # amomail_message — это по сути история почты; пропускаем такие заметки
                    if note_type.lower().startswith("amomail"):
                        # Пропускаем импорт писем из amoCRM
                        notes_skipped_amomail += 1
                        if debug_count_for_extraction < 3:
                            logger.debug(f"      -> Skipped note type '{note_type}' (amomail)")
                        continue
                        incoming = bool(params.get("income")) if isinstance(params, dict) else False
                        subj = str(params.get("subject") or "").strip()
                        frm = params.get("from") or {}
                        to = params.get("to") or {}
                        frm_s = ""
                        to_s = ""
                        try:
                            frm_s = f"{(frm.get('name') or '').strip()} <{(frm.get('email') or '').strip()}>".strip()
                        except Exception:
                            frm_s = ""
                        try:
                            to_s = f"{(to.get('name') or '').strip()} <{(to.get('email') or '').strip()}>".strip()
                        except Exception:
                            to_s = ""
                        summ = str(params.get("content_summary") or "").strip()
                        attach_cnt = params.get("attach_cnt")
                        lines = []
                        lines.append("Письмо (amoMail) · " + ("Входящее" if incoming else "Исходящее"))
                        if subj:
                            lines.append("Тема: " + subj)
                        if frm_s:
                            lines.append("От: " + frm_s)
                        if to_s:
                            lines.append("Кому: " + to_s)
                        if summ:
                            lines.append("Кратко: " + summ)
                        if attach_cnt not in (None, "", 0, "0"):
                            lines.append("Вложений: " + str(attach_cnt))
                        # для такого типа не подставляем автора как "вы"
                        author = None
                        text = "\n".join(lines) if lines else "Письмо (amoMail)"
                        prefix = "Импорт из amo"
                    elif note_type.lower() in ("call_out", "call_in", "call"):
                        # звонки — тоже форматируем, иначе будет JSON-каша
                        text = _format_call_note(note_type, params)
                        author = None
                        prefix = "Импорт из amo"
                    notes_processed += 1
                    meta_bits = []
                    if author_amo_name:
                        meta_bits.append(f"автор: {author_amo_name}")
                    if created_label:
                        meta_bits.append(f"дата: {created_label}")
                    if note_type:
                        meta_bits.append(f"type: {note_type}")
                    if nid:
                        meta_bits.append(f"id: {nid}")
                    if meta_bits:
                        prefix += " (" + ", ".join(meta_bits) + ")"
                    text_full = prefix + "\n" + text
                    if res.notes_preview is not None and len(res.notes_preview) < 5:
                        res.notes_preview.append(
                            {
                                "id": nid,
                                "type": note_type,
                                "text_head": (text_full[:140] + ("…" if len(text_full) > 140 else "")),
                            }
                        )

                    if existing_note:
                        # если раньше создали "пустышку" — обновим
                        upd = False
                        if existing_note.company_id != company.id:
                            existing_note.company = company
                            upd = True
                        old_text = (existing_note.text or "").strip()
                        # НО: если это amomail - пропускаем (не обновляем и не создаем)
                        if note_type.lower().startswith("amomail"):
                            continue
                        # Переписываем также любые почтовые записи, которые раньше импортировали как JSON-простыню.
                        should_rewrite = (
                            old_text.startswith("Импорт из amo (note id")
                            or len(old_text) < 40
                            or ("type: amomail" in old_text.lower())
                            or ("\"thread_id\"" in old_text)
                            or ("\"uniq\"" in old_text)
                            or note_type.lower().startswith("call_")
                        )
                        if should_rewrite:
                            existing_note.text = text_full[:8000]
                            upd = True
                        if existing_note.author_id == actor.id and (author is None or author.id != actor.id):
                            existing_note.author = author  # может быть None
                            upd = True
                        if upd:
                            if dry_run:
                                res.notes_would_update += 1
                                res.skipped_writes_dry_run += 1
                                logger.debug(f"DRY-RUN: would update note {nid} for company {company.id}")
                            else:
                                existing_note.save()
                                res.notes_updated += 1
                        else:
                            # Заметка уже существует и не требует обновления (no changes to apply)
                            res.notes_skipped_existing += 1
                            res.notes_skipped_no_changes += 1
                        continue

                    # Новая заметка
                    if dry_run:
                        res.notes_would_add += 1
                        res.skipped_writes_dry_run += 1
                        logger.debug(f"DRY-RUN: would create note {nid} for company {company.id}")
                    else:
                        note = CompanyNote(
                            company=company,
                            author=author,  # НЕ actor, чтобы не выглядело "как будто вы писали"
                            text=text_full[:8000],
                            external_source="amo_api",
                            external_uid=str(nid) if nid else "",
                        )
                        note.save()
                        res.notes_created += 1

            res.notes_processed = notes_processed
            # Логируем статистику. В processed-ветке: processed = would_add + would_update + skipped_existing
            # (skipped_no_changes == skipped_existing). skipped_amomail/no_text/no_company — до processed.
            logger.info(
                f"migrate_filtered: заметки обработаны: processed={notes_processed}, "
                f"skipped_amomail={notes_skipped_amomail}, skipped_no_text={notes_skipped_no_text}, "
                f"skipped_no_company={notes_skipped_no_company}, "
                f"would_add={res.notes_would_add}, would_update={res.notes_would_update}, "
                f"skipped_existing={res.notes_skipped_existing}, skipped_no_changes={res.notes_skipped_no_changes}, "
                f"created={res.notes_created}, updated={res.notes_updated}"
            )
        elif notes_error:
            # Если была ошибка при получении заметок - логируем, но не обрабатываем
            logger.warning(f"migrate_filtered: этап обработки заметок пропущен из-за ошибки: {notes_error}")
            if res.warnings is None:
                res.warnings = []
            res.warnings.append(f"Этап обработки заметок пропущен из-за ошибки: {notes_error}")

        # Импорт контактов компаний из amoCRM (опционально, т.к. может быть медленно)
        # Важно: импортируем контакты ТОЛЬКО для компаний из текущей пачки (amo_ids)
        # Инициализируем счетчики контактов всегда (даже если импорт выключен)
        res.contacts_seen = 0
        res.contacts_created = 0
        
        # В DRY-RUN всегда показываем контакты (даже если import_contacts=False),
        # чтобы пользователь мог увидеть, что будет импортировано
        # В реальном импорте обрабатываем только если import_contacts=True
        should_process_contacts = (dry_run or import_contacts) and amo_ids
        
        logger.info(f"migrate_filtered: проверка импорта контактов: import_contacts={import_contacts}, dry_run={dry_run}, should_process_contacts={should_process_contacts}, amo_ids={bool(amo_ids)}, len={len(amo_ids) if amo_ids else 0}")
        if should_process_contacts:
            # Инициализируем счетчики до блока try, чтобы они были доступны в finally
            contacts_processed = 0  # счетчик обработанных контактов
            contacts_skipped = 0  # счетчик пропущенных контактов
            contacts_errors = 0  # счетчик ошибок при обработке контактов
            
            # ВАЖНО: в реальном импорте (не dry-run) обрабатываем контакты только если import_contacts=True
            # В dry-run показываем контакты всегда для preview
            if not dry_run and not import_contacts:
                logger.info(f"migrate_filtered: реальный импорт, но import_contacts=False - пропускаем обработку контактов")
                # Не обрабатываем контакты, но инициализируем счетчики
                res.contacts_seen = 0
                res.contacts_created = 0
            else:
                res._debug_contacts_logged = 0  # счетчик для отладки
                logger.info(f"migrate_filtered: ===== НАЧАЛО ИМПОРТА КОНТАКТОВ для {len(amo_ids)} компаний =====")
                logger.info(f"migrate_filtered: ID компаний для поиска контактов: {amo_ids[:10]}...")
            try:
                # ОПТИМИЗАЦИЯ: используем bulk-получение контактов вместо запроса для каждой компании
                # Rate limiting применяется автоматически в AmoClient
                logger.info(f"migrate_filtered: вызываем fetch_contacts_bulk для {len(amo_ids)} компаний...")
                
                # fetch_contacts_bulk уже фильтрует контакты по компаниям и возвращает маппинг
                full_contacts, contact_id_to_company_map, contact_warnings = fetch_contacts_bulk(client, amo_ids)
                res.contacts_seen = len(full_contacts)
                logger.info(f"migrate_filtered: получено {res.contacts_seen} контактов из API для {len(amo_ids)} компаний (bulk-метод)")
                # Сохраняем предупреждения в результат
                if contact_warnings:
                    if res.warnings is None:
                        res.warnings = []
                    res.warnings.extend(contact_warnings)
                
                # Если контактов не найдено, сохраняем информацию об ошибке
                if res.contacts_seen == 0:
                    logger.warning(f"migrate_filtered: ⚠️ КОНТАКТЫ НЕ НАЙДЕНЫ для компаний {list(amo_ids)[:10]}. Отфильтровано: {res.contacts_seen}")
                    if res.contacts_preview is None:
                        res.contacts_preview = []
                    debug_info = {
                        "status": "NO_CONTACTS_FOUND",
                        "companies_checked": len(amo_ids),
                        "company_ids": list(amo_ids)[:5],  # первые 5 для отладки
                        "message": f"Контакты не найдены для компаний {list(amo_ids)[:5]}. Проверьте, что у компаний есть связанные контакты в AmoCRM. Использовался bulk-метод GET /api/v4/contacts?filter[company_id][]=...",
                    }
                    res.contacts_preview.append(debug_info)
                
                # Заметки контактов: НЕ запрашиваем для dry-run (слишком тяжело)
                # Заметки нужны только при реальном импорте, и то можно запросить отдельно
                # ОПТИМИЗАЦИЯ: используем bulk-метод для получения заметок
                # ОПТИМИЗАЦИЯ: заметки часто возвращают 404, делаем запрос опциональным и обрабатываем ошибки
                contact_notes_map: dict[int, list[dict[str, Any]]] = {}
                if not dry_run and full_contacts:
                    # Заметки запрашиваем только при реальном импорте через bulk-метод
                    # ОПТИМИЗАЦИЯ: пропускаем запрос заметок, если их слишком много (ускоряет импорт)
                    contact_ids_for_notes = [int(c.get("id") or 0) for c in full_contacts if isinstance(c, dict) and c.get("id")]
                    if contact_ids_for_notes and len(contact_ids_for_notes) <= 50:  # Запрашиваем заметки только для небольших батчей
                        logger.info(f"migrate_filtered: запрашиваем заметки для {len(contact_ids_for_notes)} контактов (bulk-метод)...")
                        try:
                            contact_notes_map = fetch_notes_for_contacts_bulk(client, contact_ids_for_notes)
                            logger.info(f"migrate_filtered: получено заметок для {len(contact_notes_map)} контактов")
                        except RateLimitError as e:
                            # Rate limit при получении заметок контактов - останавливаем импорт заметок с явной ошибкой
                            logger.error(
                                f"migrate_filtered: Rate limit исчерпан при получении заметок контактов. "
                                f"Импорт заметок контактов прерван. Ошибка: {e}"
                            )
                            contact_notes_map = {}
                            # Помечаем в warnings
                            if res.warnings is None:
                                res.warnings = []
                            res.warnings.append(f"Импорт заметок контактов прерван из-за rate limit (429): {e}")
                        except Exception as e:
                            # ОПТИМИЗАЦИЯ: не прерываем импорт при ошибке получения заметок (часто 404)
                            logger.warning(f"migrate_filtered: ошибка при получении заметок контактов (пропускаем): {e}")
                            contact_notes_map = {}
                    elif contact_ids_for_notes:
                        logger.info(f"migrate_filtered: пропускаем запрос заметок для {len(contact_ids_for_notes)} контактов (слишком много, ускоряет импорт)")
                
                # Отдельный счетчик для логирования структуры (не зависит от preview)
                structure_logged_count = 0
                
                # Создаем словарь для быстрого поиска компаний по amo_id
                # В dry-run используем local_companies (которые созданы в памяти, но не сохранены в БД)
                # В реальном импорте используем БД
                local_companies_by_amo_id: dict[int, Company] = {}
                if dry_run:
                    # В dry-run используем компании из local_companies (созданные в памяти)
                    for comp in local_companies:
                        if comp.amocrm_company_id:
                            local_companies_by_amo_id[int(comp.amocrm_company_id)] = comp
                    logger.info(f"migrate_filtered: создан словарь local_companies_by_amo_id для dry-run: {len(local_companies_by_amo_id)} компаний")
                else:
                    # В реальном импорте загружаем из БД
                    for comp in local_companies:
                        if comp.amocrm_company_id:
                            local_companies_by_amo_id[int(comp.amocrm_company_id)] = comp
                    # Также загружаем существующие компании из БД (на случай, если они уже были импортированы ранее)
                    existing_companies = Company.objects.filter(amocrm_company_id__in=amo_ids).all()
                    for comp in existing_companies:
                        if comp.amocrm_company_id:
                            local_companies_by_amo_id[int(comp.amocrm_company_id)] = comp
                
                # Теперь обрабатываем полные данные контактов
                # Сбрасываем счетчики перед началом обработки (они уже инициализированы до try)
                logger.info(f"migrate_filtered: ===== НАЧАЛО ОБРАБОТКИ {len(full_contacts)} КОНТАКТОВ =====")
                contacts_processed = 0  # Сброс перед обработкой контактов
                contacts_skipped = 0  # Сброс перед обработкой контактов
                contacts_errors = 0  # Сброс перед обработкой контактов
                
                # ОПТИМИЗАЦИЯ: собираем контакты для bulk_update
                contacts_to_update: list[Contact] = []
                contacts_to_create: list[Contact] = []
                
                # ОПТИМИЗАЦИЯ: предзагружаем существующие контакты, телефоны и почты для всей пачки
                # Это убирает N+1 запросы в цикле
                if not dry_run and full_contacts:
                    amo_contact_ids = [int(c.get("id") or 0) for c in full_contacts if isinstance(c, dict) and c.get("id")]
                    if amo_contact_ids:
                        # Предзагружаем существующие контакты
                        existing_contacts_map = {
                            (c.amocrm_contact_id, c.company_id): c
                            for c in Contact.objects.filter(amocrm_contact_id__in=amo_contact_ids).select_related('company')
                        }
                        
                        # Предзагружаем телефоны для всех контактов
                        contact_ids_for_phones = [c.id for c in existing_contacts_map.values()]
                        existing_phones_map: dict[tuple[UUID, str], ContactPhone] = {}
                        if contact_ids_for_phones:
                            for phone in ContactPhone.objects.filter(contact_id__in=contact_ids_for_phones).select_related('contact'):
                                key = (phone.contact_id, phone.value.lower().strip())
                                existing_phones_map[key] = phone
                        
                        # Предзагружаем почты для всех контактов
                        existing_emails_map: dict[tuple[UUID, str], ContactEmail] = {}
                        if contact_ids_for_phones:
                            for email in ContactEmail.objects.filter(contact_id__in=contact_ids_for_phones).select_related('contact'):
                                key = (email.contact_id, email.value.lower().strip())
                                existing_emails_map[key] = email
                        
                        logger.debug(f"migrate_filtered: предзагружено {len(existing_contacts_map)} контактов, {len(existing_phones_map)} телефонов, {len(existing_emails_map)} почт")
                    else:
                        existing_contacts_map = {}
                        existing_phones_map = {}
                        existing_emails_map = {}
                else:
                    existing_contacts_map = {}
                    existing_phones_map = {}
                    existing_emails_map = {}
                
                for ac_idx, ac in enumerate(full_contacts):
                    contacts_processed += 1
                    if ac_idx < 5 or contacts_processed % 10 == 0:
                        logger.info(f"migrate_filtered: обработка контакта {ac_idx + 1}/{len(full_contacts)} (processed: {contacts_processed}, skipped: {contacts_skipped}, errors: {contacts_errors})")
                    
                    # Инициализируем переменные ДО блока try, чтобы они были доступны после блока try/except
                    amo_contact_id = 0
                    local_company = None
                    existing_contact = None
                    phones: list[tuple[str, str, str]] = []
                    emails: list[tuple[str, str]] = []
                    position = ""
                    cold_call_timestamp = None
                    note_text = ""
                    birthday_timestamp = None
                    first_name = ""
                    last_name = ""
                    
                    try:
                        # ОТЛАДКА: логируем сырую структуру контакта для первых 3
                        if structure_logged_count < 3:
                            logger.debug(f"===== RAW CONTACT STRUCTURE ({structure_logged_count + 1}) [index {ac_idx}] =====")
                        logger.debug(f"  - Type: {type(ac)}")
                        logger.debug(f"  - ac is None: {ac is None}")
                        if ac is None:
                            logger.debug(f"  - ⚠️ Contact is None!")
                        elif isinstance(ac, dict):
                            logger.debug(f"  - Keys: {list(ac.keys())}")
                            logger.debug(f"  - Has 'id': {'id' in ac}, id value: {ac.get('id')}")
                            logger.debug(f"  - Has 'first_name': {'first_name' in ac}, value: {ac.get('first_name')}")
                            logger.debug(f"  - Has 'last_name': {'last_name' in ac}, value: {ac.get('last_name')}")
                            logger.debug(f"  - Has 'custom_fields_values': {'custom_fields_values' in ac}")
                            if 'custom_fields_values' in ac:
                                cfv = ac.get('custom_fields_values')
                                logger.debug(f"  - custom_fields_values type: {type(cfv)}, length: {len(cfv) if isinstance(cfv, list) else 'not_list'}")
                                if isinstance(cfv, list) and len(cfv) > 0:
                                    logger.debug(f"  - First custom_field: {cfv[0]}")
                            logger.debug(f"  - Has 'phone': {'phone' in ac}, value: {ac.get('phone')}")
                            logger.debug(f"  - Has 'email': {'email' in ac}, value: {ac.get('email')}")
                            # Полная JSON-структура - ВАЖНО для поиска примечаний!
                            import json
                            try:
                                json_str = json.dumps(ac, ensure_ascii=False, indent=2)
                                # Увеличиваем размер для поиска примечаний
                                logger.debug(f"  - Full JSON (first 5000 chars):\n{json_str[:5000]}")
                                # Также проверяем наличие ключевых полей для примечаний
                                note_related_keys = [k for k in ac.keys() if any(word in k.lower() for word in ["note", "comment", "remark", "примеч", "коммент"])]
                                if note_related_keys:
                                    logger.debug(f"  - ⚠️ Found note-related keys: {note_related_keys}")
                                    for key in note_related_keys:
                                        logger.debug(f"    - {key}: {str(ac.get(key))[:200]}")
                            except Exception as e:
                                logger.debug(f"  - JSON dump error: {e}")
                                import traceback
                                logger.debug(f"  - Traceback: {traceback.format_exc()}")
                                logger.debug(f"  - Full contact (first 500 chars): {str(ac)[:500]}")
                        else:
                            logger.debug(f"  - Contact is not a dict: {ac}, type: {type(ac)}")
                            logger.debug(f"===== END RAW STRUCTURE =====")
                            structure_logged_count += 1
                        
                        amo_contact_id = int(ac.get("id") or 0) if isinstance(ac, dict) else 0
                        
                        # Добавляем заметки из contact_notes_map, если их нет в _embedded
                        if amo_contact_id and amo_contact_id in contact_notes_map:
                            notes_from_map = contact_notes_map[amo_contact_id]
                            if notes_from_map and isinstance(ac, dict):
                                # Добавляем заметки в _embedded, если их там нет
                                if "_embedded" not in ac:
                                    ac["_embedded"] = {}
                                if not isinstance(ac["_embedded"], dict):
                                    ac["_embedded"] = {}
                                if "notes" not in ac["_embedded"] or not ac["_embedded"]["notes"]:
                                    ac["_embedded"]["notes"] = notes_from_map
                                    if structure_logged_count < 3:
                                        logger.debug(
                                            f"  -> Added {len(notes_from_map)} notes from contact_notes_map to contact {amo_contact_id}"
                                        )
                        
                        if not amo_contact_id:
                            # ОТЛАДКА: контакт без ID
                            contacts_skipped += 1
                            debug_count = getattr(res, '_debug_contacts_logged', 0)
                            if res.contacts_preview is None:
                                res.contacts_preview = []
                            preview_limit_skip = 50 if dry_run else 10
                            if debug_count < preview_limit_skip:
                                res._debug_contacts_logged = debug_count + 1
                                res.contacts_preview.append({
                                    "status": "SKIPPED_NO_ID",
                                    "raw_contact_keys": list(ac.keys())[:10] if isinstance(ac, dict) else "not_dict",
                                })
                            continue
                        
                        # Находим компанию для этого контакта через contact_id_to_company_map
                        # ВАЖНО: в dry-run используем local_companies_by_amo_id (компании в памяти)
                        # В реальном импорте используем БД или local_companies_by_amo_id
                        local_company = None
                        amo_company_id_for_contact = None
                        
                        contact_id = int(ac.get("id") or 0)
                        if contact_id in contact_id_to_company_map:
                            amo_company_id_for_contact = contact_id_to_company_map[contact_id]
                            # Сначала ищем в словаре (работает и для dry-run, и для реального импорта)
                            local_company = local_companies_by_amo_id.get(amo_company_id_for_contact)
                            # Если не нашли в словаре и это не dry-run, ищем в БД
                            if not local_company and not dry_run:
                                local_company = Company.objects.filter(amocrm_company_id=amo_company_id_for_contact).first()
                        
                        # Fallback: если не нашли через map, пробуем через company_id в самом контакте
                        if not local_company:
                            cid = int(ac.get("company_id") or 0)
                            if cid and cid in amo_ids_set:
                                # Сначала ищем в словаре
                                local_company = local_companies_by_amo_id.get(cid)
                                # Если не нашли в словаре и это не dry-run, ищем в БД
                                if not local_company and not dry_run:
                                    local_company = Company.objects.filter(amocrm_company_id=cid).first()
                                if local_company:
                                    amo_company_id_for_contact = cid
                        
                        if not local_company:
                            # ОТЛАДКА: контакт не связан с компанией из текущей пачки
                            # В dry-run показываем ВСЕ такие контакты
                            debug_count = getattr(res, '_debug_contacts_logged', 0)
                            if res.contacts_preview is None:
                                res.contacts_preview = []
                            preview_limit_skip = 1000 if dry_run else 10
                            if debug_count < preview_limit_skip:
                                # Полный анализ контакта даже если компания не найдена
                                full_analysis_skipped = _analyze_contact_completely(ac)
                                name_str = str(ac.get("name") or "").strip()
                                first_name_raw = str(ac.get("first_name") or "").strip()
                                last_name_raw = str(ac.get("last_name") or "").strip()
                                last_name_skipped, first_name_skipped = _parse_fio(name_str, first_name_raw, last_name_raw)
                            
                                debug_data = {
                                    "status": "SKIPPED_NO_LOCAL_COMPANY",
                                    "amo_contact_id": amo_contact_id,
                                    "last_name": last_name_skipped,
                                    "first_name": first_name_skipped,
                                    "amo_company_id_for_contact": amo_company_id_for_contact,
                                    "standard_fields": full_analysis_skipped.get("standard_fields", {}),
                                    "all_custom_fields": [
                                        {
                                            "field_id": cf.get("field_id"),
                                            "field_name": cf.get("field_name"),
                                            "field_code": cf.get("field_code"),
                                            "field_type": cf.get("field_type"),
                                            "values_count": cf.get("values_count", 0),
                                            "values": [
                                                {
                                                    "value": str(v.get("value", "")),
                                                    "enum_code": v.get("enum_code"),
                                                    "enum_id": v.get("enum_id"),
                                                }
                                                for v in cf.get("values", [])
                                            ],
                                        }
                                        for cf in full_analysis_skipped.get("custom_fields", [])
                                    ],
                                    "custom_fields_count": len(full_analysis_skipped.get("custom_fields", [])),
                                }
                                res.contacts_preview.append(debug_data)
                                res._debug_contacts_logged = debug_count + 1
                            continue
                        # Извлекаем данные контакта (делаем это ДО проверки на existing, чтобы всегда было в preview)
                        # Парсим ФИО с помощью функции _parse_fio
                        # Сначала очищаем имена от "доб." и инструкций
                        name_str = str(ac.get("name") or "").strip()
                        first_name_raw = str(ac.get("first_name") or "").strip()
                        last_name_raw = str(ac.get("last_name") or "").strip()
                        
                        # Очищаем имена от extension/инструкций
                        name_cleaned, name_extracted = sanitize_name(name_str)
                        first_name_cleaned, first_name_extracted = sanitize_name(first_name_raw)
                        last_name_cleaned, last_name_extracted = sanitize_name(last_name_raw)
                        
                        # Объединяем извлеченные инструкции
                        all_extracted = [e for e in [name_extracted, first_name_extracted, last_name_extracted] if e]
                        if all_extracted:
                            extracted_text = ', '.join(all_extracted)
                            # Увеличиваем счетчик метрики
                            res.name_cleaned_extension_moved_to_note += 1
                            res.name_instructions_moved_to_note += 1
                            if not note_text:
                                note_text = extracted_text[:255]
                            elif extracted_text not in note_text:
                                combined = f"{note_text}; {extracted_text[:200]}"
                                note_text = combined[:255]
                        
                        # Парсим очищенные имена
                        last_name, first_name = _parse_fio(name_cleaned, first_name_cleaned, last_name_cleaned)
                    
                        # ОТЛАДКА: логируем начало обработки контакта с улучшенным выводом
                        preview_count_before = len(res.contacts_preview) if res.contacts_preview else 0
                        if preview_count_before < 3:
                            logger.debug(f"Processing contact {amo_contact_id} (parsed: last_name={last_name}, first_name={first_name})")
                            logger.debug(f"  - raw: name={name_str}, first_name={first_name_raw}, last_name={last_name_raw}")
                            logger.debug(f"  - local_company: {local_company.id if local_company else None}")
                            
                            # Улучшенный debug: показываем структуру контакта
                            if isinstance(ac, dict):
                                all_keys = list(ac.keys())
                                logger.debug(f"  - Ключи контакта: {all_keys}")
                                logger.debug(f"  - id: {ac.get('id')}, name: {ac.get('name')}, first_name: {ac.get('first_name')}, last_name: {ac.get('last_name')}")
                                
                                # Показываем custom_fields_values с маскированием
                                custom_fields_debug = ac.get("custom_fields_values")
                                if custom_fields_debug is None:
                                    logger.debug(f"  - custom_fields_values: None")
                                elif isinstance(custom_fields_debug, list):
                                    logger.debug(f"  - custom_fields_values: list, length={len(custom_fields_debug)}")
                                    # Показываем первые 2 поля с маскированием
                                    for cf_idx, cf in enumerate(custom_fields_debug[:2]):
                                        if isinstance(cf, dict):
                                            field_name = str(cf.get('field_name') or '').strip()
                                            field_code = str(cf.get('field_code') or '').strip()
                                            values = cf.get('values') or []
                                            if values and isinstance(values, list) and len(values) > 0:
                                                v = values[0]
                                                if isinstance(v, dict):
                                                    val = str(v.get('value', ''))
                                                else:
                                                    val = str(v)
                                                # Маскируем телефоны и емейлы
                                                if field_code == "PHONE" or "телефон" in field_name.lower():
                                                    val_masked = _mask_phone(val)
                                                elif field_code == "EMAIL" or "email" in field_name.lower() or "почта" in field_name.lower():
                                                    val_masked = _mask_email(val)
                                                else:
                                                    val_masked = val[:50]  # Ограничиваем длину
                                                logger.debug(f"    [{cf_idx}] field_id={cf.get('field_id')}, code='{field_code}', name='{field_name}', value='{val_masked}'")
                                else:
                                    logger.debug(f"  - custom_fields_values: {type(custom_fields_debug)} (not list)")
                            else:
                                logger.debug(f"  - contact is not dict: {type(ac)}")
                            if isinstance(ac, dict) and 'custom_fields_values' in ac:
                                cfv = ac.get('custom_fields_values')
                                logger.debug(f"  - custom_fields_values: type={type(cfv)}, length={len(cfv) if isinstance(cfv, list) else 'not_list'}")
                    
                        # Проверяем, не импортировали ли уже этот контакт
                        # ОПТИМИЗАЦИЯ: используем предзагруженную карту вместо запроса к БД
                        company_id_for_key = local_company.id if local_company and hasattr(local_company, 'id') else None
                        existing_contact = existing_contacts_map.get((amo_contact_id, company_id_for_key))
                        
                        # Если не найдено в предзагруженных и это dry-run, делаем запрос (только для dry-run)
                        if not existing_contact and dry_run and local_company and hasattr(local_company, 'id'):
                            existing_contact = Contact.objects.filter(amocrm_contact_id=amo_contact_id, company=local_company).first()
                    
                        # В amoCRM телефоны и email могут быть:
                        # 1. В стандартных полях (phone, email) - если они есть
                        # 2. В custom_fields_values с field_code="PHONE"/"EMAIL" или по field_name
                        # 3. В custom_fields_values по названию поля
                        # phones/emails: сохраняем тип и комментарий (enum_code) для корректного отображения
                        # Переменные уже инициализированы ДО блока try, сбрасываем их значения для текущего контакта
                        phones = []
                        emails = []
                        position = ""
                        cold_call_timestamp = None  # Timestamp холодного звонка из amoCRM
                        note_text = ""  # "Примечание"/"Комментарий" контакта (одно на все номера)
                        birthday_timestamp = None  # Timestamp дня рождения из amoCRM (если есть)
                    
                        # ОТЛАДКА: определяем счетчик для логирования (ДО использования)
                        # Инициализируем всегда, чтобы избежать UnboundLocalError
                        debug_count_for_extraction = len(res.contacts_preview) if res.contacts_preview else 0
                    
                        # ВАЖНО: сначала проверяем custom_fields (там хранится поле "Примечание"),
                        # потом заметки (там могут быть служебные заметки типа call_out)
                    
                        # custom_fields_values для телефонов/почт/должности/примечаний
                        # Безопасная обработка: custom_fields_values может быть None/[]/не список
                        custom_fields_raw = ac.get("custom_fields_values")
                        if custom_fields_raw is None:
                            custom_fields = []
                        elif isinstance(custom_fields_raw, list):
                            custom_fields = custom_fields_raw
                        else:
                            # Если не список - пытаемся преобразовать или игнорируем
                            logger.warning(f"Contact {amo_contact_id}: custom_fields_values is not a list: {type(custom_fields_raw)}, ignoring")
                            custom_fields = []
                        
                        # ОТЛАДКА: логируем структуру custom_fields для первых контактов
                        # Защита от ошибок в debug-логике: оборачиваем в try-except
                        try:
                            if debug_count_for_extraction < 3:
                                logger.debug(f"Extracting data from custom_fields for contact {amo_contact_id}:")
                                logger.debug(f"  - custom_fields type: {type(custom_fields)}, length: {len(custom_fields)}")
                                # Логируем ВСЕ поля для отладки (чтобы увидеть, какие поля есть) с маскированием
                                if isinstance(custom_fields, list) and len(custom_fields) > 0:
                                    logger.debug(f"  - ALL custom_fields ({len(custom_fields)} fields):")
                                    for cf_idx, cf in enumerate(custom_fields[:5]):  # Показываем первые 5
                                        if isinstance(cf, dict):
                                            field_name = str(cf.get('field_name') or '').strip()
                                            field_code = str(cf.get('field_code') or '').strip()
                                            values = cf.get('values') or []
                                            first_val = ""
                                            if values and isinstance(values, list) and len(values) > 0:
                                                v = values[0]
                                                if isinstance(v, dict):
                                                    first_val = str(v.get('value', ''))
                                                else:
                                                    first_val = str(v)
                                                # Маскируем телефоны и емейлы
                                                if field_code == "PHONE" or "телефон" in field_name.lower():
                                                    first_val = _mask_phone(first_val)
                                                elif field_code == "EMAIL" or "email" in field_name.lower() or "почта" in field_name.lower():
                                                    first_val = _mask_email(first_val)
                                            logger.debug(f"    [{cf_idx}] {field_name} ({field_code}): {first_val[:50]}")
                                elif len(custom_fields) == 0:
                                    logger.debug(f"  - ⚠️ custom_fields is empty list (no custom fields found)")
                                else:
                                    logger.debug(f"  - ⚠️ custom_fields is not a list: {type(custom_fields)}")
                        except (NameError, UnboundLocalError, Exception) as debug_err:
                            # Защита от ошибок в debug-логике - не валим миграцию
                            logger.debug(f"Debug preview failed (non-critical): {debug_err}", exc_info=False)
                    
                        # ПРОВЕРЯЕМ ВСЕ ВОЗМОЖНЫЕ МЕСТА ДЛЯ ПРИМЕЧАНИЙ:
                        # 1. Прямые поля контакта - проверяем ВСЕ возможные варианты
                        # В amoCRM примечание может быть в разных полях
                        direct_note_keys = ["note", "notes", "comment", "comments", "remark", "remarks", "text", "description", "description_text"]
                        for note_key in direct_note_keys:
                            note_val_raw = ac.get(note_key)
                            if note_val_raw:
                                # Может быть строка или список
                                if isinstance(note_val_raw, list):
                                    note_val = " ".join([str(v) for v in note_val_raw if v]).strip()
                                else:
                                    note_val = str(note_val_raw).strip()
                                # Пропускаем ID и очень короткие значения
                                if note_val and len(note_val) > 3 and not note_val.isdigit():
                                    if not note_text:
                                        note_text = note_val[:255]
                                        if debug_count_for_extraction < 3:
                                            logger.debug(f"  -> ✅ Found note_text in direct field '{note_key}': {note_text[:100]}")
                                    else:
                                        # Объединяем, если уже есть
                                        combined = f"{note_text}; {note_val[:100]}"
                                        note_text = combined[:255]
                                        if debug_count_for_extraction < 3:
                                            logger.debug(f"  -> Appended note_text from direct field '{note_key}': {note_val[:100]}")
                    
                        # 2. В custom_fields_values - ПРИОРИТЕТ! Здесь хранится поле "Примечание"
                        # (обработка будет ниже в цикле по custom_fields)
                    
                        # 3. В _embedded.notes (если есть) - это заметки контакта из amoCRM (служебные, не примечания)
                        if isinstance(ac, dict) and "_embedded" in ac:
                            embedded = ac.get("_embedded") or {}
                            if isinstance(embedded, dict) and "notes" in embedded:
                                    notes_list = embedded.get("notes") or []
                                    if isinstance(notes_list, list) and notes_list:
                                        if debug_count_for_extraction < 3:
                                            logger.debug(f"  -> Found {len(notes_list)} notes in _embedded.notes")
                                        # Ищем примечание в заметках (обычно это текстовые заметки)
                                        for note_idx, note_item in enumerate(notes_list):
                                            if isinstance(note_item, dict):
                                                # В заметках текст может быть в разных полях
                                                note_val = (
                                                    str(note_item.get("text") or "").strip() or
                                                    str(note_item.get("note") or "").strip() or
                                                    str(note_item.get("comment") or "").strip() or
                                                    str(note_item.get("note_type") or "").strip()  # иногда тип заметки содержит текст
                                                )
                                                # Также проверяем параметры заметки
                                                if not note_val and "params" in note_item:
                                                    params = note_item.get("params") or {}
                                                    if isinstance(params, dict):
                                                        note_val = (
                                                            str(params.get("text") or "").strip() or
                                                            str(params.get("comment") or "").strip() or
                                                            str(params.get("note") or "").strip()
                                                        )
                                        
                                                # ВАЖНО: не берем служебные заметки (call_out, call_in и т.д.) как примечание
                                                # Но берем заметки типа "common", "text", "common_message" - это могут быть примечания!
                                                note_type_val = str(note_item.get("note_type") or "").strip().lower()
                                                is_service_note = note_type_val in ["call_out", "call_in", "call", "amomail", "sms", "task"]
                                                is_note_type = note_type_val in ["common", "text", "common_message", "message", "note"]
                                        
                                                # Берем заметки типа "common"/"text" (это примечания) или любые заметки с текстом, если нет служебных
                                                if note_val and len(note_val) > 5:
                                                    # ВАЖНО: заметки типа "common" или "text" - это ПРИОРИТЕТНЫЕ примечания
                                                    # Они должны заменять служебные заметки (call_out и т.д.)
                                                    if is_note_type:
                                                        # Заменяем, если нет примечания ИЛИ если текущее примечание - служебная заметка
                                                        current_is_service = note_text and (
                                                            "call_" in str(note_text).lower() or 
                                                            str(note_text).lower() in ["call_out", "call_in", "call", "amomail", "sms", "task"] or
                                                            len(str(note_text).strip()) < 10
                                                        )
                                                        if not note_text or current_is_service:
                                                            note_text = note_val[:255]
                                                            if debug_count_for_extraction < 3:
                                                                logger.debug(f"  -> ✅ Found note_text in _embedded.notes[{note_idx}] (type={note_type_val}): {note_text[:100]}")
                                                        else:
                                                            combined = f"{note_text}; {note_val[:100]}"
                                                            note_text = combined[:255]
                                                            if debug_count_for_extraction < 3:
                                                                logger.debug(f"  -> Appended note_text from _embedded.notes[{note_idx}] (type={note_type_val}): {note_val[:100]}")
                                                    # Если это не служебная заметка и у нас еще нет примечания - берем её
                                                    elif not is_service_note and not note_text:
                                                        note_text = note_val[:255]
                                                        if debug_count_for_extraction < 3:
                                                            logger.debug(f"  -> Found note_text in _embedded.notes[{note_idx}] (type={note_type_val}, not service): {note_text[:100]}")
                                                    # Берем первые 5 заметок (чтобы найти примечание)
                                                    if note_idx >= 4:
                                                        break
                                                elif is_service_note and debug_count_for_extraction < 3:
                                                    logger.debug(f"  -> Skipped service note type '{note_type_val}' (not a real note)")
                    
                        # Стандартные поля (если есть)
                        # Обработка телефонов с нормализацией через parse_phone_value
                        # СТРОГАЯ ПРОВЕРКА: только валидные телефоны попадают в PHONE
                        if ac.get("phone"):
                            for pv in _split_multi(str(ac.get("phone"))):
                                if pv:
                                    parsed = parse_phone_value(pv)
                                    if parsed.phones:
                                        # Телефоны найдены - добавляем их
                                        for phone_e164 in parsed.phones:
                                            if is_valid_phone(phone_e164):
                                                comment_parts = []
                                                if parsed.extension:
                                                    comment_parts.append(f"доб. {parsed.extension}")
                                                    res.phones_extracted_with_extension += 1
                                                if parsed.comment:
                                                    comment_parts.append(parsed.comment)
                                                comment = "; ".join(comment_parts) if comment_parts else ""
                                                phones.append((ContactPhone.PhoneType.OTHER, phone_e164, comment[:255]))
                                            else:
                                                # Телефон не валиден - переносим в note
                                                res.phones_rejected_invalid += 1
                                                note_to_add = parsed.comment or f"Комментарий к телефону: {phone_e164}"
                                                if not note_text:
                                                    note_text = note_to_add[:255]
                                                elif note_to_add not in note_text:
                                                    combined = f"{note_text}; {note_to_add[:200]}"
                                                    note_text = combined[:255]
                                    else:
                                        # Телефоны не найдены - весь текст в note
                                        note_to_add = parsed.comment or f"Комментарий к телефону: {pv}"
                                        if parsed.rejected_reason and "instruction" in parsed.rejected_reason.lower():
                                            res.phones_rejected_as_note += 1
                                        else:
                                            res.phones_rejected_invalid += 1
                                        if not note_text:
                                            note_text = note_to_add[:255]
                                        elif note_to_add not in note_text:
                                            combined = f"{note_text}; {note_to_add[:200]}"
                                            note_text = combined[:255]
                        if ac.get("email"):
                            ev = str(ac.get("email")).strip()
                            if ev and validate_email(ev):
                                emails.append((ContactEmail.EmailType.OTHER, ev))
                            elif ev:
                                # Email не прошел валидацию
                                res.emails_rejected_invalid_format += 1
                    
                        # custom_fields_values для телефонов/почт/должности/примечаний
                        # Переменная custom_fields уже определена выше, используем её
                        # (custom_fields уже безопасно обработан выше)
                        
                        # ОТЛАДКА: логируем структуру custom_fields для первых контактов
                        # Защита от ошибок в debug-логике: оборачиваем в try-except
                        try:
                            if debug_count_for_extraction < 3:
                                logger.debug(f"Extracting data from custom_fields for contact {amo_contact_id}:")
                                logger.debug(f"  - custom_fields type: {type(custom_fields)}, length: {len(custom_fields)}")
                                # Логируем ВСЕ ключи контакта для поиска примечаний
                                if isinstance(custom_fields, list) and len(custom_fields) > 0:
                                    logger.debug(f"  - ALL custom_fields ({len(custom_fields)} fields):")
                                    for cf_idx, cf in enumerate(custom_fields[:5]):  # Показываем первые 5
                                        if isinstance(cf, dict):
                                            field_name = str(cf.get('field_name') or '').strip()
                                            field_code = str(cf.get('field_code') or '').strip()
                                            logger.debug(f"    [{cf_idx}] id={cf.get('field_id')}, code='{field_code}', name='{field_name}'")
                                elif len(custom_fields) == 0:
                                    logger.debug(f"  - ⚠️ custom_fields is empty list (no custom fields found)")
                                else:
                                    logger.debug(f"  - ⚠️ custom_fields is not a list: {type(custom_fields)}")
                        except (NameError, UnboundLocalError, Exception) as debug_err:
                            # Защита от ошибок в debug-логике - не валим миграцию
                            logger.debug(f"Debug preview failed (non-critical): {debug_err}", exc_info=False)
                    
                        for cf_idx, cf in enumerate(custom_fields):
                                if not isinstance(cf, dict):
                                    if debug_count_for_extraction < 3:
                                        logger.debug(f"  - [field {cf_idx}] Skipped: not a dict, type={type(cf)}")
                                    continue
                                field_id = int(cf.get("field_id") or 0)
                                # ВАЖНО: в amoCRM используется field_code (не code) и field_name (не name)
                                field_code = str(cf.get("field_code") or "").upper()  # PHONE, EMAIL в верхнем регистре
                                field_name = str(cf.get("field_name") or "").lower()  # "телефон", "должность"
                                field_type = str(cf.get("field_type") or "").lower()  # "multitext", "text", "date"
                                values = cf.get("values") or []
                                if not isinstance(values, list):
                                    if debug_count_for_extraction < 3:
                                        logger.debug(f"  - [field {cf_idx}] Skipped: values not a list, type={type(values)}")
                                    continue
                        
                                if debug_count_for_extraction < 3:
                                    logger.debug(f"  - [field {cf_idx}] field_id={field_id}, field_code={field_code}, field_name={field_name}, field_type={field_type}, values_count={len(values)}")
                        
                                for v_idx, v in enumerate(values):
                                    # Согласно документации AmoCRM API v4:
                                    # Значение может быть dict с полями: value, enum_id, enum_code
                                    # Также может быть поле "enum" (строка) для обратной совместимости
                                    if isinstance(v, dict):
                                        # value может быть строкой, числом или объектом (для сложных типов)
                                        value_raw = v.get("value")
                                        if value_raw is None:
                                            continue
                                        # Преобразуем value в строку (для телефонов/email это всегда строка)
                                        if isinstance(value_raw, (str, int, float)):
                                            val = str(value_raw).strip()
                                        elif isinstance(value_raw, dict):
                                            # Для сложных типов (например, связь с другими сущностями)
                                            # Пытаемся извлечь текстовое представление
                                            val = str(value_raw.get("value") or value_raw.get("name") or str(value_raw)).strip()
                                        else:
                                            val = str(value_raw).strip()
                                
                                        # enum_id - числовой идентификатор enum
                                        enum_id = v.get("enum_id")
                                
                                        # enum_code - строковый код enum (WORK, MOBILE и т.д.)
                                        # Также проверяем поле "enum" для обратной совместимости
                                        enum_code = v.get("enum_code") or v.get("enum")
                                        if enum_code and not isinstance(enum_code, str):
                                            enum_code = str(enum_code)
                                    elif isinstance(v, str):
                                        val = v.strip()
                                        enum_id = None
                                        enum_code = None
                                    else:
                                        val = str(v).strip() if v else ""
                                        enum_id = None
                                        enum_code = None
                            
                                    if not val:
                                        continue
                            
                                    # Телефоны: проверяем field_code="PHONE" или field_name содержит "телефон"
                                    # В amoCRM field_type для телефонов обычно "multitext"
                                    is_phone = (field_code == "PHONE" or 
                                               "телефон" in field_name)
                                    # Email: проверяем field_code="EMAIL" или field_name содержит "email"/"почта"
                                    is_email = (field_code == "EMAIL" or
                                               "email" in field_name or "почта" in field_name or "e-mail" in field_name)
                                    # Должность: проверяем field_code="POSITION" или field_name содержит "должность"/"позиция"
                                    is_position = (field_code == "POSITION" or
                                                  "должность" in field_name or "позиция" in field_name)
                                    # Холодный звонок: проверяем field_id=448321 (из примера), field_name и field_type="date"
                                    is_cold_call_date = (
                                        field_id == 448321 or  # Известный ID поля "Холодный звонок" из примера
                                        (field_type == "date" and ("холодный" in field_name and "звонок" in field_name))
                                    )
                                    # День рождения: проверяем field_type="birthday" или field_name содержит "день рождения"/"birthday"
                                    is_birthday = (
                                        field_type == "birthday" or
                                        ("день" in field_name and "рождени" in field_name) or
                                        "birthday" in field_name.lower()
                                    )
                                    # Примечание/Комментарий (текстовое поле)
                                    # Проверяем field_id=366537 (из примера), field_name, и field_code для большей надежности
                                    is_note = (
                                        field_id == 366537 or  # Известный ID поля "Примечание" из примера
                                        any(k in field_name for k in ["примеч", "комментар", "коммент", "заметк"]) or
                                        any(k in str(field_code or "").upper() for k in ["NOTE", "COMMENT", "REMARK"])
                                    )
                            
                                    if debug_count_for_extraction < 3:
                                        logger.debug(f"    [value {v_idx}] val={val[:50]}, is_phone={is_phone}, is_email={is_email}, is_position={is_position}, is_cold_call_date={is_cold_call_date}, is_birthday={is_birthday}, is_note={is_note}")
                            
                                    if is_phone:
                                        # Определяем тип телефона через маппинг enum_code
                                        # Используем allowlist: WORK, MOB, HOME, OTHER
                                        original_enum_code = enum_code
                                        ptype = map_phone_enum_code(enum_code, field_name, res)
                                        
                                        # Сохраняем информацию о маппинге enum_code для dry-run
                                        if original_enum_code:
                                            enum_code_upper = str(original_enum_code).upper().strip()
                                            if enum_code_upper not in PHONE_ENUM_ALLOWLIST:
                                                enum_mapped_info[f"phone_{field_id}"] = {
                                                    "original_enum_code": original_enum_code,
                                                    "mapped_to": str(ptype),
                                                    "reason": "not_in_allowlist"
                                                }
                                                if debug_count_for_extraction < 3:
                                                    logger.debug(f"      -> enum_code '{original_enum_code}' не в allowlist, замаплен в {ptype}")
                                
                                        # Парсим значение: используем parse_phone_value для извлечения телефонов, extension и комментариев
                                        # parse_phone_value обрабатывает многострочные значения, extension и инструкции
                                        parsed = parse_phone_value(val)
                                        
                                        if parsed.phones:
                                            # Телефоны найдены - добавляем их
                                            for phone_e164 in parsed.phones:
                                                # Проверяем валидность еще раз
                                                if is_valid_phone(phone_e164):
                                                    # Формируем комментарий
                                                    phone_comment_parts = []
                                                    if parsed.extension:
                                                        phone_comment_parts.append(f"доб. {parsed.extension}")
                                                        res.phones_extracted_with_extension += 1
                                                    if parsed.comment:
                                                        phone_comment_parts.append(parsed.comment)
                                                    
                                                    phone_comment = "; ".join(phone_comment_parts) if phone_comment_parts else ""
                                                    
                                                    # Если комментарий пустой, используем enum_code как fallback
                                                    if not phone_comment and enum_code:
                                                        phone_comment = str(enum_code)
                                                    
                                                    phones.append((ptype, phone_e164, phone_comment[:255]))
                                                    if debug_count_for_extraction < 3:
                                                        logger.debug(f"      -> Added phone: {_mask_phone(phone_e164)} (type={ptype}, ext={parsed.extension}, comment={phone_comment[:50]})")
                                                else:
                                                    # Телефон не валиден - переносим в note
                                                    res.phones_rejected_invalid += 1
                                                    note_to_add = parsed.comment or f"Комментарий к телефону: {phone_e164}"
                                                    if not note_text:
                                                        note_text = note_to_add[:255]
                                                    elif note_to_add not in note_text:
                                                        combined = f"{note_text}; {note_to_add[:200]}"
                                                        note_text = combined[:255]
                                        
                                        # Если телефоны не найдены - весь текст в note
                                        if not parsed.phones:
                                            # НЕ записываем в PHONE, только в NOTE
                                            note_to_add = parsed.comment or f"Комментарий к телефону: {val}"
                                            
                                            # Увеличиваем счетчик метрики
                                            if parsed.rejected_reason and "instruction" in parsed.rejected_reason.lower():
                                                res.phones_rejected_as_note += 1
                                            else:
                                                res.phones_rejected_invalid += 1
                                            
                                            # Логируем причину пропуска
                                            if res.skip_reasons is None:
                                                res.skip_reasons = []
                                            res.skip_reasons.append({
                                                "type": "skip_PHONE" if parsed.rejected_reason else "move_PHONE_text_to_NOTE",
                                                "reason": parsed.rejected_reason or "no_valid_phone_found",
                                                "value": val[:100] if len(val) <= 100 else val[:50] + "...",
                                                "original_value": val[:100],
                                                "contact_id": amo_contact_id,
                                                "field_name": field_name,
                                            })
                                            
                                            if not note_text:
                                                note_text = note_to_add[:255]
                                            elif note_to_add not in note_text:
                                                combined = f"{note_text}; {note_to_add[:200]}"
                                                note_text = combined[:255]
                                            
                                            if debug_count_for_extraction < 3:
                                                logger.debug(f"      -> Skipped non-phone value as note: '{val[:50]}' (reason={parsed.rejected_reason})")
                                        
                                        # Обработка телефонов завершена через parse_phone_value
                                        continue
                                    elif is_email:
                                        # Определяем тип email:
                                        # 1. По enum_code (WORK/PRIV/...)
                                        # 2. По названию поля (если содержит "раб" - WORK, "личн" - PERSONAL)
                                        t = str(enum_code or "").upper()
                                        field_name_lower = field_name.lower()
                                
                                        if t in ("WORK",) or "раб" in field_name_lower:
                                            etype = ContactEmail.EmailType.WORK
                                        elif t in ("PRIV", "PERSONAL", "HOME") or "личн" in field_name_lower or "персон" in field_name_lower:
                                            etype = ContactEmail.EmailType.PERSONAL
                                        else:
                                            etype = ContactEmail.EmailType.OTHER
                                
                                        # Email обычно в одной строке, но может быть несколько через запятую
                                        # ВАЖНО: валидируем email перед добавлением
                                        for ev in _split_multi(val):
                                            ev_clean = ev.strip() if ev else ""
                                            if ev_clean and validate_email(ev_clean):
                                                emails.append((etype, ev_clean))
                                                if debug_count_for_extraction < 3:
                                                    logger.debug(f"      -> Added email: {ev_clean} (type={etype})")
                                            elif ev_clean and "@" in ev_clean:
                                                # Email не прошел валидацию - логируем и увеличиваем счетчик
                                                res.emails_rejected_invalid_format += 1
                                                if debug_count_for_extraction < 3:
                                                    logger.debug(f"      -> Rejected invalid email format: {ev_clean[:50]}")
                                    elif is_position:
                                        # СТРОГАЯ защита POSITION от телефонов
                                        # Извлекаем телефон из POSITION ТОЛЬКО если POSITION = "100% телефон" (без текста)
                                        if position_is_only_phone(val):
                                            # Увеличиваем счетчик метрики
                                            res.position_phone_detected += 1
                                            res.position_rejected_as_phone += 1  # Для обратной совместимости
                                            # Логируем причину пропуска
                                            if res.skip_reasons is None:
                                                res.skip_reasons = []
                                            res.skip_reasons.append({
                                                "type": "skip_POSITION",
                                                "reason": "looks like phone",
                                                "value": _mask_phone(val) if len(val) > 10 else val[:50],
                                                "contact_id": amo_contact_id,
                                                "field_name": field_name,
                                                "source": "POSITION",
                                            })
                                            # Если POSITION = 100% телефон - извлекаем телефон
                                            # ВАЖНО: НЕ очищаем POSITION автоматически, только логируем предупреждение
                                            normalized = normalize_phone(val)
                                            # СТРОГАЯ ПРОВЕРКА: только валидные телефоны попадают в PHONE
                                            if normalized.isValid and normalized.phone_e164 and is_valid_phone(normalized.phone_e164):
                                                # Переносим в телефоны с пометкой source=POSITION
                                                # Используем ТОЛЬКО нормализованный номер (phone_e164)
                                                phone_value = normalized.phone_e164
                                                comment_parts = ["из поля должности (POSITION)"]
                                                if normalized.ext:
                                                    comment_parts.append(f"доб. {normalized.ext}")
                                                    res.phones_extracted_with_extension += 1
                                                if normalized.note:
                                                    comment_parts.append(normalized.note)
                                                comment = "; ".join(comment_parts)
                                                phones.append((ContactPhone.PhoneType.OTHER, phone_value, comment[:255]))
                                                
                                                # ВАЖНО: НЕ очищаем POSITION автоматически
                                                # Логируем предупреждение и добавляем в note "Телефон был в поле Должность"
                                                if not position:
                                                    position = val  # Оставляем исходное значение (может быть пустым, если это был только телефон)
                                                
                                                # Добавляем в note информацию о том, что телефон был в POSITION
                                                note_about_position = "Телефон был в поле Должность"
                                                if not note_text:
                                                    note_text = note_about_position[:255]
                                                elif note_about_position not in note_text:
                                                    combined = f"{note_text}; {note_about_position}"
                                                    note_text = combined[:255]
                                                
                                                # Логируем извлечение телефона из POSITION
                                                if res.skip_reasons is None:
                                                    res.skip_reasons = []
                                                res.skip_reasons.append({
                                                    "type": "extract_phone_from_POSITION",
                                                    "reason": "phone extracted from position field (position not cleared)",
                                                    "phone": _mask_phone(phone_value),
                                                    "original_position": val[:50],
                                                    "position_kept": val[:50],
                                                    "contact_id": amo_contact_id,
                                                    "source": "POSITION",
                                                })
                                                
                                                if debug_count_for_extraction < 3:
                                                    logger.debug(f"      -> Position '{val}' recognized as phone, extracted '{phone_value}', position kept as '{val[:50]}'")
                                            else:
                                                # Если не валидный телефон - НЕ трогаем POSITION, но логируем
                                                if not position:
                                                    position = val  # Оставляем исходное значение
                                                if debug_count_for_extraction < 3:
                                                    logger.debug(f"      -> Position '{val}' not valid phone, keeping original position")
                                        else:
                                            # Нормальная должность - устанавливаем, если еще не установлена
                                            if not position:
                                                position = val
                                                if debug_count_for_extraction < 3:
                                                    logger.debug(f"      -> Set position: {val}")
                                    elif is_note:
                                        # ВАЖНО: примечание из custom_fields имеет ПРИОРИТЕТ над заметками
                                        # Если уже есть note_text из заметок - проверяем, не служебная ли это заметка
                                        is_current_note_service = (
                                            not note_text or 
                                            "call_" in str(note_text).lower() or 
                                            note_text.lower() in ["call_out", "call_in", "call", "amomail", "sms", "task"] or
                                            len(str(note_text).strip()) < 10  # Очень короткие значения тоже подозрительны
                                        )
                                
                                        if is_current_note_service or not note_text:
                                            # Заменяем служебные заметки на реальное примечание из custom_fields
                                            note_text = val[:255]
                                            if debug_count_for_extraction < 3:
                                                logger.debug(f"      -> Found note_text in custom_field (field_name='{field_name}', field_code='{field_code}'): {note_text[:100]}")
                                                if is_current_note_service:
                                                    logger.debug(f"      -> Replaced service note '{note_text[:50]}' with real note from custom_field")
                                        else:
                                            # Если уже есть нормальное примечание, добавляем через точку с запятой
                                            combined = f"{note_text}; {val[:100]}"
                                            note_text = combined[:255]
                                            if debug_count_for_extraction < 3:
                                                logger.debug(f"      -> Appended note_text from custom_field: {val[:100]}")
                                    elif is_cold_call_date:
                                        # Холодный звонок: val может быть timestamp (Unix timestamp) или числом
                                        # Сохраняем для последующей обработки (берем первое значение, если их несколько)
                                        if cold_call_timestamp is None:
                                            try:
                                                # Если val - это строка, пытаемся преобразовать в число
                                                if isinstance(val, str):
                                                    cold_call_timestamp = int(float(val))
                                                else:
                                                    cold_call_timestamp = int(float(val))
                                                # Будем использовать это значение при создании/обновлении контакта
                                                if debug_count_for_extraction < 3:
                                                    logger.debug(f"      -> Found cold call date: {cold_call_timestamp} (from field_id={field_id})")
                                            except (ValueError, TypeError):
                                                if debug_count_for_extraction < 3:
                                                    logger.debug(f"      -> Invalid cold call timestamp: {val}")
                                                cold_call_timestamp = None
                                    elif is_birthday:
                                        # День рождения: val может быть timestamp (Unix timestamp) или числом
                                        # Сохраняем для последующей обработки (берем первое значение, если их несколько)
                                        if birthday_timestamp is None:
                                            try:
                                                # Если val - это строка, пытаемся преобразовать в число
                                                if isinstance(val, str):
                                                    birthday_timestamp = int(float(val))
                                                else:
                                                    birthday_timestamp = int(float(val))
                                                # Сохраняем в raw_fields (пока нет поля в модели)
                                                if debug_count_for_extraction < 3:
                                                    logger.debug(f"      -> Found birthday: {birthday_timestamp} (from field_id={field_id})")
                                            except (ValueError, TypeError):
                                                if debug_count_for_extraction < 3:
                                                    logger.debug(f"      -> Invalid birthday timestamp: {val}")
                                                birthday_timestamp = None
                    
                        # Убираем дубликаты
                        # Дедуп по нормализованному виду (сравниваем E.164 номера)
                        dedup_phones: list[tuple[str, str, str]] = []
                        seen_p = set()  # Множество нормализованных номеров
                        seen_p_raw = set()  # Множество исходных номеров (для логирования)
                        for pt, pv, pc in phones:
                            pv2 = str(pv or "").strip()
                            if not pv2:
                                continue
                            
                            # Нормализуем для сравнения
                            normalized_for_dedup = normalize_phone(pv2)
                            if normalized_for_dedup.isValid and normalized_for_dedup.phone_e164:
                                phone_key = normalized_for_dedup.phone_e164
                            else:
                                phone_key = pv2.lower()
                            
                            if phone_key in seen_p:
                                # Логируем причину дедупа
                                if res.skip_reasons is None:
                                    res.skip_reasons = []
                                res.skip_reasons.append({
                                    "type": "dedup_PHONE",
                                    "reason": "already exists",
                                    "value": _mask_phone(pv2) if len(pv2) > 10 else pv2[:50],
                                    "contact_id": amo_contact_id,
                                    "normalized": phone_key if normalized_for_dedup.isValid else None,
                                })
                                continue
                            seen_p.add(phone_key)
                            seen_p_raw.add(pv2)
                            dedup_phones.append((pt, pv2, str(pc or "")))
                        phones = dedup_phones

                        # Если есть одно общее примечание, а номеров несколько — пишем его в comment первого номера
                        # ИСПРАВЛЕНИЕ: всегда добавляем примечание в comment первого телефона (объединяем с существующим, если есть)
                        if note_text and phones:
                            pt0, pv0, pc0 = phones[0]
                            existing_comment = str(pc0 or "").strip()
                            if existing_comment:
                                # Если уже есть комментарий, объединяем через точку с запятой
                                combined_comment = f"{existing_comment}; {note_text[:200]}"
                                phones[0] = (pt0, pv0, combined_comment[:255])
                                if debug_count_for_extraction < 3:
                                    logger.debug(f"  -> Merged note_text with existing comment in first phone: {combined_comment[:100]}")
                            else:
                                # Если комментария нет, просто добавляем примечание
                                phones[0] = (pt0, pv0, note_text[:255])
                                if debug_count_for_extraction < 3:
                                    logger.debug(f"  -> Applied note_text to first phone: {note_text[:100]}")
                        elif debug_count_for_extraction < 3 and not note_text:
                            logger.debug(f"  -> ⚠️ No note_text found for contact {amo_contact_id} (checked direct fields and custom_fields)")

                        dedup_emails: list[tuple[str, str]] = []
                        seen_e = set()
                        for et, ev in emails:
                            ev2 = str(ev or "").strip().lower()
                            if not ev2:
                                continue
                            if ev2 in seen_e:
                                continue
                            seen_e.add(ev2)
                            dedup_emails.append((et, ev2))
                        emails = dedup_emails
                    
                        # ОТЛАДКА: сохраняем сырые данные для анализа
                        # Собираем информацию о том, где искали примечания
                        note_search_info = []
                        if isinstance(ac, dict):
                            # Проверяем прямые поля
                            for note_key in ["note", "notes", "comment", "comments", "remark", "remarks"]:
                                if note_key in ac:
                                    note_search_info.append(f"direct:{note_key}={bool(ac.get(note_key))}")
                            # Проверяем _embedded
                            if "_embedded" in ac:
                                embedded = ac.get("_embedded") or {}
                                if isinstance(embedded, dict) and "notes" in embedded:
                                    notes_list = embedded.get("notes") or []
                                    notes_count = len(notes_list) if isinstance(notes_list, list) else 0
                                    if notes_count > 0:
                                        note_search_info.append(f"_embedded.notes={notes_count}")
                                        # Показываем типы заметок для отладки
                                        note_types = []
                                        for note_item in notes_list[:3]:  # первые 3
                                            if isinstance(note_item, dict):
                                                note_type = str(note_item.get("note_type") or "").strip()
                                                if note_type:
                                                    note_types.append(note_type)
                                        if note_types:
                                            note_search_info.append(f"note_types:{','.join(note_types)}")
                                        # Показываем, есть ли текст в заметках
                                        has_text = False
                                        for note_item in notes_list[:3]:
                                            if isinstance(note_item, dict):
                                                if note_item.get("text") or note_item.get("params", {}).get("text"):
                                                    has_text = True
                                                    break
                                        if has_text:
                                            note_search_info.append("notes_has_text=True")
                                        else:
                                            note_search_info.append("notes_has_text=False")
                            # Проверяем custom_fields на наличие полей с примечаниями
                            note_fields_in_custom = []
                            all_custom_field_names = []  # Для отладки - показываем ВСЕ поля
                            all_custom_fields_with_values = []  # Для отладки - показываем ВСЕ поля с их значениями
                            for cf in custom_fields:
                                if isinstance(cf, dict):
                                    field_id = cf.get("field_id")  # ВАЖНО: field_id может быть числом (366537)
                                    field_name = str(cf.get("field_name") or "").strip()
                                    field_code_raw = cf.get("field_code")
                                    # Безопасное преобразование field_code - может быть None, строкой или другим типом
                                    if field_code_raw is None:
                                        field_code = ""
                                    else:
                                        field_code = str(field_code_raw).strip()
                                    field_name_lower = field_name.lower() if field_name else ""
                                    field_code_upper = field_code.upper() if field_code else ""
                            
                                    # Сохраняем все поля для отладки (включая field_id)
                                    all_custom_field_names.append(f"id={field_id} name={field_name} code={field_code}")
                            
                                    # Сохраняем все поля с их значениями для отладки
                                    values = cf.get("values") or []
                                    if values and isinstance(values, list) and len(values) > 0:
                                        first_val = values[0]
                                        if isinstance(first_val, dict):
                                            val_text = str(first_val.get("value", ""))[:100]
                                        else:
                                            val_text = str(first_val)[:100]
                                        if val_text:
                                            all_custom_fields_with_values.append(f"id={field_id} name={field_name} code={field_code} value={val_text[:50]}")
                            
                                    # Проверяем на примечания (расширенный список ключевых слов)
                                    # Также проверяем field_id - возможно, примечание имеет конкретный ID (например, 366537)
                                    is_note_field = (
                                        any(k in field_name_lower for k in ["примеч", "комментар", "коммент", "заметк", "note", "comment", "remark"]) or
                                        any(k in field_code_upper for k in ["NOTE", "COMMENT", "REMARK", "NOTE_TEXT", "COMMENT_TEXT"]) or
                                        (field_id and str(field_id) in ["366537"])  # Известные ID полей примечаний
                                    )
                            
                                    if is_note_field:
                                        note_fields_in_custom.append(f"id={field_id} name={field_name}({field_code})")
                                        # Логируем значение этого поля
                                        if values and isinstance(values, list) and len(values) > 0:
                                            first_val = values[0]
                                            if isinstance(first_val, dict):
                                                val_text = str(first_val.get("value", ""))[:100]
                                            else:
                                                val_text = str(first_val)[:100]
                                            if val_text:
                                                note_text = val_text[:255]  # Устанавливаем примечание!
                                                note_search_info.append(f"found_note_value:{val_text[:50]}")
                                                if debug_count_for_extraction < 3:
                                                    logger.debug(f"  -> ✅ Found note_text in custom_field id={field_id} name={field_name}: {note_text[:100]}")
                    
                            # Добавляем информацию о всех полях для отладки
                            if all_custom_field_names:
                                note_search_info.append(f"all_fields:{','.join(all_custom_field_names)}")
                            if note_fields_in_custom:
                                note_search_info.append(f"note_fields:{','.join(note_fields_in_custom)}")
                            elif debug_count_for_extraction < 3:
                                # Если не нашли поля с примечаниями, логируем все поля
                                logger.debug(f"  -> ⚠️ No note fields found in custom_fields. All fields: {all_custom_field_names}")
                    
                        # Обрабатываем данные о холодном звонке из amoCRM (ДО использования в contact_debug)
                        # Нормализуем timestamp: конвертируем в дату без времени в таймзоне проекта
                        # Используем Europe/Moscow как таймзону по умолчанию (можно настроить через settings)
                        cold_marked_at_dt = None
                        if cold_call_timestamp:
                            try:
                                # Используем UTC для конвертации, затем нормализуем на начало дня в нужной таймзоне
                                UTC = getattr(timezone, "UTC", dt_timezone.utc)
                                
                                # Конвертируем timestamp в datetime в UTC
                                dt_utc = timezone.datetime.fromtimestamp(cold_call_timestamp, tz=UTC)
                                
                                # Нормализуем на начало дня (00:00:00) в UTC
                                # Это гарантирует, что дата не сместится при конвертации в другую таймзону
                                cold_marked_at_dt = dt_utc.replace(hour=0, minute=0, second=0, microsecond=0)
                                
                                # Сохраняем информацию о конвертации для dry-run
                                if dry_run:
                                    cold_call_date_info = {
                                        "raw_epoch_seconds": cold_call_timestamp,
                                        "converted_date": cold_marked_at_dt.strftime("%Y-%m-%d"),
                                        "timezone": "UTC",
                                        "normalized_to": "00:00:00 UTC"
                                    }
                                
                                if debug_count_for_extraction < 3:
                                    logger.debug(f"      -> Cold call date: timestamp={cold_call_timestamp} -> {cold_marked_at_dt.isoformat()} (normalized to 00:00:00 UTC)")
                            except Exception as e:
                                cold_marked_at_dt = None
                                if dry_run:
                                    cold_call_date_info = {
                                        "raw_epoch_seconds": cold_call_timestamp,
                                        "error": str(e),
                                    }
                        else:
                            # Если нет cold_call_timestamp, очищаем info
                            if dry_run:
                                cold_call_date_info = {}
                    
                        debug_data = {
                            "source": "amo_api",
                            "amo_contact_id": amo_contact_id,
                            "first_name": first_name,
                            "last_name": last_name,
                            "extracted_phones": phones,
                            "extracted_emails": emails,
                            "extracted_position": position,
                            "extracted_note_text": note_text,  # Добавляем note_text для отладки
                            "extracted_cold_call_timestamp": cold_call_timestamp,  # Timestamp холодного звонка
                            "extracted_birthday_timestamp": birthday_timestamp,  # Timestamp дня рождения
                            "note_search_info": note_search_info,  # Где искали примечания
                            "custom_fields_count": len(custom_fields),
                            "custom_fields_sample": custom_fields if dry_run else (custom_fields[:3] if custom_fields else []),  # В dry-run показываем все поля
                            "has_phone_field": bool(ac.get("phone")),
                            "has_email_field": bool(ac.get("email")),
                        }
                    
                        # ПОЛНЫЙ АНАЛИЗ КОНТАКТА для dry-run
                        # Используем новую функцию для извлечения ВСЕХ полей
                        debug_count = getattr(res, '_debug_contacts_logged', 0)
                        if res.contacts_preview is None:
                            res.contacts_preview = []
                    
                        # В dry-run показываем ВСЕ контакты (до 1000), чтобы видеть все проблемы
                        preview_limit = 1000 if dry_run else 10
                        logger.info(f"migrate_filtered: обработка контакта {amo_contact_id}: debug_count={debug_count}, preview_limit={preview_limit}, local_company={'найдена' if local_company else 'не найдена'}")
                        if debug_count < preview_limit:
                            # Полный анализ контакта
                            full_analysis = _analyze_contact_completely(ac)
                    
                            # Формируем понятный отчет для dry-run
                            contact_debug = {
                                "status": "UPDATED" if existing_contact else "CREATED",
                                "amo_contact_id": amo_contact_id,
                                "company_name": local_company.name if local_company else None,
                                "company_id": local_company.id if local_company else None,
                        
                                # Стандартные поля
                                "standard_fields": full_analysis.get("standard_fields", {}),
                                "first_name": first_name,
                                "last_name": last_name,
                        
                                # Извлеченные данные (что будет импортировано)
                                "extracted_phones": [
                                    {
                                        "value": p[1],
                                        "type": str(p[0]),
                                        "comment": p[2],
                                    }
                                    for p in phones
                                ],
                                "extracted_emails": [
                                    {
                                        "value": e[1],
                                        "type": str(e[0]),
                                    }
                                    for e in emails
                                ],
                                "extracted_position": position,
                                "extracted_note_text": note_text,
                                "extracted_cold_call": cold_marked_at_dt.isoformat() if cold_marked_at_dt else None,
                                "extracted_birthday": birthday_timestamp,  # Timestamp дня рождения (если есть)
                        
                                # ВСЕ кастомные поля (полная информация)
                                "all_custom_fields": [
                                    {
                                        "field_id": cf.get("field_id"),
                                        "field_name": cf.get("field_name"),
                                        "field_code": cf.get("field_code"),
                                        "field_type": cf.get("field_type"),
                                        "values_count": cf.get("values_count", 0),
                                        "values": [
                                            {
                                                "value": str(v.get("value", "")),
                                                "enum_code": v.get("enum_code"),
                                                "enum_id": v.get("enum_id"),
                                                "enum": v.get("enum"),
                                            }
                                            for v in cf.get("values", [])
                                        ],
                                        "is_used": (
                                            (str(cf.get("field_code") or "").upper() in ["PHONE", "EMAIL", "POSITION"]) or
                                            any(k in (str(cf.get("field_name") or "").lower()) for k in ["телефон", "почта", "email", "должность", "позиция", "примеч", "комментар", "холодный"])
                                        ),
                                    }
                                    for cf in full_analysis.get("custom_fields", [])
                                ],
                                "custom_fields_count": len(full_analysis.get("custom_fields", [])),
                        
                                # Вложенные данные (_embedded)
                                "embedded_tags": full_analysis.get("embedded_data", {}).get("tags", []),
                                "embedded_companies": full_analysis.get("embedded_data", {}).get("companies", []),
                                "embedded_leads": full_analysis.get("embedded_data", {}).get("leads", []),
                                "embedded_customers": full_analysis.get("embedded_data", {}).get("customers", []),
                                "embedded_notes": full_analysis.get("embedded_data", {}).get("notes", []),
                                "embedded_notes_count": len(full_analysis.get("embedded_data", {}).get("notes", [])),
                        
                                # Метаинформация
                                "all_contact_keys": full_analysis.get("all_keys", []),
                                "note_search_info": note_search_info,
                        
                                # Полная структура для первых 3 контактов (для глубокой отладки)
                                "full_structure": None,
                            }
                    
                            # Сохраняем полную структуру для первых 3 контактов
                            preview_count = len(res.contacts_preview) if res.contacts_preview else 0
                            if preview_count < 3 and isinstance(ac, dict):
                                import json
                                try:
                                    # Сохраняем полную структуру (ограничиваем размер для UI)
                                    contact_debug["full_structure"] = json.dumps(ac, ensure_ascii=False, indent=2)[:5000]
                                except Exception as e:
                                    contact_debug["full_structure"] = f"JSON error: {e}\n{str(ac)[:2000]}"
                    
                            res.contacts_preview.append(contact_debug)
                            res._debug_contacts_logged = debug_count + 1
                            logger.info(f"migrate_filtered: ✅ контакт {amo_contact_id} добавлен в preview (всего в preview: {len(res.contacts_preview)})")
                    
                            # ОТЛАДКА: логируем, что добавили в preview
                            if preview_count < 3:
                                logger.debug(f"Added contact {amo_contact_id} to preview (count: {debug_count + 1}):")
                                logger.debug(f"  - phones_found: {phones}")
                                logger.debug(f"  - emails_found: {emails}")
                                logger.debug(f"  - position_found: {position}")
                                logger.debug(f"  - note_text_found: {note_text}")
                                logger.debug(f"  - custom_fields_count: {len(full_analysis.get('custom_fields', []))}")
                                logger.debug(f"  - all_custom_fields: {len(contact_debug.get('all_custom_fields', []))}")
                        else:
                            logger.info(f"migrate_filtered: ⚠️ контакт {amo_contact_id} НЕ добавлен в preview (превышен лимит: {debug_count} >= {preview_limit})")
                    
                            # Также логируем в консоль для первых контактов
                            if contacts_processed <= 3:
                                logger.debug(f"Contact {amo_contact_id}:")
                                logger.debug(f"  - first_name: {first_name}")
                                logger.debug(f"  - last_name: {last_name}")
                                logger.debug(f"  - phones found: {phones}")
                                logger.debug(f"  - emails found: {emails}")
                                logger.debug(f"  - position found: {position}")
                                logger.debug(f"  - note_text found: {note_text}")
                                logger.debug(f"  - custom_fields_values count: {len(custom_fields)}")
                                if custom_fields:
                                    logger.debug(f"  - custom_fields sample (first 3):")
                                    for idx, cf in enumerate(custom_fields[:3]):
                                        logger.debug(f"    [{idx}] field_id={cf.get('field_id')}, code={cf.get('code')}, name={cf.get('name')}, type={cf.get('type')}, values={cf.get('values')}")
                                else:
                                    logger.debug(f"  - ⚠️ custom_fields_values пуст или отсутствует")
                                logger.debug(f"  - raw contact top-level keys: {list(ac.keys())[:15] if isinstance(ac, dict) else 'not_dict'}")
                                logger.debug(f"  - has phone field: {bool(ac.get('phone')) if isinstance(ac, dict) else False}")
                                logger.debug(f"  - has email field: {bool(ac.get('email')) if isinstance(ac, dict) else False}")
                    
                    except Exception as e:
                        contacts_errors += 1
                        amo_contact_id_for_error = int(ac.get("id") or 0) if isinstance(ac, dict) else 0
                        import traceback
                        error_traceback = traceback.format_exc()
                        logger.error(f"migrate_filtered: ❌ ОШИБКА при обработке контакта {ac_idx + 1}/{len(full_contacts)} (amo_id: {amo_contact_id_for_error}): {e}", exc_info=True)
                        # Добавляем информацию об ошибке в preview
                        if res.contacts_preview is None:
                            res.contacts_preview = []
                        if len(res.contacts_preview) < 100:  # Ограничиваем количество ошибок в preview
                            # Пытаемся извлечь базовую информацию о контакте для отображения
                            contact_name_error = ""
                            if isinstance(ac, dict):
                                name_str = str(ac.get("name") or "").strip()
                                first_name_str = str(ac.get("first_name") or "").strip()
                                last_name_str = str(ac.get("last_name") or "").strip()
                                if name_str:
                                    contact_name_error = name_str
                                elif first_name_str or last_name_str:
                                    contact_name_error = f"{last_name_str} {first_name_str}".strip()
                            
                            res.contacts_preview.append({
                                "status": "ERROR",
                                "amo_contact_id": amo_contact_id_for_error,
                                "contact_name": contact_name_error,
                                "error": str(e),
                                "error_type": type(e).__name__,
                                "message": f"Ошибка при обработке контакта: {e}",
                                "traceback_short": error_traceback.split('\n')[-3:-1] if error_traceback else [],  # Последние 2 строки трейсбека
                            })
                        continue
                    
                    # Обновляем или создаём контакт
                    # DRY-RUN: собираем понятный diff "что будет обновлено" по контакту (поля + что добавится в телефоны/почты)
                    if dry_run:
                        if res.contacts_updates_preview is None:
                            res.contacts_updates_preview = []

                        planned_field_changes: dict[str, dict[str, str]] = {}
                        planned_phones_add: list[dict[str, str]] = []
                        planned_phones_skipped_invalid: list[dict[str, str]] = []  # Невалидные телефоны
                        planned_phones_deduped: list[dict[str, str]] = []  # Дубликаты
                        planned_emails_add: list[dict[str, str]] = []
                        planned_notes_appended: list[str] = []  # Добавленные примечания
                        name_cleaned_info: dict[str, str] = {}  # Информация об очистке имени
                        enum_mapped_info: dict[str, dict[str, str]] = {}  # Информация о маппинге enum_code
                        cold_call_date_info: dict[str, Any] = {}  # Информация о дате холодного звонка

                        # Снимок текущих данных контакта (если он уже есть в CRM)
                        old_position = ""
                        old_is_cold_call = False
                        old_phones: list[dict[str, str]] = []
                        old_emails: list[str] = []
                        if existing_contact:
                            old_position = str(existing_contact.position or "")
                            old_is_cold_call = bool(getattr(existing_contact, "is_cold_call", False))
                            try:
                                old_phones = [
                                    {"value": p.value, "type": str(p.type), "comment": str(p.comment or "")}
                                    for p in existing_contact.phones.all()
                                ]
                            except Exception:
                                old_phones = []
                            try:
                                old_emails = [str(e.value or "") for e in existing_contact.emails.all()]
                            except Exception:
                                old_emails = []

                        # Позиция: показываем только если "мягкий режим" позволил бы обновить
                        if existing_contact:
                            try:
                                crf_preview = dict(existing_contact.raw_fields or {})
                            except Exception:
                                crf_preview = {}
                            cprev_preview = crf_preview.get("amo_values") or {}
                            if not isinstance(cprev_preview, dict):
                                cprev_preview = {}

                            def _c_can_update_preview(field: str) -> bool:
                                cur = getattr(existing_contact, field)
                                if cur in ("", None):
                                    return True
                                if field in cprev_preview and cprev_preview.get(field) == cur:
                                    return True
                                return False

                            if position and _c_can_update_preview("position") and (existing_contact.position or "") != position[:255]:
                                planned_field_changes["position"] = {"old": old_position, "new": position[:255]}
                        else:
                            if position:
                                planned_field_changes["position"] = {"old": "", "new": position[:255]}

                        # Холодный звонок
                        if cold_marked_at_dt:
                            planned_field_changes["cold_call"] = {
                                "old": "Да" if old_is_cold_call else "Нет",
                                "new": "Да",
                            }

                        # Телефоны/почты: покажем только добавления (мы не удаляем/не затираем)
                        # Также показываем пропущенные невалидные и дедуплицированные
                        old_phone_values = set([p.get("value") for p in (old_phones or []) if p.get("value")])
                        old_phone_values_normalized = set()  # Нормализованные номера для дедупликации
                        for p in (old_phones or []):
                            pv_old = p.get("value")
                            if pv_old:
                                normalized_old = normalize_phone(pv_old)
                                if normalized_old.isValid and normalized_old.phone_e164:
                                    old_phone_values_normalized.add(normalized_old.phone_e164)
                        
                        phones_added_count = 0
                        phones_skipped_count = 0
                        phones_deduped_count = 0
                        
                        for pt, pv, pc in phones:
                            pv_db = str(pv).strip()[:50]
                            if not pv_db:
                                continue
                            
                            # Проверяем дедупликацию по нормализованному виду
                            normalized_for_dedup = normalize_phone(pv_db)
                            if normalized_for_dedup.isValid and normalized_for_dedup.phone_e164:
                                phone_key = normalized_for_dedup.phone_e164
                            else:
                                phone_key = pv_db.lower()
                            
                            # Если уже есть такой номер (по нормализованному виду) - это дубликат
                            if phone_key in old_phone_values_normalized or pv_db in old_phone_values:
                                phones_deduped_count += 1
                                if dry_run:
                                    planned_phones_deduped.append({
                                        "value": _mask_phone(pv_db) if len(pv_db) > 10 else pv_db[:50],
                                        "normalized": phone_key if normalized_for_dedup.isValid else None,
                                        "reason": "already_exists"
                                    })
                                continue
                            
                            # Добавляем новый телефон
                            phones_added_count += 1
                            if dry_run:
                                planned_phones_add.append(
                                    {
                                        "value": _mask_phone(pv_db) if len(pv_db) > 10 else pv_db[:50],
                                        "type": str(pt),
                                        "comment": str(pc or "")[:255],
                                    }
                                )
                            old_phone_values.add(pv_db)
                            if normalized_for_dedup.isValid and normalized_for_dedup.phone_e164:
                                old_phone_values_normalized.add(phone_key)

                        old_email_values = set([str(e or "").strip().lower() for e in (old_emails or []) if e])
                        for et, ev in emails:
                            ev_db = str(ev).strip()[:254].lower()
                            if ev_db and ev_db not in old_email_values:
                                planned_emails_add.append({"value": ev_db, "type": str(et)})

                        # Комментарий к первому телефону, если note_text
                        if note_text and phones:
                            first_phone_val = str(phones[0][1]).strip()[:50]
                            first_phone_comment_from_phones = str(phones[0][2] or "").strip()
                            if first_phone_val:
                                existing_first = None
                                for p in (old_phones or []):
                                    if p.get("value") == first_phone_val:
                                        existing_first = p
                                        break
                                
                                # Если телефон существует и у него пустой комментарий, показываем обновление
                                if existing_first and not (existing_first.get("comment") or "").strip():
                                    planned_field_changes["first_phone_comment"] = {"old": "", "new": note_text[:255]}
                                # Если телефон новый и у него есть комментарий из note_text, он уже будет в planned_phones_add
                                # Но для ясности также показываем отдельно, если note_text не пустой
                                elif not existing_first and first_phone_comment_from_phones:
                                    # Комментарий уже будет в planned_phones_add, но для наглядности можно добавить отдельное поле
                                    # Проверяем, что комментарий действительно из note_text (не из enum_code)
                                    if first_phone_comment_from_phones == note_text[:255]:
                                        # Это уже будет видно в planned_phones_add, но можно добавить отдельное поле для ясности
                                        pass

                        # Используем полный анализ для формирования информации о кастомных полях
                        full_analysis = _analyze_contact_completely(ac)
                        all_custom_fields_info = []
                        for cf in full_analysis.get("custom_fields", []):
                            field_id = cf.get("field_id")
                            field_code_raw = cf.get("field_code")
                            field_name_raw = cf.get("field_name")
                            field_type = cf.get("field_type")
                            
                            # Безопасное преобразование в строки
                            field_code = str(field_code_raw) if field_code_raw is not None else ""
                            field_name = str(field_name_raw) if field_name_raw is not None else ""
                            
                            # Собираем все значения в читаемом виде
                            field_values = []
                            for val_info in cf.get("values", []):
                                val_str = str(val_info.get("value", ""))
                                enum_code = val_info.get("enum_code") or val_info.get("enum")
                                if val_str:
                                    if enum_code:
                                        field_values.append(f"{val_str} ({enum_code})")
                                    else:
                                        field_values.append(val_str)
                            
                            # Определяем, было ли поле использовано (извлечено)
                            is_used = False
                            usage_info = []
                            field_code_upper = field_code.upper() if field_code else ""
                            field_name_lower = field_name.lower() if field_name else ""
                            
                            if field_code_upper == "PHONE" or "телефон" in field_name_lower:
                                is_used = True
                                usage_info.append("Телефон")
                            elif field_code_upper == "EMAIL" or "email" in field_name_lower or "почта" in field_name_lower:
                                is_used = True
                                usage_info.append("Email")
                            elif field_code_upper == "POSITION" or "должность" in field_name_lower or "позиция" in field_name_lower:
                                is_used = True
                                usage_info.append("Должность")
                            elif any(k in field_name_lower for k in ["примеч", "комментар", "коммент", "заметк"]):
                                is_used = True
                                usage_info.append("Примечание")
                            elif field_type == "date" and "холодный" in field_name_lower and "звонок" in field_name_lower:
                                is_used = True
                                usage_info.append("Холодный звонок")
                            
                            all_custom_fields_info.append({
                                "field_id": field_id,
                                "code": field_code,
                                "name": field_name,
                                "type": field_type,
                                "values": field_values,
                                "values_count": cf.get("values_count", 0),
                                "is_used": is_used,
                                "usage_info": usage_info,
                            })
                        
                        if planned_field_changes or planned_phones_add or planned_emails_add or all_custom_fields_info:
                            res.contacts_updates_preview.append(
                                {
                                    "company_name": local_company.name if local_company else "",
                                    "company_id": local_company.id if local_company else None,
                                    "contact_name": f"{last_name} {first_name}".strip() or "(без имени)",
                                    "amo_contact_id": amo_contact_id,
                                    "is_new": existing_contact is None,
                                    "field_changes": planned_field_changes,
                                    "phones_add": planned_phones_add,
                                    "emails_add": planned_emails_add,
                                    "all_custom_fields": all_custom_fields_info,  # Все найденные кастомные поля
                                }
                            )

                    # Обрабатываем данные о холодном звонке из amoCRM
                    cold_marked_at_dt = None
                    # Переменная cold_call_timestamp уже инициализирована ДО блока try
                    if cold_call_timestamp:
                        try:
                            UTC = getattr(timezone, "UTC", dt_timezone.utc)
                            cold_marked_at_dt = timezone.datetime.fromtimestamp(cold_call_timestamp, tz=UTC)
                        except Exception:
                            cold_marked_at_dt = None
                    
                    # Определяем, кто отметил холодный звонок (используем ответственного или создателя компании)
                    cold_marked_by_user = None
                    if local_company:
                        cold_marked_by_user = local_company.responsible or local_company.created_by or actor
                    else:
                        cold_marked_by_user = actor
                    
                    if existing_contact:
                        # ОБНОВЛЯЕМ существующий контакт с мягким обновлением
                        contact = existing_contact
                        
                        # Мягкий апдейт: не затираем данные, измененные вручную
                        try:
                            crf = dict(contact.raw_fields or {})
                        except Exception:
                            crf = {}
                        cprev = crf.get("amo_values") or {}
                        if not isinstance(cprev, dict):
                            cprev = {}

                        def c_can_update(field: str) -> bool:
                            """
                            Проверяет, можно ли обновить поле (мягкое обновление).
                            Поле можно обновить, если:
                            1. Оно пустое
                            2. Оно было импортировано из AmoCRM (есть в cprev и значение совпадает)
                            
                            ВАЖНО: защита от перезаписи непустых полей пустыми значениями.
                            """
                            cur = getattr(contact, field, None)
                            # Если поле пустое - можно обновить
                            if cur in ("", None):
                                return True
                            # Если поле было импортировано из AmoCRM - можно обновить
                            if field in cprev and cprev.get(field) == cur:
                                return True
                            # Иначе - не обновляем (защита от перезаписи)
                            return False

                        # Применяем изменения (мягкое обновление - не затираем непустые поля)
                        if first_name and c_can_update("first_name"):
                            contact.first_name = first_name[:120]
                        elif not first_name and c_can_update("first_name"):
                            # Если новое значение пустое, но поле можно обновить - не затираем
                            res.fields_skipped_to_prevent_blank_overwrite += 1
                        
                        if last_name and c_can_update("last_name"):
                            contact.last_name = last_name[:120]
                        elif not last_name and c_can_update("last_name"):
                            res.fields_skipped_to_prevent_blank_overwrite += 1
                        
                        # Обновляем должность только если можно и новое значение непустое
                        if position and c_can_update("position"):
                            contact.position = position[:255]
                        elif not position and c_can_update("position"):
                            # Если новое значение пустое - не затираем существующую должность
                            res.fields_skipped_to_prevent_blank_overwrite += 1
                        
                        # Обновляем примечание только если можно и новое значение непустое
                        # Примечание объединяем, а не заменяем (если уже есть)
                        if note_text and c_can_update("note"):
                            existing_note = contact.note or ""
                            if existing_note and note_text not in existing_note:
                                # Объединяем примечания без дублирования
                                combined_note = f"{existing_note}; {note_text}".strip("; ")
                                contact.note = combined_note[:8000]
                            elif not existing_note:
                                contact.note = note_text[:8000]
                        elif not note_text and c_can_update("note"):
                            # Если новое значение пустое - не затираем существующее примечание
                            res.fields_skipped_to_prevent_blank_overwrite += 1
                        # Обновляем данные о холодном звонке из amoCRM
                        if cold_marked_at_dt:
                            contact.is_cold_call = True
                            contact.cold_marked_at = cold_marked_at_dt
                            contact.cold_marked_by = cold_marked_by_user
                            # cold_marked_call оставляем NULL, т.к. в amoCRM нет связи с CallRequest
                        # Обновляем raw_fields + снимок импортированных значений
                        crf.update(debug_data)
                        # Сохраняем день рождения в raw_fields (пока нет поля в модели)
                        if birthday_timestamp:
                            crf["birthday_timestamp"] = birthday_timestamp
                        # Сохраняем все custom_fields_values в raw_fields["amo"]
                        if "amo" not in crf:
                            crf["amo"] = {}
                        crf["amo"]["custom_fields_values"] = ac.get("custom_fields_values") or []
                        # Сохраняем снимок импортированных значений для мягкого обновления
                        cprev.update({
                            "first_name": contact.first_name,
                            "last_name": contact.last_name,
                            "position": contact.position,
                            "note": contact.note,
                        })
                        crf["amo_values"] = cprev
                        contact.raw_fields = crf
                        
                        # ОПТИМИЗАЦИЯ: проверяем, изменились ли данные контакта ДО применения изменений
                        # Сохраняем старые значения для сравнения
                        old_first_name = contact.first_name
                        old_last_name = contact.last_name
                        old_position = contact.position
                        old_note = contact.note or ""  # ВАЖНО: нормализуем None -> "" для корректного сравнения
                        old_is_cold_call = contact.is_cold_call
                        old_cold_marked_at = contact.cold_marked_at
                        old_raw_fields = dict(contact.raw_fields or {})
                        
                        # Применяем изменения
                        if first_name and c_can_update("first_name"):
                            contact.first_name = first_name[:120]
                        if last_name and c_can_update("last_name"):
                            contact.last_name = last_name[:120]
                        if position and c_can_update("position"):
                            contact.position = position[:255]
                        if note_text and c_can_update("note"):
                            contact.note = note_text[:8000]  # TextField может быть длинным
                        if cold_marked_at_dt:
                            contact.is_cold_call = True
                            contact.cold_marked_at = cold_marked_at_dt
                            contact.cold_marked_by = cold_marked_by_user
                        
                        # Проверяем, действительно ли что-то изменилось
                        # ВАЖНО: нормализуем None -> "" для корректного сравнения строк
                        new_note = contact.note or ""
                        contact_changed = (
                            contact.first_name != old_first_name or
                            contact.last_name != old_last_name or
                            contact.position != old_position or
                            new_note != old_note or
                            contact.is_cold_call != old_is_cold_call or
                            contact.cold_marked_at != old_cold_marked_at or
                            birthday_timestamp is not None  # raw_fields всегда обновляем
                        )
                        
                        if dry_run:
                            if existing_contact:
                                res.contacts_would_update += 1
                                res.skipped_writes_dry_run += 1
                                logger.debug(f"DRY-RUN: would update contact {amo_contact_id} for company {local_company.id if local_company else None}")
                            else:
                                res.contacts_would_create += 1
                                res.skipped_writes_dry_run += 1
                                logger.debug(f"DRY-RUN: would create contact {amo_contact_id} for company {local_company.id if local_company else None}")
                        else:
                            # ОПТИМИЗАЦИЯ: сохраняем контакт сразу, если изменился (для телефонов/email нужен сохраненный контакт)
                            # Если ничего не изменилось, пропускаем сохранение (ускоряет импорт при обновлении)
                            if contact_changed:
                                contact.save()
                                contacts_to_update.append(contact)  # Для статистики
                            if existing_contact:
                                res.contacts_updated += 1
                            else:
                                res.contacts_created += 1
                        
                        # Телефоны: мягкий upsert (не удаляем вручную добавленные)
                        # Примечание добавляется в comment первого телефона
                        # ОПТИМИЗАЦИЯ: используем предзагруженные данные вместо запросов к БД
                        # ВАЖНО: в dry-run не создаем/обновляем телефоны
                        phones_added = 0
                        phones_updated = 0
                        phones_to_create: list[ContactPhone] = []
                        phones_to_update: list[ContactPhone] = []
                        
                        if dry_run:
                            # В dry-run только считаем, сколько телефонов было бы создано/обновлено
                            for pt, pv, pc in phones:
                                pv_db = str(pv).strip()[:50]
                                if not pv_db:
                                    continue
                                phone_key = (contact.id, pv_db.lower().strip())
                                obj = existing_phones_map.get(phone_key)
                                if obj is None:
                                    phones_added += 1
                                else:
                                    phones_updated += 1
                            res.skipped_writes_dry_run += phones_added + phones_updated
                            logger.debug(f"DRY-RUN: would add {phones_added} phones, update {phones_updated} phones for contact {amo_contact_id}")
                        else:
                            for idx, (pt, pv, pc) in enumerate(phones):
                                pv_db = str(pv).strip()[:50]
                                if not pv_db:
                                    continue
                                
                                # Для первого телефона добавляем примечание в comment (объединяем с существующим, если есть)
                                phone_comment = str(pc or "").strip()
                                if idx == 0 and note_text:
                                    if phone_comment:
                                        # Если уже есть комментарий, объединяем через точку с запятой
                                        phone_comment = f"{phone_comment}; {note_text[:200]}"
                                        phone_comment = phone_comment[:255]
                                    else:
                                        # Если комментария нет, просто добавляем примечание
                                        phone_comment = note_text[:255]
                                
                                # ОПТИМИЗАЦИЯ: проверяем в предзагруженной карте
                                phone_key = (contact.id, pv_db.lower().strip())
                                obj = existing_phones_map.get(phone_key)
                                
                                if obj is None:
                                    # Создаем новый телефон (добавим через bulk_create позже)
                                    phones_to_create.append(ContactPhone(
                                        contact=contact,
                                        type=pt,
                                        value=pv_db,
                                        comment=phone_comment[:255]
                                    ))
                                    phones_added += 1
                                else:
                                    # Обновляем существующий телефон (мягко)
                                    upd = False
                                    # Обновляем comment только если он пустой или совпадает с импортированным
                                    if not obj.comment and phone_comment:
                                        obj.comment = phone_comment[:255]
                                        upd = True
                                    # Обновляем type только если comment пустой или совпадает
                                    if obj.type != pt and (not obj.comment or obj.comment == phone_comment[:255]):
                                        obj.type = pt
                                        upd = True
                                    if upd:
                                        phones_to_update.append(obj)
                                        phones_updated += 1
                            
                            # Bulk-создание телефонов
                            if phones_to_create:
                                ContactPhone.objects.bulk_create(phones_to_create, ignore_conflicts=True)
                            
                            # Bulk-обновление телефонов
                            if phones_to_update:
                                ContactPhone.objects.bulk_update(phones_to_update, ["type", "comment"])
                            
                        # Email: мягкий upsert
                        # ОПТИМИЗАЦИЯ: используем предзагруженные данные
                        # ВАЖНО: в dry-run не создаем/обновляем email
                        emails_added = 0
                        emails_to_create: list[ContactEmail] = []
                        
                        if dry_run:
                            # В dry-run только считаем, сколько email было бы создано
                            for et, ev in emails:
                                ev_db = str(ev).strip().lower()
                                if not ev_db:
                                    continue
                                email_key = (contact.id, ev_db)
                                obj = existing_emails_map.get(email_key)
                                if obj is None:
                                    emails_added += 1
                            res.skipped_writes_dry_run += emails_added
                            logger.debug(f"DRY-RUN: would add {emails_added} emails for contact {amo_contact_id}")
                        else:
                            for et, ev in emails:
                                ev_db = str(ev).strip()[:254]
                                if not ev_db:
                                    continue
                                
                                # ОПТИМИЗАЦИЯ: проверяем в предзагруженной карте
                                email_key = (contact.id, ev_db.lower().strip())
                                if email_key not in existing_emails_map:
                                    emails_to_create.append(ContactEmail(
                                        contact=contact,
                                        type=et,
                                        value=ev_db
                                    ))
                                    emails_added += 1
                            
                            # Bulk-создание почт
                            if emails_to_create:
                                try:
                                    ContactEmail.objects.bulk_create(emails_to_create, ignore_conflicts=True)
                                except Exception:
                                    # Fallback: создаем по одному при ошибке
                                    for email_obj in emails_to_create:
                                        try:
                                            email_obj.save()
                                        except Exception:
                                            pass
                            
                            # Логируем результат обновления
                            debug_count_after = getattr(res, '_debug_contacts_logged', 0)
                            if debug_count_after < 10:
                                logger.debug(f"  - Updated: phones={phones_added}, emails={emails_added}, position={bool(position)}")
                    else:
                        # СОЗДАЁМ новый контакт
                        # ВАЖНО: для нового контакта old_note всегда пустая строка (нет старого значения)
                        old_note = ""
                        
                        # Сохраняем день рождения в raw_fields (пока нет поля в модели)
                        if birthday_timestamp:
                            debug_data["birthday_timestamp"] = birthday_timestamp
                        # Сохраняем все custom_fields_values в raw_fields["amo"]
                        if "amo" not in debug_data:
                            debug_data["amo"] = {}
                        debug_data["amo"]["custom_fields_values"] = ac.get("custom_fields_values") or []
                        
                        contact = Contact(
                            company=local_company,
                            first_name=first_name[:120],
                            last_name=last_name[:120],
                            position=position[:255],
                            note=note_text[:8000] if note_text else "",  # Устанавливаем примечание при создании
                            amocrm_contact_id=amo_contact_id,
                            raw_fields=debug_data,
                        )
                        # Устанавливаем данные о холодном звонке из amoCRM
                        if cold_marked_at_dt:
                            contact.is_cold_call = True
                            contact.cold_marked_at = cold_marked_at_dt
                            contact.cold_marked_by = cold_marked_by_user
                            # cold_marked_call оставляем NULL, т.к. в amoCRM нет связи с CallRequest
                        
                        # Для нового контакта всегда есть изменения (создаем новый)
                        # ВАЖНО: нормализуем None -> "" для корректного сравнения строк
                        new_note = contact.note or ""
                        contact_changed = True  # Новый контакт всегда требует сохранения
                        
                        if dry_run:
                            res.contacts_would_create += 1
                            res.skipped_writes_dry_run += 1
                            logger.debug(f"DRY-RUN: would create contact {amo_contact_id} for company {local_company.id if local_company else None}")
                            # В dry-run только считаем телефоны/email
                            phones_count = len([p for p in phones if str(p[1]).strip()])
                            emails_count = len([e for e in emails if str(e[1]).strip()])
                            res.skipped_writes_dry_run += phones_count + emails_count
                            logger.debug(f"DRY-RUN: would add {phones_count} phones, {emails_count} emails for new contact {amo_contact_id}")
                        else:
                            # ОПТИМИЗАЦИЯ: сохраняем контакт сразу (для телефонов/email нужен сохраненный контакт)
                            contact.save()
                            contacts_to_create.append(contact)  # Для статистики
                            res.contacts_created += 1
                            
                            # ОПТИМИЗАЦИЯ: используем bulk_create для телефонов и почт новых контактов
                            phones_added = 0
                            phones_to_create_new: list[ContactPhone] = []
                            
                            for idx, (pt, pv, pc) in enumerate(phones):
                                pv_db = str(pv).strip()[:50]
                                if not pv_db:
                                    continue
                                
                                # Если это первый телефон и есть примечание - добавляем в comment (объединяем с существующим, если есть)
                                phone_comment = str(pc or "").strip()
                                if idx == 0 and note_text:
                                    if phone_comment:
                                        # Если уже есть комментарий, объединяем через точку с запятой
                                        phone_comment = f"{phone_comment}; {note_text[:200]}"
                                        phone_comment = phone_comment[:255]
                                    else:
                                        # Если комментария нет, просто добавляем примечание
                                        phone_comment = note_text[:255]
                                
                                phones_to_create_new.append(ContactPhone(
                                    contact=contact,
                                    type=pt,
                                    value=pv_db,
                                    comment=phone_comment[:255]
                                ))
                                phones_added += 1
                            
                            # Bulk-создание телефонов для нового контакта
                            if phones_to_create_new:
                                ContactPhone.objects.bulk_create(phones_to_create_new, ignore_conflicts=True)
                            
                            emails_added = 0
                            emails_to_create_new: list[ContactEmail] = []
                            
                            for et, ev in emails:
                                ev_db = str(ev).strip()[:254]
                                if ev_db:
                                    emails_to_create_new.append(ContactEmail(
                                        contact=contact,
                                        type=et,
                                        value=ev_db
                                    ))
                                    emails_added += 1
                            
                            # Bulk-создание почт для нового контакта
                            if emails_to_create_new:
                                try:
                                    ContactEmail.objects.bulk_create(emails_to_create_new, ignore_conflicts=True)
                                except Exception:
                                    # Fallback: создаем по одному при ошибке
                                    for email_obj in emails_to_create_new:
                                        try:
                                            email_obj.save()
                                        except Exception:
                                            pass
                            
                            # Логируем результат сохранения
                            debug_count_after = getattr(res, '_debug_contacts_logged', 0)
                            if debug_count_after < 10:
                                logger.debug(f"  - Saved: phones={phones_added}, emails={emails_added}, position={bool(position)}")
                
                # ОПТИМИЗАЦИЯ: логируем статистику (контакты уже сохранены выше для обработки телефонов/email)
                if not dry_run:
                    if contacts_to_create:
                        logger.info(f"migrate_filtered: created {len(contacts_to_create)} new contacts")
                    if contacts_to_update:
                        skipped_count = len(full_contacts) - len(contacts_to_update) - len(contacts_to_create)
                        logger.info(f"migrate_filtered: updated {len(contacts_to_update)} existing contacts, skipped {skipped_count} without changes")
            except Exception as e:
                # Если контакты недоступны — не валим всю миграцию
                contacts_errors += 1  # Увеличиваем счетчик ошибок при исключении
                logger.error(f"ERROR importing contacts: {type(e).__name__}: {e}", exc_info=True)
                import traceback
                logger.debug("Contact import error", exc_info=True)
            finally:
                # Используем безопасный доступ к переменным (они должны быть инициализированы до try)
                logger.info(f"migrate_filtered: ===== ИМПОРТ КОНТАКТОВ ЗАВЕРШЕН: created={res.contacts_created}, seen={res.contacts_seen}, processed={contacts_processed}, skipped={contacts_skipped}, errors={contacts_errors} =====")
        else:
            logger.info(f"migrate_filtered: обработка контактов пропущена: import_contacts={import_contacts}, dry_run={dry_run}, amo_ids={bool(amo_ids)}")
            # В dry-run все равно показываем информацию, что контакты не будут импортированы
            if dry_run and not import_contacts and amo_ids:
                if res.contacts_preview is None:
                    res.contacts_preview = []
                res.contacts_preview.append({
                    "status": "INFO",
                    "message": "⚠️ Импорт контактов выключен. Включите опцию 'Импортировать контакты' для импорта.",
                    "companies_count": len(amo_ids),
                })

        # ВАЖНО: для dry-run откатываем все изменения
        # ОПТИМИЗАЦИЯ: проверяем, находимся ли мы внутри atomic блока
        if dry_run:
            try:
                # Проверяем, есть ли активная транзакция
                from django.db import connection
                if connection.in_atomic_block:
                    transaction.set_rollback(True)
                else:
                    # Если нет активной транзакции, просто не коммитим (dry-run уже не делает коммит)
                    pass
            except Exception:
                # Если произошла ошибка, просто пропускаем (dry-run уже не делает коммит)
                pass

    try:
        _run()
    except Exception as e:
        # Логируем ошибку, но не падаем - возвращаем частичный результат
        import traceback
        error_details = traceback.format_exc()
        logger.error(f"Migration failed: {type(e).__name__}: {e}")
        logger.error(f"Traceback:\n{error_details}")
        # Устанавливаем флаг ошибки в результате
        res.error = str(e)
        res.error_traceback = error_details
    
    # Логируем метрики импорта
    elapsed_time = time.time() - start_time
    metrics = client.get_metrics()
    logger.info(f"migrate_filtered: ===== МЕТРИКИ ИМПОРТА =====")
    logger.info(f"  Время выполнения: {elapsed_time:.2f} сек")
    logger.info(f"  API-запросов: {metrics['request_count']}")
    logger.info(f"  Средний RPS: {metrics['avg_rps']:.2f}")
    logger.info(f"  Компаний: seen={res.companies_seen}, matched={res.companies_matched}, batch={res.companies_batch}, created={res.companies_created}, updated={res.companies_updated}")
    logger.info(f"  Задач: seen={res.tasks_seen}, created={res.tasks_created}, updated={res.tasks_updated}")
    logger.info(f"  Заметок: seen={res.notes_seen}, created={res.notes_created}, updated={res.notes_updated}")
    logger.info(f"  Контактов: seen={res.contacts_seen}, created={res.contacts_created}")
    logger.info(f"  CompanyPhone: skynet_added={res.skynet_phones_added}, skynet_rejected={res.skynet_phone_values_rejected}, rejected_invalid={res.company_phones_rejected_invalid}")
    logger.info(f"migrate_filtered: ===== КОНЕЦ МЕТРИК =====")
    
    return res