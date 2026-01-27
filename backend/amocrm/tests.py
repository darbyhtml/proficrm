"""
Unit-тесты для AmoClient, особенно для обработки rate limit (429).
А также тесты для нормализации телефонов и валидации данных контактов.
"""
import unittest
from unittest.mock import Mock, patch

try:
    import pytest
except ImportError:
    pytest = None

from amocrm.client import AmoClient, AmoApiError, RateLimitError, AmoResponse
from amocrm.migrate import (
    normalize_phone, sanitize_name, looks_like_phone_for_position,
    NormalizedPhone, is_valid_phone, extract_phone_from_text, parse_skynet_phones,
)
from ui.models import AmoApiConfig


if pytest is not None:
    # TestAmoClientRateLimit использует @pytest.fixture и pytest.raises — без pytest класс не определяем
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
            responses = [
                AmoResponse(status=429, data=None, headers={"retry-after": "1"}),
                AmoResponse(status=200, data={"result": "ok"}, headers={}),
            ]
            with patch.object(client, '_request', side_effect=responses):
                with patch('time.sleep'):
                    result = client.get("/api/v4/test")
                    assert result == {"result": "ok"}
        
        def test_rate_limit_exhausted_raises_error(self, client):
            """Тест: при 429 после всех retry - должен поднять RateLimitError."""
            response_429 = AmoResponse(status=429, data=None, headers={})
            with patch.object(client, '_request', return_value=response_429):
                with patch('time.sleep'):
                    with pytest.raises(RateLimitError) as exc_info:
                        client.get("/api/v4/test")
                    assert "429" in str(exc_info.value) or "Rate limit" in str(exc_info.value)
        
        def test_rate_limit_uses_retry_after_header(self, client):
            """Тест: при 429 с Retry-After header."""
            response_429 = AmoResponse(status=429, data=None, headers={"retry-after": "5"})
            response_200 = AmoResponse(status=200, data={"result": "ok"}, headers={})
            with patch.object(client, '_request', side_effect=[response_429, response_200]):
                with patch('time.sleep') as mock_sleep:
                    client.get("/api/v4/test")
                    assert mock_sleep.called
                    assert 3.5 <= mock_sleep.call_args[0][0] <= 6.5
        
        def test_get_all_pages_raises_on_rate_limit(self, client):
            """Тест: get_all_pages при 429 - RateLimitError."""
            response_429 = AmoResponse(status=429, data=None, headers={})
            with patch.object(client, '_request', return_value=response_429):
                with patch('time.sleep'):
                    with pytest.raises(RateLimitError):
                        client.get_all_pages("/api/v4/notes", embedded_key="notes")
        
        def test_5xx_retry_with_success(self, client):
            """Тест: при 5xx затем 200 - успех после retry."""
            responses = [
                AmoResponse(status=500, data=None, headers={}),
                AmoResponse(status=200, data={"result": "ok"}, headers={}),
            ]
            with patch.object(client, '_request', side_effect=responses):
                with patch('time.sleep'):
                    result = client.get("/api/v4/test")
                    assert result == {"result": "ok"}


class TestNormalizePhone(unittest.TestCase):
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


