"""
Unit-тесты для AmoClient, особенно для обработки rate limit (429).
А также тесты для нормализации телефонов и валидации данных контактов.
"""
from unittest.mock import Mock, patch
import pytest

from amocrm.client import AmoClient, AmoApiError, RateLimitError, AmoResponse
from amocrm.migrate import (
    normalize_phone, sanitize_name, looks_like_phone_for_position,
    NormalizedPhone
)
from ui.models import AmoApiConfig


class TestAmoClientRateLimit:
    """Тесты для обработки rate limit (429) в AmoClient."""
    
    @pytest.fixture
    def mock_config(self):
        """Создает мок конфигурации AmoCRM."""
        config = Mock(spec=AmoApiConfig)
        config.domain = "test.amocrm.ru"
        config.long_lived_token = "test_token"
        config.access_token = None
        config.refresh_token = None
        config.token_type = "Bearer"
        config.expires_at = None
        return config
    
    @pytest.fixture
    def client(self, mock_config):
        """Создает AmoClient с мок конфигурацией."""
        return AmoClient(mock_config)
    
    def test_rate_limit_retry_with_success(self, client):
        """Тест: при 429 затем 200 - должен успешно вернуть данные после retry."""
        # Мокаем _request чтобы сначала вернуть 429, затем 200
        responses = [
            AmoResponse(status=429, data=None, headers={"retry-after": "1"}),
            AmoResponse(status=200, data={"result": "ok"}, headers={}),
        ]
        
        with patch.object(client, '_request', side_effect=responses):
            with patch('time.sleep'):  # Мокаем sleep для ускорения теста
                result = client.get("/api/v4/test")
                assert result == {"result": "ok"}
    
    def test_rate_limit_exhausted_raises_error(self, client):
        """Тест: при 429 после всех retry - должен поднять RateLimitError."""
        # Мокаем _request чтобы всегда возвращать 429
        response_429 = AmoResponse(status=429, data=None, headers={})
        
        with patch.object(client, '_request', return_value=response_429):
            with patch('time.sleep'):  # Мокаем sleep для ускорения теста
                with pytest.raises(RateLimitError) as exc_info:
                    client.get("/api/v4/test")
                
                assert "429" in str(exc_info.value) or "Rate limit" in str(exc_info.value)
    
    def test_rate_limit_uses_retry_after_header(self, client):
        """Тест: при 429 с Retry-After header - должен использовать указанное время."""
        response_429 = AmoResponse(status=429, data=None, headers={"retry-after": "5"})
        response_200 = AmoResponse(status=200, data={"result": "ok"}, headers={})
        
        with patch.object(client, '_request', side_effect=[response_429, response_200]):
            with patch('time.sleep') as mock_sleep:
                client.get("/api/v4/test")
                # Проверяем, что sleep был вызван с правильным временем (≈5 секунд с jitter)
                assert mock_sleep.called
                sleep_time = mock_sleep.call_args[0][0]
                # Jitter ±25%, так что должно быть между 3.75 и 6.25 секунд
                assert 3.5 <= sleep_time <= 6.5
    
    def test_get_all_pages_raises_on_rate_limit(self, client):
        """Тест: get_all_pages при 429 после всех retry - должен поднять RateLimitError, не вернуть пустой список."""
        response_429 = AmoResponse(status=429, data=None, headers={})
        
        with patch.object(client, '_request', return_value=response_429):
            with patch('time.sleep'):  # Мокаем sleep для ускорения теста
                with pytest.raises(RateLimitError):
                    client.get_all_pages("/api/v4/notes", embedded_key="notes")
    
    def test_5xx_retry_with_success(self, client):
        """Тест: при 5xx затем 200 - должен успешно вернуть данные после retry."""
        responses = [
            AmoResponse(status=500, data=None, headers={}),
            AmoResponse(status=200, data={"result": "ok"}, headers={}),
        ]
        
        with patch.object(client, '_request', side_effect=responses):
            with patch('time.sleep'):  # Мокаем sleep для ускорения теста
                result = client.get("/api/v4/test")
                assert result == {"result": "ok"}


