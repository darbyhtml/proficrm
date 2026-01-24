"""
Unit-тесты для AmoClient, особенно для обработки rate limit (429).
"""
from unittest.mock import Mock, patch
import pytest

from amocrm.client import AmoClient, AmoApiError, RateLimitError, AmoResponse
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