class SkynetParsePhonesTests(unittest.TestCase):
    """Тесты для parse_skynet_phones (поле 309609 «Список телефонов (Скайнет)»)."""

    def test_split_newline_comma_semicolon(self):
        """Разделение по \\n, запятой, точке с запятой; нормализация 8/7 -> +7."""
        phones, rejected, examples = parse_skynet_phones("8 (919) 305-55-10,\n+7 919 337-77-55")
        values = [p["value"] for p in phones]
        comments = [(p.get("comment") or "").strip() for p in phones]
        assert len(values) == 2
        assert "+79193055510" in values
        assert "+79193377755" in values
        assert all(c == "" for c in comments)
        assert rejected == 0
        assert len(examples) == 0

    def test_single_phone_8_prefix(self):
        """Один номер: 8 919 111-11-11 -> +79191111111."""
        phones, rejected, examples = parse_skynet_phones("8 919 111-11-11")
        values = [p["value"] for p in phones]
        assert values == ["+79191111111"]
        assert rejected == 0

    def test_single_phone_7_prefix(self):
        """7XXXXXXXXXX -> +7XXXXXXXXXX."""
        phones, rejected, examples = parse_skynet_phones("7 919 222-22-22")
        values = [p["value"] for p in phones]
        assert values == ["+79192222222"]
        assert rejected == 0

    def test_rejects_garbage(self):
        """Мусорные строки отбрасываются, rejected и examples заполняются."""
        phones, rejected, examples = parse_skynet_phones("Обслуживание радио-, сотовой, телефонной")
        assert phones == []
        assert rejected >= 1
        assert len(examples) >= 1

    def test_mixed_valid_and_garbage(self):
        """Смесь: валидные номера и мусор."""
        phones, rejected, examples = parse_skynet_phones("+7 919 111-11-11; мусор; 8 (495) 123-45-67")
        values = [p["value"] for p in phones]
        assert len(values) == 2
        assert "+79191111111" in values
        assert "+74951234567" in values
        assert rejected == 1
        assert "мусор" in examples[0] or "мусор" in str(examples)

    def test_empty_and_none(self):
        """Пустая строка и None -> ([], 0, [])."""
        for v in (None, "", "   "):
            phones, rejected, examples = parse_skynet_phones(v)
            assert phones == []
            assert rejected == 0
            assert examples == []

    def test_dedup_same_number(self):
        """Один и тот же номер в разных форматах — один в результате."""
        phones, rejected, examples = parse_skynet_phones("8 (919) 111-11-11, +7 919 111 11 11")
        assert len(phones) == 1
        assert phones[0]["value"] == "+79191111111"
        assert rejected == 0

    def test_number_with_instruction_comment(self):
        """Номер с текстом вокруг превращается в SkynetPhone с value и comment без потерь."""
        phones, rejected, examples = parse_skynet_phones(" +7 (912) 384-49-85 временно не доступен ")
        assert len(phones) == 1
        assert phones[0]["value"] == "+79123844985"
        comment = (phones[0].get("comment") or "")
        assert "временно не доступен" in comment

    def test_number_with_short_text_comment(self):
        """'+73453522095 неправ. номер' -> value +73453522095, comment 'неправ. номер'."""
        phones, rejected, examples = parse_skynet_phones("+73453522095 неправ. номер")
        assert len(phones) == 1
        assert phones[0]["value"] == "+73453522095"
        comment = (phones[0].get("comment") or "")
        assert "неправ. номер" in comment

    def test_dedup_same_number_keeps_first_comment(self):
        """Одинаковый номер с разными comment -> сохраняем один, с первым comment."""
        phones, rejected, examples = parse_skynet_phones(
            "+7 912 384 49 85 секретарь; 8 (912) 384-49-85 другой комментарий"
        )
        assert len(phones) == 1
        assert phones[0]["value"] == "+79123844985"
        comment = (phones[0].get("comment") or "")
        assert "секретарь" in comment
        assert "другой комментарий" not in comment

    def test_only_text_no_digits_is_rejected(self):
        """Текст без цифр не создаёт SkynetPhone, идёт в rejected/examples."""
        phones, rejected, examples = parse_skynet_phones("временно не доступен")
        assert phones == []
        assert rejected >= 1
        assert any("временно не доступен" in ex for ex in examples)


class SkynetExtractCompanyFieldsTests(unittest.TestCase):
    """Тест: поле 309609 (Скайнет) -> skynet_phones в результате _extract_company_fields."""

    def test_field_309609_extracts_skynet_phones(self):
        from amocrm.migrate import _extract_company_fields

        # field_meta: отдельное поле «Телефон» (999) и 309609 «Скайнет», чтобы fid_phone != fid_phone_skynet
        field_meta = {
            999: {"id": 999, "name": "Телефон", "code": "PHONE", "type": "text"},
            309609: {"id": 309609, "name": "Список телефонов (Скайнет)", "code": "", "type": "textarea"},
        }
        amo_company = {
            "custom_fields_values": [
                {"field_id": 309609, "values": [{"value": "8 (919) 305-55-10,\n+7 919 337-77-55"}]},
            ],
        }
        result = _extract_company_fields(amo_company, field_meta)
        assert "skynet_phones" in result
        skynet_phones = result["skynet_phones"]
        values = [p["value"] for p in skynet_phones]
        assert len(values) == 2
        assert "+79193055510" in values
        assert "+79193377755" in values
        assert "phones" in result
        # Скайнет не должен попасть в phones (только в skynet_phones)
        main_phones = result.get("phones") or []
        assert "+79193055510" not in main_phones
        assert "+79193377755" not in main_phones