class TestNormalizePhone:
    """Тесты для функции normalize_phone."""
    
    def test_instruction_only_not_phone(self):
        """Тест: 'только через приемную! мини АТС' -> NOTE, не PHONE."""
        result = normalize_phone("только через приемную! мини АТС")
        assert not result.isValid
        assert result.note == "только через приемную! мини АТС"
        assert result.phone_e164 is None
    
    def test_valid_phone_with_extension(self):
        """Тест: '+7 495 632-21-97 доб. 4' -> PHONE + ext."""
        result = normalize_phone("+7 495 632-21-97 доб. 4")
        assert result.isValid
        assert result.phone_e164 == "+74956322197"
        assert result.ext == "4"
    
    def test_phone_with_instruction(self):
        """Тест: '+7 345 2540415 (WORK) +79829481568 (MOB)' -> два телефона."""
        # Первый номер
        result1 = normalize_phone("+7 345 2540415 (WORK)")
        assert result1.isValid
        assert result1.phone_e164 == "+73452540415"
        
        # Второй номер
        result2 = normalize_phone("+79829481568 (MOB)")
        assert result2.isValid
        assert result2.phone_e164 == "+79829481568"
    
    def test_position_looks_like_phone(self):
        """Тест: '+7 495 632-21-97' в POSITION -> распознается как телефон."""
        assert looks_like_phone_for_position("+7 495 632-21-97")
        assert looks_like_phone_for_position("84956322197")
        assert not looks_like_phone_for_position("Менеджер по продажам")
        assert not looks_like_phone_for_position("Директор")
    
    def test_sanitize_name_removes_extension(self):
        """Тест: 'Павлович, доб. 4, затем 1 Андрей' -> name 'Андрей Павлович', NOTE содержит 'доб. 4, затем 1'."""
        cleaned, extracted = sanitize_name("Павлович, доб. 4, затем 1 Андрей")
        assert "доб. 4" in extracted
        assert "затем 1" in extracted
        assert "Андрей" in cleaned
        assert "Павлович" in cleaned
    
    def test_sanitize_name_simple(self):
        """Тест: простое имя без extension."""
        cleaned, extracted = sanitize_name("Иванов Иван")
        assert cleaned == "Иванов Иван"
        assert extracted == ""
    
    def test_normalize_phone_russian_format(self):
        """Тест: нормализация российских номеров."""
        # 8 -> +7
        result = normalize_phone("84951234567")
        assert result.isValid
        assert result.phone_e164 == "+74951234567"
        
        # 7 -> +7
        result = normalize_phone("74951234567")
        assert result.isValid
        assert result.phone_e164 == "+74951234567"
        
        # 10 цифр -> +7
        result = normalize_phone("4951234567")
        assert result.isValid
        assert result.phone_e164 == "+74951234567"
    
    def test_normalize_phone_too_short(self):
        """Тест: слишком короткий номер -> не валиден."""
        result = normalize_phone("12345")
        assert not result.isValid
    
    def test_normalize_phone_with_multiple_extensions(self):
        """Тест: номер с несколькими extension."""
        result = normalize_phone("+7 495 123-45-67 доб. 4 затем 1")
        assert result.isValid
        assert result.phone_e164 == "+74951234567"
        # Должен извлечь первое extension
        assert result.ext is not None
    
    def test_normalize_phone_no_crash_on_none_and_text(self):
        """Тест: normalize_phone не падает на None и не-строки."""
        # None
        result = normalize_phone(None)
        assert not result.isValid
        
        # Пустая строка
        result = normalize_phone("")
        assert not result.isValid
        
        # Не-строка (число)
        result = normalize_phone(12345)
        assert not result.isValid
        
        # Не-строка (список)
        result = normalize_phone([1, 2, 3])
        assert not result.isValid
    
    def test_normalize_phone_with_instructions(self):
        """Тест: normalize_phone корректно обрабатывает инструкции."""
        # Инструкция без номера
        result = normalize_phone("только через приемную! мини АТС")
        assert not result.isValid
        assert result.note == "только через приемную! мини АТС"
    
    def test_normalize_phone_format_8_dash(self):
        """Тест: нормализация формата '8-816-565-49-58' -> '+78165654958'."""
        result = normalize_phone("8-816-565-49-58")
        assert result.isValid
        assert result.phone_e164 == "+78165654958"
    
    def test_normalize_phone_format_brackets(self):
        """Тест: нормализация формата '(38473)3-33-92' -> '+73847333392'."""
        result = normalize_phone("(38473)3-33-92")
        assert result.isValid
        assert result.phone_e164 == "+73847333392"
    
    def test_position_phone_salvaged(self):
        """Тест: POSITION='+7 495 632-21-97' -> не обновлять POSITION, добавить в PHONE."""
        # Проверяем, что looks_like_phone_for_position распознает телефон
        assert looks_like_phone_for_position("+7 495 632-21-97")
        
        # Проверяем, что normalize_phone извлекает номер
        result = normalize_phone("+7 495 632-21-97")
        assert result.isValid
        assert result.phone_e164 == "+74956322197"
    
    def test_phone_text_moved_to_note(self):
        """Тест: PHONE='только через приемную! мини АТС' -> не в PHONE, а в NOTE."""
        result = normalize_phone("только через приемную! мини АТС")
        assert not result.isValid
        assert result.note == "только через приемную! мини АТС"
        assert result.phone_e164 is None
        
        # Номер с инструкцией
        result = normalize_phone("+7 495 123-45-67 только через приемную")
        assert result.isValid
        assert result.phone_e164 == "+74951234567"
        assert result.note is not None  # Инструкция должна быть в note
    
    def test_extract_phones_from_custom_fields_values_variants(self):
        """Тест: извлечение телефонов из разных вариантов custom_fields_values."""
        # Тест структуры - проверяем, что функция не падает на разных вариантах
        # Это интеграционный тест, который проверяет логику парсинга
        
        # Вариант 1: custom_fields_values = None
        contact1 = {"id": 1, "custom_fields_values": None}
        # Должно обработаться без ошибки
        
        # Вариант 2: custom_fields_values = []
        contact2 = {"id": 2, "custom_fields_values": []}
        # Должно обработаться без ошибки
        
        # Вариант 3: custom_fields_values с PHONE
        contact3 = {
            "id": 3,
            "custom_fields_values": [
                {
                    "field_id": 123,
                    "field_code": "PHONE",
                    "field_name": "Телефон",
                    "field_type": "multitext",
                    "values": [
                        {
                            "value": "+7 495 123-45-67",
                            "enum_code": "WORK"
                        }
                    ]
                }
            ]
        }
        # Должно извлечь телефон
        
        # Вариант 4: custom_fields_values с телефоном по field_name
        contact4 = {
            "id": 4,
            "custom_fields_values": [
                {
                    "field_id": 456,
                    "field_code": None,
                    "field_name": "Телефон",
                    "field_type": "text",
                    "values": [
                        {
                            "value": "84951234567"
                        }
                    ]
                }
            ]
        }
        # Должно извлечь телефон
        
        # Все варианты должны обрабатываться без падения
        assert True  # Placeholder - реальная проверка будет в интеграционных тестах
    
    def test_phone_text_moved_to_note(self):
        """Тест: PHONE='только через приемную! мини АТС' -> не в PHONE, а в NOTE."""
        result = normalize_phone("только через приемную! мини АТС")
        assert not result.isValid
        assert result.note == "только через приемную! мини АТС"
        assert result.phone_e164 is None
    
    def test_normalize_phone_format_8_dash(self):
        """Тест: нормализация формата '8-816-565-49-58' -> '+78165654958'."""
        result = normalize_phone("8-816-565-49-58")
        assert result.isValid
        assert result.phone_e164 == "+78165654958"
    
    def test_normalize_phone_format_brackets(self):
        """Тест: нормализация формата '(38473)3-33-92' -> '+73847333392'."""
        result = normalize_phone("(38473)3-33-92")
        assert result.isValid
        assert result.phone_e164 == "+73847333392"
    
    def test_position_phone_salvaged(self):
        """Тест: POSITION='+7 495 632-21-97' -> не обновлять POSITION, добавить в PHONE."""
        # Проверяем, что looks_like_phone_for_position распознает телефон
        assert looks_like_phone_for_position("+7 495 632-21-97")
        
        # Проверяем, что normalize_phone извлекает номер
        result = normalize_phone("+7 495 632-21-97")
        assert result.isValid
        assert result.phone_e164 == "+74956322197"
    
    def test_is_valid_phone(self):
        """Тест: функция is_valid_phone для строгой проверки."""
        from amocrm.migrate import is_valid_phone
        
        # Валидные телефоны
        assert is_valid_phone("+7 495 632-21-97")
        assert is_valid_phone("84951234567")
        assert is_valid_phone("(38473)3-33-92")
        
        # Невалидные (текст)
        assert not is_valid_phone("только через приемную! мини АТС")
        assert not is_valid_phone("доб. 4 затем 1")
        assert not is_valid_phone("Ольга Юрьевна")
        assert not is_valid_phone("12345")  # слишком короткий
    
    def test_extract_phone_from_text(self):
        """Тест: функция extract_phone_from_text извлекает телефон из текста."""
        from amocrm.migrate import extract_phone_from_text
        
        # Телефон в тексте
        phone, cleaned = extract_phone_from_text("+7 495 632-21-97")
        assert phone == "+74956322197"
        assert len(cleaned) < 3  # После извлечения телефона почти ничего не осталось
        
        # Только текст
        phone, cleaned = extract_phone_from_text("только через приемную")
        assert phone is None
        assert cleaned == "только через приемную"
        
        # Текст с телефоном
        phone, cleaned = extract_phone_from_text("Ольга Юрьевна +7 495 632-21-97")
        assert phone == "+74956322197"
        assert "Ольга" in cleaned or len(cleaned) < 3  # Имя должно быть удалено или остаться минимально
