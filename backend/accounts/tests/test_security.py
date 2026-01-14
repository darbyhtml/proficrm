"""
Unit-тесты для модуля безопасности (accounts.security).
"""
from django.test import TestCase, RequestFactory
from unittest.mock import patch
from accounts.security import get_client_ip, _is_valid_ip


class SecurityTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
    
    def test_is_valid_ip_ipv4(self):
        """Проверка валидации IPv4 адресов."""
        self.assertTrue(_is_valid_ip("192.168.1.1"))
        self.assertTrue(_is_valid_ip("127.0.0.1"))
        self.assertTrue(_is_valid_ip("8.8.8.8"))
        self.assertFalse(_is_valid_ip("256.256.256.256"))
        self.assertFalse(_is_valid_ip("192.168.1"))
        self.assertFalse(_is_valid_ip("not.an.ip"))
        self.assertFalse(_is_valid_ip(""))
    
    def test_is_valid_ip_ipv6(self):
        """Проверка валидации IPv6 адресов."""
        self.assertTrue(_is_valid_ip("2001:0db8:85a3:0000:0000:8a2e:0370:7334"))
        self.assertTrue(_is_valid_ip("::1"))
        self.assertTrue(_is_valid_ip("2001:db8::1"))
        self.assertFalse(_is_valid_ip("2001:db8::1::2"))  # Двойной :: невалиден
        self.assertFalse(_is_valid_ip("2001:db8:g::1"))  # Невалидный символ
    
    def test_get_client_ip_direct_connection(self):
        """Проверка получения IP при прямом подключении (без прокси)."""
        request = self.factory.get("/")
        request.META["REMOTE_ADDR"] = "192.168.1.100"
        
        with patch("accounts.security.settings.PROXY_IPS", []):
            ip = get_client_ip(request)
            self.assertEqual(ip, "192.168.1.100")
    
    def test_get_client_ip_trusted_proxy_single_ip(self):
        """Проверка получения IP за доверенным прокси (один IP в X-Forwarded-For)."""
        request = self.factory.get("/")
        request.META["REMOTE_ADDR"] = "10.0.0.1"  # IP прокси
        request.META["HTTP_X_FORWARDED_FOR"] = "192.168.1.100"  # IP клиента
        
        with patch("accounts.security.settings.PROXY_IPS", ["10.0.0.1"]):
            ip = get_client_ip(request)
            self.assertEqual(ip, "192.168.1.100")
    
    def test_get_client_ip_trusted_proxy_multiple_ips(self):
        """Проверка получения IP за доверенным прокси (несколько IP в X-Forwarded-For)."""
        request = self.factory.get("/")
        request.META["REMOTE_ADDR"] = "10.0.0.1"  # IP прокси
        request.META["HTTP_X_FORWARDED_FOR"] = "192.168.1.100, 10.0.0.2, 172.16.0.1"
        
        with patch("accounts.security.settings.PROXY_IPS", ["10.0.0.1"]):
            ip = get_client_ip(request)
            # Должен взять первый IP (клиент)
            self.assertEqual(ip, "192.168.1.100")
    
    def test_get_client_ip_trusted_proxy_with_spaces(self):
        """Проверка обработки X-Forwarded-For с пробелами."""
        request = self.factory.get("/")
        request.META["REMOTE_ADDR"] = "10.0.0.1"
        request.META["HTTP_X_FORWARDED_FOR"] = " 192.168.1.100 , 10.0.0.2 "
        
        with patch("accounts.security.settings.PROXY_IPS", ["10.0.0.1"]):
            ip = get_client_ip(request)
            self.assertEqual(ip, "192.168.1.100")
    
    def test_get_client_ip_untrusted_proxy(self):
        """Проверка защиты от spoofing: недоверенный прокси."""
        request = self.factory.get("/")
        request.META["REMOTE_ADDR"] = "192.168.1.200"  # Не в списке прокси
        request.META["HTTP_X_FORWARDED_FOR"] = "1.2.3.4"  # Пытается подделать IP
        
        with patch("accounts.security.settings.PROXY_IPS", ["10.0.0.1"]):
            ip = get_client_ip(request)
            # Должен использовать REMOTE_ADDR, игнорируя X-Forwarded-For
            self.assertEqual(ip, "192.168.1.200")
    
    def test_get_client_ip_invalid_xff_fallback(self):
        """Проверка fallback на REMOTE_ADDR при невалидном X-Forwarded-For."""
        request = self.factory.get("/")
        request.META["REMOTE_ADDR"] = "10.0.0.1"
        request.META["HTTP_X_FORWARDED_FOR"] = "invalid.ip.address"
        
        with patch("accounts.security.settings.PROXY_IPS", ["10.0.0.1"]):
            ip = get_client_ip(request)
            # Должен использовать REMOTE_ADDR при невалидном XFF
            self.assertEqual(ip, "10.0.0.1")
    
    def test_get_client_ip_invalid_xff_try_next(self):
        """Проверка попытки использовать следующий IP при невалидном первом."""
        request = self.factory.get("/")
        request.META["REMOTE_ADDR"] = "10.0.0.1"
        request.META["HTTP_X_FORWARDED_FOR"] = "invalid, 192.168.1.100, also-invalid"
        
        with patch("accounts.security.settings.PROXY_IPS", ["10.0.0.1"]):
            ip = get_client_ip(request)
            # Должен взять первый валидный IP
            self.assertEqual(ip, "192.168.1.100")
    
    def test_get_client_ip_no_remote_addr(self):
        """Проверка обработки отсутствия REMOTE_ADDR."""
        request = self.factory.get("/")
        request.META.pop("REMOTE_ADDR", None)
        
        with patch("accounts.security.settings.PROXY_IPS", []):
            ip = get_client_ip(request)
            self.assertEqual(ip, "unknown")
    
    def test_get_client_ip_invalid_remote_addr(self):
        """Проверка обработки невалидного REMOTE_ADDR."""
        request = self.factory.get("/")
        request.META["REMOTE_ADDR"] = "invalid"
        
        with patch("accounts.security.settings.PROXY_IPS", []):
            ip = get_client_ip(request)
            self.assertEqual(ip, "unknown")