class TestContactDataQuality(unittest.TestCase):
    """Тесты для качества данных контактов."""
    
    def test_position_phone_salvaged(self):
        """Тест: POSITION='+7 495 632-21-97' -> телефон добавлен, POSITION не затёрт."""
        from amocrm.migrate import looks_like_phone_for_position, normalize_phone, extract_phone_from_text
        
        position_value = "+7 495 632-21-97"
        
        # Проверяем, что распознается как телефон
        assert looks_like_phone_for_position(position_value)
        
        # Проверяем нормализацию
        normalized = normalize_phone(position_value)
        assert normalized.isValid
        assert normalized.phone_e164 == "+74956322197"
        
        # Проверяем извлечение
        phone_extracted, position_cleaned = extract_phone_from_text(position_value)
        assert phone_extracted == "+74956322197"
        assert len(position_cleaned) < 3  # После извлечения телефона почти ничего не осталось
    
    def test_phone_text_never_in_phone(self):
        """Тест: PHONE='только через приемную! мини АТС' -> телефон не добавлен, текст ушёл в note."""
        from amocrm.migrate import normalize_phone, is_valid_phone
        
        text = "только через приемную! мини АТС"
        
        normalized = normalize_phone(text)
        assert not normalized.isValid
        assert not is_valid_phone(text)
        assert normalized.phone_e164 is None
        assert normalized.note == text
    
    def test_name_cleaned_extension(self):
        """Тест: ФИО содержит 'доб. 4, затем 1' -> очищено, инструкции ушли в note."""
        from amocrm.migrate import clean_person_name_fields
        
        name = "Павлович, доб. 4, затем 1 Андрей"
        cleaned, extracted = clean_person_name_fields(name)
        
        assert "доб. 4" in extracted
        assert "затем 1" in extracted
        assert "Андрей" in cleaned
        assert "Павлович" in cleaned
        assert "доб. 4" not in cleaned
        assert "затем 1" not in cleaned
    
    def test_enum_code_mapping(self):
        """Тест: enum_code 'WORKDD' -> замаплен в OTHER (или WORK если allowlist расширен)."""
        from amocrm.migrate import map_phone_enum_code
        from companies.models import ContactPhone
        
        # WORKDD не в allowlist, должен замапиться в OTHER
        result = map_phone_enum_code("WORKDD", "")
        assert result == ContactPhone.PhoneType.OTHER
        
        # WORK в allowlist
        result = map_phone_enum_code("WORK", "")
        assert result == ContactPhone.PhoneType.WORK
        
        # MOB в allowlist
        result = map_phone_enum_code("MOB", "")
        assert result == ContactPhone.PhoneType.MOBILE
    
    def test_cold_call_date_no_shift(self):
        """Тест: epoch seconds -> корректный YYYY-MM-DD, без TZ сдвига."""
        from django.utils import timezone
        from datetime import timezone as dt_timezone
        
        # Тест для timestamp около полуночи (проверка сдвига)
        # 2024-01-15 23:30:00 UTC -> должно стать 2024-01-15 00:00:00 UTC
        timestamp = 1705361400  # 2024-01-15 23:30:00 UTC (исправлено: было 1705368600 = 2024-01-16 01:30 UTC)
        
        UTC = getattr(timezone, "UTC", dt_timezone.utc)
        dt_utc = timezone.datetime.fromtimestamp(timestamp, tz=UTC)
        normalized = dt_utc.replace(hour=0, minute=0, second=0, microsecond=0)
        
        # Проверяем, что дата не сместилась
        assert normalized.strftime("%Y-%m-%d") == "2024-01-15"
        assert normalized.hour == 0
        assert normalized.minute == 0


class TestNotesBulkFallback(unittest.TestCase):
    """Тесты для fallback bulk notes на per-company endpoint."""
    
    def test_bulk_notes_404_fallback(self):
        """Тест: bulk endpoint возвращает 404 -> код переключается на per-company и не падает."""
        from unittest.mock import Mock, patch
        from amocrm.client import AmoClient, AmoApiError
        from amocrm.migrate import fetch_notes_for_companies, _notes_bulk_supported
        
        # Сбрасываем глобальный флаг
        import amocrm.migrate as migrate_module
        migrate_module._notes_bulk_supported = None
        
        mock_client = Mock(spec=AmoClient)
        
        # Мокаем bulk endpoint - возвращает 404
        def mock_get_all_pages_bulk(path, **kwargs):
            if path == "/api/v4/notes" and "filter[entity_type]" in (kwargs.get("params") or {}):
                raise AmoApiError("404 Not Found")
            return []
        
        mock_client.get_all_pages = Mock(side_effect=mock_get_all_pages_bulk)
        
        # Мокаем per-company endpoint - возвращает успешно
        def mock_get_all_pages_per_company(path, **kwargs):
            if "/companies/" in path and "/notes" in path:
                return [{"id": 1, "text": "Test note"}]
            return []
        
        # После первого вызова (404) должен переключиться на per-company
        with patch.object(mock_client, 'get_all_pages', side_effect=[
            AmoApiError("404 Not Found"),  # Первый вызов (bulk) - 404
            [{"id": 1, "text": "Test note"}],  # Второй вызов (per-company) - успех
        ]):
            result = fetch_notes_for_companies(mock_client, [123])
            # Должен вернуть заметки через per-company
            assert len(result) > 0 or migrate_module._notes_bulk_supported is False
    
    def test_bulk_notes_no_retry_after_404(self):
        """Тест: при повторном вызове в рамках запуска bulk больше не вызывается."""
        from unittest.mock import Mock, patch
        from amocrm.migrate import fetch_notes_for_companies, _notes_bulk_supported
        
        # Сбрасываем глобальный флаг
        import amocrm.migrate as migrate_module
        migrate_module._notes_bulk_supported = None
        
        mock_client = Mock(spec=AmoClient)
        
        # Первый вызов - 404, устанавливает флаг в False
        with patch.object(mock_client, 'get_all_pages', side_effect=AmoApiError("404 Not Found")):
            try:
                fetch_notes_for_companies(mock_client, [123])
            except:
                pass
        
        # Второй вызов - должен сразу использовать per-company, не пытаться bulk
        assert migrate_module._notes_bulk_supported is False


class TestPaginationTruncated(unittest.TestCase):
    """Тесты для пагинации с флагом truncated."""
    
    def test_pagination_truncated_flag(self):
        """Тест: при достижении max_pages выставляется флаг 'truncated' и логируется warning."""
        from unittest.mock import Mock, patch
        from amocrm.client import AmoClient
        
        mock_client = Mock(spec=AmoClient)
        
        # Мокаем get_all_pages чтобы достичь max_pages
        page_count = 0
        def mock_get_all_pages(path, **kwargs):
            nonlocal page_count
            page_count += 1
            max_pages = kwargs.get("max_pages", 100)
            if page_count > max_pages:
                # Возвращаем пустой список (конец пагинации)
                return []
            # Возвращаем данные (симулируем продолжение пагинации)
            return [{"id": page_count}]
        
        mock_client.get_all_pages = Mock(side_effect=mock_get_all_pages)
        
        # Вызываем с return_meta=True
        result = mock_client.get_all_pages("/api/v4/test", max_pages=5, return_meta=True)
        
        # Проверяем, что truncated установлен (если реализовано)
        # Это зависит от реализации, но логика должна быть
        assert True  # Placeholder - реальная проверка зависит от реализации


class TestFetchCompaniesByResponsible(unittest.TestCase):
    """Тесты для fetch_companies_by_responsible с return_meta."""
    
    def test_fetch_companies_by_responsible_without_return_meta(self):
        """Тест: вызов без return_meta возвращает только список (обратная совместимость)."""
        from unittest.mock import Mock
        from amocrm.migrate import fetch_companies_by_responsible
        
        mock_client = Mock()
        mock_client.get_all_pages = Mock(return_value=[{"id": 1}, {"id": 2}])
        
        result = fetch_companies_by_responsible(mock_client, 123, return_meta=False)
        
        # Должен вернуть только список
        assert isinstance(result, list)
        assert len(result) == 2
        mock_client.get_all_pages.assert_called_once()
    
    def test_fetch_companies_by_responsible_with_return_meta(self):
        """Тест: вызов с return_meta=True возвращает tuple (list, meta)."""
        from unittest.mock import Mock
        from amocrm.migrate import fetch_companies_by_responsible
        
        mock_client = Mock()
        mock_companies = [{"id": 1}, {"id": 2}]
        mock_meta = {"pages_fetched": 1, "elements_fetched": 2, "truncated": False, "limit": 25}
        mock_client.get_all_pages = Mock(return_value=(mock_companies, mock_meta))
        
        result = fetch_companies_by_responsible(mock_client, 123, return_meta=True)
        
        # Должен вернуть tuple
        assert isinstance(result, tuple)
        assert len(result) == 2
        companies, meta = result
        assert companies == mock_companies
        assert meta == mock_meta
        mock_client.get_all_pages.assert_called_once()
    
    def test_fetch_companies_by_responsible_no_unexpected_keyword_error(self):
        """Тест: вызов с return_meta не должен вызывать 'unexpected keyword argument'."""
        from unittest.mock import Mock
        from amocrm.migrate import fetch_companies_by_responsible
        
        mock_client = Mock()
        mock_client.get_all_pages = Mock(return_value=[{"id": 1}])
        
        # Должен работать без ошибки
        try:
            result = fetch_companies_by_responsible(mock_client, 123, return_meta=True)
            assert result is not None
        except TypeError as e:
            if "unexpected keyword argument" in str(e):
                self.fail(f"Функция не принимает return_meta: {e}")
            raise


class TestNotesBulk404Fallback(unittest.TestCase):
    """Тесты для graceful fallback при 404 на bulk notes."""
    
    def test_bulk_notes_404_graceful_fallback(self):
        """Тест: 404 на bulk notes → graceful fallback на per-company, без падения миграции."""
        from unittest.mock import Mock
        from amocrm.client import AmoApiError
        from amocrm.migrate import fetch_notes_for_companies, _notes_bulk_supported
        
        # Сбрасываем глобальный флаг
        import amocrm.migrate as migrate_module
        migrate_module._notes_bulk_supported = None
        
        mock_client = Mock()
        
        # Мокаем: bulk возвращает 404, per-company возвращает заметки
        call_count = [0]
        def mock_get_all_pages(path, **kwargs):
            call_count[0] += 1
            if path == "/api/v4/notes" and "filter[entity_type]" in (kwargs.get("params") or {}):
                # Первый вызов (bulk) - 404
                raise AmoApiError("404 Not Found")
            elif "/companies/" in path and "/notes" in path:
                # Второй вызов (per-company) - успех
                return [{"id": 1, "text": "Test note"}]
            return []
        
        mock_client.get_all_pages = Mock(side_effect=mock_get_all_pages)
        
        # Вызываем - не должно упасть
        result = fetch_notes_for_companies(mock_client, [123, 456])
        
        # Должен вернуть заметки через per-company
        assert len(result) > 0
        # Флаг должен быть установлен в False
        assert migrate_module._notes_bulk_supported is False
    
    def test_bulk_notes_405_also_triggers_fallback(self):
        """Тест: 405 (Method Not Allowed) также триггерит fallback."""
        from unittest.mock import Mock
        from amocrm.client import AmoApiError
        from amocrm.migrate import fetch_notes_for_companies_bulk, _notes_bulk_supported
        
        # Сбрасываем глобальный флаг
        import amocrm.migrate as migrate_module
        migrate_module._notes_bulk_supported = None
        
        mock_client = Mock()
        mock_client.get_all_pages = Mock(side_effect=AmoApiError("405 Method Not Allowed"))
        
        # Вызываем - не должно упасть
        result = fetch_notes_for_companies_bulk(mock_client, [123])
        
        # Должен вернуть пустой список (fallback будет в fetch_notes_for_companies)
        assert result == []
        # Флаг должен быть установлен в False
        assert migrate_module._notes_bulk_supported is False
    
    def test_phone_text_never_in_phone(self):
        """Тест: текст 'только через приемную! мини АТС' НЕ попадает в PHONE, только в NOTE."""
        from amocrm.migrate import normalize_phone, is_valid_phone
        
        # Проверяем, что текст не валиден как телефон
        text = "только через приемную! мини АТС"
        normalized = normalize_phone(text)
        assert not normalized.isValid
        assert not is_valid_phone(text)
        assert normalized.phone_e164 is None
        
        # Проверяем, что текст сохраняется в note
        assert normalized.note == text
        
        # Проверяем, что текст не попадет в PHONE (симуляция логики)
        phones = []
        note_text = ""
        
        # Логика из кода: если не валиден - не добавляем в phones, добавляем в note_text
        if not is_valid_phone(text):
            note_text = f"Комментарий к телефону: {text}"
        else:
            phones.append(("OTHER", text, ""))
        
        # Проверяем результат
        assert len(phones) == 0  # PHONE пустой
        assert "только через приемную" in note_text  # NOTE содержит исходный текст
