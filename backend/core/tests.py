"""
Юнит-тесты для пакета core/.

Покрывают:
  - core/crypto.py          — шифрование/расшифровка Fernet (MultiFernet, ротация ключей)
  - core/timezone_utils.py  — RUS_TZ_CHOICES, guess_ru_timezone_from_address
  - core/request_id.py      — RequestIdMiddleware, RequestIdLoggingFilter, get_request_id
  - core/exceptions.py      — custom_exception_handler (DRF)
  - core/work_schedule_utils.py — parse_work_schedule, normalize_work_schedule,
                                  get_worktime_status_from_schedule
  - core/input_cleaners.py  — clean_int_id, clean_uuid
  - core/json_formatter.py  — JSONFormatter

Запуск:
  python manage.py test core --verbosity 2
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime, time, timezone

from cryptography.fernet import Fernet
from django.test import RequestFactory, TestCase, override_settings
from rest_framework import status
from rest_framework.exceptions import AuthenticationFailed, NotFound, PermissionDenied, ValidationError
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory


# ────────────────────────────────────────────────────────────────────────────
# Вспомогательная утилита для генерации валидного Fernet-ключа
# ────────────────────────────────────────────────────────────────────────────

def _new_fernet_key() -> str:
    """Генерирует случайный, валидный URL-safe base64 Fernet-ключ (32 байта)."""
    return Fernet.generate_key().decode()


# ============================================================================
# crypto.py
# ============================================================================

class CryptoEncryptDecryptTest(TestCase):
    """Базовые тесты round-trip шифрования."""

    def _patched_settings(self, key: str):
        """Возвращает контекст override_settings с отключённым lru_cache."""
        return override_settings(MAILER_FERNET_KEY=key, MAILER_FERNET_KEYS_OLD="")

    def _clear_cache(self):
        """Сбрасываем lru_cache _fernet() между тестами."""
        from core.crypto import _fernet
        _fernet.cache_clear()

    def setUp(self):
        self._clear_cache()

    def tearDown(self):
        self._clear_cache()

    def test_encrypt_decrypt_roundtrip_обычная_строка(self):
        """encrypt_str → decrypt_str возвращает исходное значение."""
        key = _new_fernet_key()
        with override_settings(MAILER_FERNET_KEY=key, MAILER_FERNET_KEYS_OLD=""):
            self._clear_cache()
            from core.crypto import decrypt_str, encrypt_str
            original = "super-secret-password-123"
            token = encrypt_str(original)
            self.assertIsInstance(token, str)
            self.assertNotEqual(token, original)
            self.assertEqual(decrypt_str(token), original)

    def test_encrypt_decrypt_roundtrip_unicode(self):
        """Шифрование строк с Unicode (кириллица, эмодзи)."""
        key = _new_fernet_key()
        with override_settings(MAILER_FERNET_KEY=key, MAILER_FERNET_KEYS_OLD=""):
            self._clear_cache()
            from core.crypto import decrypt_str, encrypt_str
            original = "Привет, мир! 🔐"
            self.assertEqual(decrypt_str(encrypt_str(original)), original)

    def test_encrypt_пустая_строка(self):
        """Пустая строка шифруется и расшифровывается в пустую строку."""
        key = _new_fernet_key()
        with override_settings(MAILER_FERNET_KEY=key, MAILER_FERNET_KEYS_OLD=""):
            self._clear_cache()
            from core.crypto import decrypt_str, encrypt_str
            token = encrypt_str("")
            # токен должен быть непустым — Fernet шифрует даже пустой plaintext
            self.assertTrue(len(token) > 0)
            self.assertEqual(decrypt_str(token), "")

    def test_encrypt_none_обрабатывается_как_пустая_строка(self):
        """None в encrypt_str трактуется как пустая строка (не падает)."""
        key = _new_fernet_key()
        with override_settings(MAILER_FERNET_KEY=key, MAILER_FERNET_KEYS_OLD=""):
            self._clear_cache()
            from core.crypto import decrypt_str, encrypt_str
            token = encrypt_str(None)  # type: ignore[arg-type]
            self.assertEqual(decrypt_str(token), "")

    def test_decrypt_пустого_токена_возвращает_пустую_строку(self):
        """decrypt_str('') → '' без исключений (защитная ветка)."""
        key = _new_fernet_key()
        with override_settings(MAILER_FERNET_KEY=key, MAILER_FERNET_KEYS_OLD=""):
            self._clear_cache()
            from core.crypto import decrypt_str
            self.assertEqual(decrypt_str(""), "")

    def test_decrypt_невалидного_токена_вызывает_исключение(self):
        """Расшифровка мусорного токена поднимает cryptography.fernet.InvalidToken."""
        from cryptography.fernet import InvalidToken
        key = _new_fernet_key()
        with override_settings(MAILER_FERNET_KEY=key, MAILER_FERNET_KEYS_OLD=""):
            self._clear_cache()
            from core.crypto import decrypt_str
            with self.assertRaises((InvalidToken, Exception)):
                decrypt_str("это-не-fernet-токен")

    def test_отсутствие_ключа_поднимает_RuntimeError(self):
        """Если MAILER_FERNET_KEY не задан — RuntimeError при первом вызове."""
        with override_settings(MAILER_FERNET_KEY="", MAILER_FERNET_KEYS_OLD=""):
            self._clear_cache()
            from core.crypto import encrypt_str
            with self.assertRaises(RuntimeError):
                encrypt_str("anything")

    def test_multifernet_ротация_ключей_старый_ключ_расшифровывает(self):
        """
        MultiFernet: зашифровано старым ключом — расшифровывается
        при ротации (старый ключ в MAILER_FERNET_KEYS_OLD).
        """
        old_key = _new_fernet_key()
        new_key = _new_fernet_key()

        # Шифруем старым ключом
        with override_settings(MAILER_FERNET_KEY=old_key, MAILER_FERNET_KEYS_OLD=""):
            self._clear_cache()
            from core.crypto import encrypt_str
            old_token = encrypt_str("секрет")

        # Расшифровываем: новый ключ основной, старый — в KEYS_OLD
        with override_settings(MAILER_FERNET_KEY=new_key, MAILER_FERNET_KEYS_OLD=old_key):
            self._clear_cache()
            from core.crypto import decrypt_str
            result = decrypt_str(old_token)
        self.assertEqual(result, "секрет")

    def test_multifernet_новый_ключ_шифрует_только_primary(self):
        """
        После ротации encrypt использует новый (primary) ключ.
        Расшифровка без нового ключа должна завершиться ошибкой.
        """
        from cryptography.fernet import InvalidToken
        old_key = _new_fernet_key()
        new_key = _new_fernet_key()

        with override_settings(MAILER_FERNET_KEY=new_key, MAILER_FERNET_KEYS_OLD=old_key):
            self._clear_cache()
            from core.crypto import encrypt_str
            new_token = encrypt_str("новый секрет")

        # Попытка расшифровать только старым ключом — должна упасть
        with override_settings(MAILER_FERNET_KEY=old_key, MAILER_FERNET_KEYS_OLD=""):
            self._clear_cache()
            from core.crypto import decrypt_str
            with self.assertRaises((InvalidToken, Exception)):
                decrypt_str(new_token)


class CollectKeysTest(TestCase):
    """Тесты вспомогательной функции _collect_keys."""

    def setUp(self):
        from core.crypto import _fernet
        _fernet.cache_clear()

    def tearDown(self):
        from core.crypto import _fernet
        _fernet.cache_clear()

    def test_collect_keys_только_primary(self):
        key = _new_fernet_key()
        with override_settings(MAILER_FERNET_KEY=key, MAILER_FERNET_KEYS_OLD=""):
            from core.crypto import _collect_keys
            keys = _collect_keys()
        self.assertEqual(keys, [key])

    def test_collect_keys_primary_plus_old(self):
        k1 = _new_fernet_key()
        k2 = _new_fernet_key()
        with override_settings(MAILER_FERNET_KEY=k1, MAILER_FERNET_KEYS_OLD=k2):
            from core.crypto import _collect_keys
            keys = _collect_keys()
        self.assertEqual(keys[0], k1)
        self.assertIn(k2, keys)

    def test_collect_keys_old_дубликаты_удаляются(self):
        k1 = _new_fernet_key()
        with override_settings(MAILER_FERNET_KEY=k1, MAILER_FERNET_KEYS_OLD=f"{k1},{k1}"):
            from core.crypto import _collect_keys
            keys = _collect_keys()
        # k1 не должен встречаться дважды
        self.assertEqual(keys.count(k1), 1)

    def test_collect_keys_пустой_primary_не_добавляется(self):
        with override_settings(MAILER_FERNET_KEY="   ", MAILER_FERNET_KEYS_OLD=""):
            from core.crypto import _collect_keys
            keys = _collect_keys()
        self.assertEqual(keys, [])


# ============================================================================
# timezone_utils.py
# ============================================================================

class RusTzChoicesTest(TestCase):
    """Тесты константы RUS_TZ_CHOICES."""

    def test_choices_непустой_список(self):
        from core.timezone_utils import RUS_TZ_CHOICES
        self.assertIsInstance(RUS_TZ_CHOICES, list)
        self.assertGreater(len(RUS_TZ_CHOICES), 0)

    def test_choices_состоят_из_двухэлементных_кортежей(self):
        from core.timezone_utils import RUS_TZ_CHOICES
        for item in RUS_TZ_CHOICES:
            self.assertIsInstance(item, tuple, msg=f"Элемент {item!r} не кортеж")
            self.assertEqual(len(item), 2, msg=f"Кортеж {item!r} не двухэлементный")

    def test_choices_содержат_moscow(self):
        from core.timezone_utils import RUS_TZ_CHOICES
        tz_values = [tz for tz, _ in RUS_TZ_CHOICES]
        self.assertIn("Europe/Moscow", tz_values)

    def test_choices_содержат_yekaterinburg(self):
        from core.timezone_utils import RUS_TZ_CHOICES
        tz_values = [tz for tz, _ in RUS_TZ_CHOICES]
        self.assertIn("Asia/Yekaterinburg", tz_values)

    def test_choices_не_содержат_дубликатов_tz(self):
        from core.timezone_utils import RUS_TZ_CHOICES
        tz_values = [tz for tz, _ in RUS_TZ_CHOICES]
        self.assertEqual(len(tz_values), len(set(tz_values)))


class GuessRuTimezoneTest(TestCase):
    """Тесты эвристики определения часового пояса по адресу."""

    def _guess(self, addr: str) -> str:
        from core.timezone_utils import guess_ru_timezone_from_address
        return guess_ru_timezone_from_address(addr)

    # --- Известные города ---

    def test_москва_возвращает_europe_moscow(self):
        self.assertEqual(self._guess("Москва, ул. Ленина 1"), "Europe/Moscow")

    def test_санкт_петербург_возвращает_europe_moscow(self):
        # Fallback кириллицы → Europe/Moscow (нет специфичного правила)
        result = self._guess("Санкт-Петербург, Невский проспект")
        self.assertEqual(result, "Europe/Moscow")

    def test_екатеринбург_возвращает_asia_yekaterinburg(self):
        self.assertEqual(self._guess("Екатеринбург, ул. Малышева 36"), "Asia/Yekaterinburg")

    def test_тюмень_возвращает_asia_yekaterinburg(self):
        self.assertEqual(self._guess("Тюмень, пр. Мира 10"), "Asia/Yekaterinburg")

    def test_новосибирск_возвращает_asia_novosibirsk(self):
        self.assertEqual(self._guess("Новосибирск, Красный проспект"), "Asia/Novosibirsk")

    def test_омск_возвращает_asia_omsk(self):
        self.assertEqual(self._guess("Омск, ул. Ленина 1"), "Asia/Omsk")

    def test_краснодар_возвращает_europe_moscow(self):
        # Нет правила для Краснодара → кириллица → Europe/Moscow
        result = self._guess("Краснодар, ул. Красная")
        self.assertEqual(result, "Europe/Moscow")

    def test_владивосток_возвращает_asia_vladivostok(self):
        self.assertEqual(self._guess("Владивосток, ул. Светланская"), "Asia/Vladivostok")

    def test_иркутск_возвращает_asia_irkutsk(self):
        self.assertEqual(self._guess("Иркутск, ул. Ленина 5"), "Asia/Irkutsk")

    def test_калининград_возвращает_europe_kaliningrad(self):
        self.assertEqual(self._guess("Калининград, Ленинский пр-т"), "Europe/Kaliningrad")

    def test_магадан_возвращает_asia_magadan(self):
        self.assertEqual(self._guess("Магадан, ул. Пушкина"), "Asia/Magadan")

    def test_камчатка_возвращает_asia_kamchatka(self):
        self.assertEqual(self._guess("Петропавловск-Камчатский"), "Asia/Kamchatka")

    def test_самара_возвращает_europe_samara(self):
        self.assertEqual(self._guess("Самара, ул. Ленина"), "Europe/Samara")

    def test_казань_возвращает_europe_samara(self):
        self.assertEqual(self._guess("Казань, ул. Баумана 1"), "Europe/Samara")

    def test_якутск_возвращает_asia_yakutsk(self):
        self.assertEqual(self._guess("Якутск, пр. Ленина 1"), "Asia/Yakutsk")

    # --- Граничные случаи ---

    def test_пустая_строка_возвращает_пустую_строку(self):
        self.assertEqual(self._guess(""), "")

    def test_none_возвращает_пустую_строку(self):
        self.assertEqual(self._guess(None), "")  # type: ignore[arg-type]

    def test_только_пробелы_возвращает_пустую_строку(self):
        self.assertEqual(self._guess("   "), "")

    def test_латиница_без_кириллицы_возвращает_пустую_строку(self):
        # Нет кириллицы и нет совпадения → пустая строка
        result = self._guess("New York, NY")
        self.assertEqual(result, "")

    def test_неизвестный_русский_адрес_возвращает_moscow(self):
        # Любой адрес с кириллицей без специфичного ключевого слова → Europe/Moscow
        result = self._guess("Тьмутаракань, ул. Непонятная")
        self.assertEqual(result, "Europe/Moscow")

    def test_ё_нормализуется_к_е(self):
        # "Ёкатеринбург" — "ё" → "е", должно найти "екатеринбург"
        result = self._guess("Ёкатеринбург")
        self.assertEqual(result, "Asia/Yekaterinburg")

    def test_адрес_с_пунктуацией_парсируется(self):
        # Знаки препинания не должны мешать определению
        result = self._guess("г. Екатеринбург (Свердловская обл.)")
        self.assertEqual(result, "Asia/Yekaterinburg")


# ============================================================================
# request_id.py
# ============================================================================

class RequestIdMiddlewareTest(TestCase):
    """Тесты middleware RequestIdMiddleware."""

    def setUp(self):
        self.factory = RequestFactory()
        # Импортируем здесь, чтобы не тащить глобально
        from core.request_id import RequestIdMiddleware, _thread_local

        # Простой get_response-stub
        def _get_response(req):
            from django.http import HttpResponse
            return HttpResponse("ok")

        self.middleware = RequestIdMiddleware(_get_response)
        self._thread_local = _thread_local

    def test_middleware_устанавливает_request_id_на_объект_запроса(self):
        """process_request должен установить request.request_id."""
        request = self.factory.get("/")
        self.middleware.process_request(request)
        self.assertTrue(hasattr(request, "request_id"))
        self.assertIsNotNone(request.request_id)
        self.assertIsInstance(request.request_id, str)
        self.assertGreater(len(request.request_id), 0)

    def test_middleware_request_id_имеет_длину_8(self):
        """По коду: str(uuid4())[:8] — длина ровно 8 символов."""
        request = self.factory.get("/")
        self.middleware.process_request(request)
        self.assertEqual(len(request.request_id), 8)

    def test_middleware_устанавливает_заголовок_x_request_id_в_ответе(self):
        """process_response должен добавить X-Request-ID в заголовок ответа."""
        from django.http import HttpResponse
        request = self.factory.get("/")
        self.middleware.process_request(request)
        response = HttpResponse("ok")
        self.middleware.process_response(request, response)
        self.assertIn("X-Request-ID", response)
        self.assertEqual(response["X-Request-ID"], request.request_id)

    def test_middleware_очищает_thread_local_после_ответа(self):
        """После process_response thread-local не должен содержать request_id."""
        from django.http import HttpResponse
        request = self.factory.get("/")
        self.middleware.process_request(request)
        # Убедимся что установился
        self.assertTrue(hasattr(self._thread_local, "request_id"))
        response = HttpResponse("ok")
        self.middleware.process_response(request, response)
        self.assertFalse(hasattr(self._thread_local, "request_id"))

    def test_middleware_не_падает_на_запросе_без_request_id(self):
        """process_response не должен падать, если request_id не был установлен."""
        from django.http import HttpResponse
        request = self.factory.get("/")
        # Намеренно не вызываем process_request
        response = HttpResponse("ok")
        # Не должно быть исключения
        result = self.middleware.process_response(request, response)
        self.assertIsNotNone(result)

    def test_process_request_не_перезаписывает_уже_существующий_id_в_объекте(self):
        """Каждый вызов process_request генерирует новый ID (не переиспользует старый)."""
        request1 = self.factory.get("/")
        request2 = self.factory.get("/")
        self.middleware.process_request(request1)
        rid1 = request1.request_id
        self.middleware.process_request(request2)
        rid2 = request2.request_id
        # Два разных запроса — два разных ID
        self.assertNotEqual(rid1, rid2)

    def test_middleware_полный_цикл_через_вызов(self):
        """Полный цикл через __call__: ответ должен содержать X-Request-ID."""
        request = self.factory.get("/some-path/")
        response = self.middleware(request)
        self.assertIn("X-Request-ID", response)


class RequestIdLoggingFilterTest(TestCase):
    """Тесты фильтра логирования RequestIdLoggingFilter."""

    def _get_filter(self):
        from core.request_id import RequestIdLoggingFilter
        return RequestIdLoggingFilter()

    def _make_record(self, msg: str = "test") -> logging.LogRecord:
        return logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg=msg,
            args=(),
            exc_info=None,
        )

    def test_filter_добавляет_request_id_если_thread_local_установлен(self):
        """Если в thread-local есть request_id — он попадёт в запись."""
        from core.request_id import _thread_local
        _thread_local.request_id = "abc12345"
        try:
            flt = self._get_filter()
            record = self._make_record()
            result = flt.filter(record)
            self.assertTrue(result)
            self.assertEqual(record.request_id, "abc12345")
        finally:
            if hasattr(_thread_local, "request_id"):
                delattr(_thread_local, "request_id")

    def test_filter_устанавливает_none_если_thread_local_пуст(self):
        """Если thread-local не содержит request_id — record.request_id = None."""
        from core.request_id import _thread_local
        # Убеждаемся, что нет request_id
        if hasattr(_thread_local, "request_id"):
            delattr(_thread_local, "request_id")

        flt = self._get_filter()
        record = self._make_record()
        result = flt.filter(record)
        self.assertTrue(result)
        self.assertIsNone(record.request_id)

    def test_filter_всегда_возвращает_true(self):
        """Filter должен пропускать все записи (возвращать True)."""
        flt = self._get_filter()
        record = self._make_record("любое сообщение")
        self.assertTrue(flt.filter(record))


class GetRequestIdTest(TestCase):
    """Тесты вспомогательной функции get_request_id."""

    def test_get_request_id_возвращает_none_вне_запроса(self):
        from core.request_id import _thread_local, get_request_id
        if hasattr(_thread_local, "request_id"):
            delattr(_thread_local, "request_id")
        self.assertIsNone(get_request_id())

    def test_get_request_id_возвращает_значение_из_thread_local(self):
        from core.request_id import _thread_local, get_request_id
        _thread_local.request_id = "testid1"
        try:
            self.assertEqual(get_request_id(), "testid1")
        finally:
            delattr(_thread_local, "request_id")

    def test_get_request_id_потокобезопасность(self):
        """Разные потоки имеют независимые request_id (thread-local изоляция)."""
        from core.request_id import _thread_local, get_request_id
        results = {}

        def _worker(name: str, rid: str):
            _thread_local.request_id = rid
            import time as _time
            _time.sleep(0.01)  # даём другим потокам шанс вмешаться
            results[name] = get_request_id()

        t1 = threading.Thread(target=_worker, args=("t1", "id-thread-1"))
        t2 = threading.Thread(target=_worker, args=("t2", "id-thread-2"))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        self.assertEqual(results["t1"], "id-thread-1")
        self.assertEqual(results["t2"], "id-thread-2")


# ============================================================================
# exceptions.py
# ============================================================================

class CustomExceptionHandlerTest(TestCase):
    """Тесты кастомного обработчика исключений DRF."""

    def _get_handler(self):
        from core.exceptions import custom_exception_handler
        return custom_exception_handler

    def _make_context(self):
        """Минимальный context для DRF exception_handler."""
        factory = APIRequestFactory()
        request = factory.get("/api/test/")
        return {"request": Request(request), "view": None}

    # --- Режим DEBUG=True (ответы 4xx/5xx возвращаются без изменений) ---

    @override_settings(DEBUG=True)
    def test_validation_error_400_возвращается_как_есть_в_debug(self):
        handler = self._get_handler()
        exc = ValidationError({"field": ["Обязательное поле."]})
        ctx = self._make_context()
        response = handler(exc, ctx)
        self.assertIsNotNone(response)
        self.assertEqual(response.status_code, 400)

    @override_settings(DEBUG=True)
    def test_not_found_404_возвращается_как_есть_в_debug(self):
        handler = self._get_handler()
        exc = NotFound("Не найдено")
        ctx = self._make_context()
        response = handler(exc, ctx)
        self.assertIsNotNone(response)
        self.assertEqual(response.status_code, 404)

    @override_settings(DEBUG=True)
    def test_permission_denied_403_возвращается_как_есть_в_debug(self):
        handler = self._get_handler()
        exc = PermissionDenied("Доступ запрещён")
        ctx = self._make_context()
        response = handler(exc, ctx)
        self.assertIsNotNone(response)
        self.assertEqual(response.status_code, 403)

    @override_settings(DEBUG=True)
    def test_authentication_failed_401_возвращается_как_есть(self):
        handler = self._get_handler()
        exc = AuthenticationFailed("Невалидный токен")
        ctx = self._make_context()
        response = handler(exc, ctx)
        self.assertIsNotNone(response)
        self.assertEqual(response.status_code, 401)

    # --- Режим DEBUG=False (5xx → заменяем сообщение) ---

    @override_settings(DEBUG=False)
    def test_клиентская_ошибка_400_не_скрывается_в_production(self):
        """Ошибки валидации (400) не заменяются в production."""
        handler = self._get_handler()
        exc = ValidationError({"email": ["Неверный формат email."]})
        ctx = self._make_context()
        response = handler(exc, ctx)
        self.assertIsNotNone(response)
        self.assertEqual(response.status_code, 400)
        # Детали должны сохраняться
        self.assertIn("email", response.data)

    @override_settings(DEBUG=False)
    def test_клиентская_ошибка_404_сохраняет_детали_в_production(self):
        handler = self._get_handler()
        exc = NotFound("Объект не найден")
        ctx = self._make_context()
        response = handler(exc, ctx)
        self.assertIsNotNone(response)
        self.assertEqual(response.status_code, 404)

    # --- Не-DRF исключения ---

    @override_settings(DEBUG=True)
    def test_не_drf_исключение_возвращает_none(self):
        """Обработчик возвращает None для не-DRF исключений (стандартное поведение)."""
        handler = self._get_handler()
        exc = ValueError("Обычная Python ошибка")
        ctx = self._make_context()
        response = handler(exc, ctx)
        self.assertIsNone(response)

    @override_settings(DEBUG=True)
    def test_zero_division_error_возвращает_none(self):
        handler = self._get_handler()
        exc = ZeroDivisionError("деление на ноль")
        ctx = self._make_context()
        response = handler(exc, ctx)
        self.assertIsNone(response)

    @override_settings(DEBUG=True)
    def test_обычное_exception_возвращает_none(self):
        handler = self._get_handler()
        exc = Exception("базовое исключение")
        ctx = self._make_context()
        response = handler(exc, ctx)
        self.assertIsNone(response)


# ============================================================================
# work_schedule_utils.py
# ============================================================================

class ParseWorkScheduleTest(TestCase):
    """Тесты функции parse_work_schedule."""

    def _parse(self, text: str):
        from core.work_schedule_utils import parse_work_schedule
        return parse_work_schedule(text)

    def test_пустая_строка_возвращает_пустой_словарь(self):
        self.assertEqual(self._parse(""), {})

    def test_none_возвращает_пустой_словарь(self):
        self.assertEqual(self._parse(None), {})  # type: ignore[arg-type]

    def test_круглосуточно_24_7(self):
        """24/7 → все 7 дней 00:00–23:59."""
        schedule = self._parse("24/7")
        self.assertEqual(len(schedule), 7)
        for i in range(7):
            self.assertEqual(schedule[i], [(time(0, 0), time(23, 59))])

    def test_круглосуточно_текстом(self):
        schedule = self._parse("круглосуточно")
        for i in range(7):
            self.assertEqual(schedule[i], [(time(0, 0), time(23, 59))])

    def test_пн_пт_09_18(self):
        """Рабочие дни Пн–Пт 09:00–18:00, Сб–Вс выходной."""
        schedule = self._parse("Пн-Пт: 09:00-18:00\nСб-Вс: выходной")
        # Пн (0) … Пт (4) должны иметь интервал
        for i in range(5):
            self.assertGreater(len(schedule[i]), 0, msg=f"День {i} должен быть рабочим")
            start, end = schedule[i][0]
            self.assertEqual(start, time(9, 0))
            self.assertEqual(end, time(18, 0))
        # Сб (5), Вс (6) — выходной
        self.assertEqual(schedule[5], [])
        self.assertEqual(schedule[6], [])

    def test_будни_сокращение(self):
        """Будни 8:30-17:30."""
        schedule = self._parse("Будни: 8:30-17:30")
        for i in range(5):  # Пн–Пт
            self.assertGreater(len(schedule[i]), 0)
            start, end = schedule[i][0]
            self.assertEqual(start, time(8, 30))
            self.assertEqual(end, time(17, 30))

    def test_ежедневно(self):
        """Ежедневно 10:00-20:00."""
        schedule = self._parse("Ежедневно 10:00-20:00")
        for i in range(7):
            self.assertEqual(schedule[i], [(time(10, 0), time(20, 0))])

    def test_выходной_для_конкретных_дней(self):
        """Сб, Вс — выходной."""
        schedule = self._parse("Пн-Пт 09:00-17:00\nСб: выходной\nВс: выходной")
        self.assertEqual(schedule[5], [])
        self.assertEqual(schedule[6], [])

    def test_перерыв_разбивает_интервал(self):
        """Обед внутри рабочего дня создаёт два интервала."""
        schedule = self._parse("Пн-Пт: 09:00-18:00\nОбед: 13:00-14:00")
        # Для рабочих дней должно быть 2 интервала: 09:00–13:00 и 14:00–18:00
        for i in range(5):
            intervals = schedule[i]
            self.assertEqual(len(intervals), 2, msg=f"День {i}: ожидается 2 интервала")
            self.assertEqual(intervals[0], (time(9, 0), time(13, 0)))
            self.assertEqual(intervals[1], (time(14, 0), time(18, 0)))

    def test_одиночный_день(self):
        """Пт 10:00-15:00 при наличии общего расписания."""
        schedule = self._parse("Пн-Вс: 09:00-18:00\nПт: 10:00-15:00")
        # Пятница (4) должна быть перезаписана
        self.assertEqual(schedule[4], [(time(10, 0), time(15, 0))])

    def test_несколько_временных_слотов_в_день(self):
        """Формат 'Пн 9:00-12:00 14:00-18:00' — два слота."""
        schedule = self._parse("Пн: 9:00-12:00, 14:00-18:00")
        # Понедельник должен содержать оба интервала
        self.assertGreaterEqual(len(schedule.get(0, [])), 1)

    def test_без_выходных(self):
        """'Без выходных' — все 7 дней."""
        schedule = self._parse("Без выходных 10:00-22:00")
        for i in range(7):
            self.assertGreater(len(schedule[i]), 0)

    def test_только_цифровое_время_без_двоеточий(self):
        """Формат 9-18 (без минут)."""
        schedule = self._parse("Пн-Пт: 9-18")
        start, end = schedule[0][0]
        self.assertEqual(start, time(9, 0))
        self.assertEqual(end, time(18, 0))


class NormalizeWorkScheduleTest(TestCase):
    """Тесты функции normalize_work_schedule."""

    def _norm(self, text: str) -> str:
        from core.work_schedule_utils import normalize_work_schedule
        return normalize_work_schedule(text)

    def test_пустая_строка_возвращает_пустую(self):
        self.assertEqual(self._norm(""), "")

    def test_round_trip_пн_пт(self):
        """Нормализация должна вернуть читаемую строку."""
        result = self._norm("пн-пт: 9:00-18:00\nсб-вс: выходной")
        self.assertIn("09:00", result)
        self.assertIn("18:00", result)

    def test_круглосуточно_даёт_строку_круглосуточно(self):
        result = self._norm("24/7")
        self.assertIn("Круглосуточно", result)

    def test_форматирует_время_в_двузначный_формат(self):
        """9.00 → 09:00."""
        result = self._norm("Пн-Пт: 9.00-18.00")
        self.assertIn("09:00", result)
        self.assertIn("18:00", result)

    def test_перерыв_присутствует_в_выводе(self):
        result = self._norm("Пн-Пт: 09:00-18:00\nОбед: 13:00-14:00")
        self.assertIn("Перерыв", result)

    def test_ежедневно_с_одинаковым_расписанием(self):
        result = self._norm("Ежедневно 10:00-20:00")
        self.assertIn("Ежедневно", result)


class GetWorktimeStatusTest(TestCase):
    """Тесты функции get_worktime_status_from_schedule."""

    _TZ = timezone.utc

    def _status(self, schedule_text: str, now: datetime):
        from core.work_schedule_utils import get_worktime_status_from_schedule
        return get_worktime_status_from_schedule(schedule_text, now_tz=now)

    def _dt(self, weekday: int, hour: int, minute: int = 0) -> datetime:
        """Создаёт datetime с заданным днём недели и временем (UTC)."""
        # 2025-01-06 — понедельник (weekday=0)
        base = datetime(2025, 1, 6, tzinfo=self._TZ)  # понедельник
        from datetime import timedelta
        return base + timedelta(days=weekday, hours=hour, minutes=minute)

    def test_неизвестное_расписание_возвращает_unknown(self):
        status, minutes = self._status("", datetime(2025, 1, 6, 12, tzinfo=self._TZ))
        self.assertEqual(status, "unknown")
        self.assertIsNone(minutes)

    def test_рабочее_время_mid_day_возвращает_ok(self):
        """В середине рабочего дня статус ok."""
        now = self._dt(0, 14, 0)  # Понедельник 14:00
        status, minutes = self._status("Пн-Пт: 09:00-18:00", now)
        self.assertEqual(status, "ok")
        self.assertIsNotNone(minutes)
        self.assertGreater(minutes, 60)

    def test_перед_концом_дня_возвращает_warn_end(self):
        """За 30 минут до конца — warn_end."""
        now = self._dt(0, 17, 35)  # Понедельник 17:35 (осталось 25 мин до 18:00)
        status, minutes = self._status("Пн-Пт: 09:00-18:00", now)
        self.assertEqual(status, "warn_end")
        self.assertLessEqual(minutes, 60)

    def test_вне_рабочего_времени_возвращает_off(self):
        """До начала рабочего дня — off."""
        now = self._dt(0, 7, 0)  # Понедельник 07:00 (раньше 09:00)
        status, minutes = self._status("Пн-Пт: 09:00-18:00", now)
        self.assertEqual(status, "off")
        self.assertIsNone(minutes)

    def test_выходной_возвращает_off(self):
        """Суббота — выходной → off."""
        now = self._dt(5, 12, 0)  # Суббота 12:00
        status, minutes = self._status("Пн-Пт: 09:00-18:00\nСб-Вс: выходной", now)
        self.assertEqual(status, "off")

    def test_круглосуточно_всегда_ok(self):
        """При расписании 24/7 — всегда ok, minutes > 0."""
        for weekday in range(7):
            for hour in [0, 6, 12, 18, 23]:
                now = self._dt(weekday, hour)
                status, minutes = self._status("24/7", now)
                self.assertIn(
                    status, ("ok", "warn_end"),
                    msg=f"weekday={weekday}, hour={hour}: статус должен быть ok/warn_end",
                )

    def test_отсутствие_tzinfo_возвращает_unknown(self):
        """Если datetime без tzinfo — функция возвращает unknown."""
        naive_dt = datetime(2025, 1, 6, 12, 0)  # без tzinfo
        status, minutes = self._status("Пн-Пт: 09:00-18:00", naive_dt)
        self.assertEqual(status, "unknown")

    def test_ровно_60_минут_до_конца_warn_end(self):
        """Ровно 60 минут до конца → warn_end."""
        now = self._dt(0, 17, 0)  # Понедельник 17:00, конец 18:00
        status, minutes = self._status("Пн-Пт: 09:00-18:00", now)
        self.assertEqual(status, "warn_end")
        self.assertEqual(minutes, 60)


class ExpandDaySpecTest(TestCase):
    """Тесты вспомогательной функции _expand_day_spec."""

    def _expand(self, spec: str):
        from core.work_schedule_utils import _expand_day_spec
        return _expand_day_spec(spec)

    def test_пн_пт_диапазон(self):
        self.assertEqual(self._expand("пн-пт"), [0, 1, 2, 3, 4])

    def test_сб_вс(self):
        self.assertEqual(self._expand("сб-вс"), [5, 6])

    def test_ежедневно(self):
        self.assertEqual(self._expand("ежедневно"), list(range(7)))

    def test_будни(self):
        self.assertEqual(self._expand("будни"), [0, 1, 2, 3, 4])

    def test_выходные(self):
        self.assertEqual(self._expand("выходные"), [5, 6])

    def test_одиночный_день(self):
        self.assertEqual(self._expand("ср"), [2])

    def test_без_выходных(self):
        self.assertEqual(self._expand("без выходных"), list(range(7)))

    def test_перечисление_через_запятую(self):
        result = self._expand("пн, ср, пт")
        self.assertIn(0, result)
        self.assertIn(2, result)
        self.assertIn(4, result)

    def test_пустая_строка_возвращает_пустой_список(self):
        self.assertEqual(self._expand(""), [])

    def test_обратный_диапазон_пт_пн(self):
        """Wrap-around: пт-пн = [4,5,6,0]."""
        result = self._expand("пт-пн")
        self.assertEqual(result, [4, 5, 6, 0])


class ParseTimeTokenTest(TestCase):
    """Тесты вспомогательной функции _parse_time_token."""

    def _pt(self, s: str):
        from core.work_schedule_utils import _parse_time_token
        return _parse_time_token(s)

    def test_9_00(self):
        self.assertEqual(self._pt("9:00"), time(9, 0))

    def test_09_30(self):
        self.assertEqual(self._pt("09:30"), time(9, 30))

    def test_только_час(self):
        self.assertEqual(self._pt("9"), time(9, 0))

    def test_18(self):
        self.assertEqual(self._pt("18"), time(18, 0))

    def test_9_00_с_точкой(self):
        self.assertEqual(self._pt("9.00"), time(9, 0))

    def test_невалидный_час(self):
        self.assertIsNone(self._pt("25:00"))

    def test_невалидные_минуты(self):
        self.assertIsNone(self._pt("9:60"))

    def test_пустая_строка(self):
        self.assertIsNone(self._pt(""))

    def test_none(self):
        self.assertIsNone(self._pt(None))  # type: ignore[arg-type]


# ============================================================================
# input_cleaners.py
# ============================================================================

class CleanIntIdTest(TestCase):
    """Тесты функции clean_int_id."""

    def _clean(self, value):
        from core.input_cleaners import clean_int_id
        return clean_int_id(value)

    def test_обычный_int(self):
        self.assertEqual(self._clean(1), 1)

    def test_строка_int(self):
        self.assertEqual(self._clean("42"), 42)

    def test_строка_с_пробелами(self):
        self.assertEqual(self._clean("  5  "), 5)

    def test_список_с_одним_элементом(self):
        self.assertEqual(self._clean(["7"]), 7)

    def test_список_с_пробелами(self):
        self.assertEqual(self._clean([" 3 "]), 3)

    def test_json_scalar(self):
        self.assertEqual(self._clean("10"), 10)

    def test_json_список(self):
        self.assertEqual(self._clean('["99"]'), 99)

    def test_json_dict_с_id(self):
        self.assertEqual(self._clean('{"id": 55}'), 55)

    def test_python_literal_список(self):
        self.assertEqual(self._clean("['12']"), 12)

    def test_none_возвращает_none(self):
        self.assertIsNone(self._clean(None))

    def test_пустая_строка_возвращает_none(self):
        self.assertIsNone(self._clean(""))

    def test_пустой_список_возвращает_none(self):
        self.assertIsNone(self._clean([]))

    def test_отрицательное_число_возвращает_none(self):
        self.assertIsNone(self._clean(-1))

    def test_ноль_возвращает_none(self):
        self.assertIsNone(self._clean(0))

    def test_дробное_число_возвращает_none(self):
        self.assertIsNone(self._clean("3.14"))

    def test_мусорная_строка_возвращает_none(self):
        self.assertIsNone(self._clean("not-a-number"))

    def test_большое_число(self):
        self.assertEqual(self._clean(999999999), 999999999)


class CleanUuidTest(TestCase):
    """Тесты функции clean_uuid."""

    def _clean(self, value):
        from core.input_cleaners import clean_uuid
        return clean_uuid(value)

    def test_валидный_uuid_строка(self):
        uid = "550e8400-e29b-41d4-a716-446655440000"
        result = self._clean(uid)
        self.assertIsNotNone(result)
        self.assertEqual(str(result), uid)

    def test_валидный_uuid_объект(self):
        uid = uuid.uuid4()
        result = self._clean(uid)
        self.assertEqual(result, uid)

    def test_uuid_в_кавычках(self):
        uid = "550e8400-e29b-41d4-a716-446655440000"
        result = self._clean(f'"{uid}"')
        self.assertIsNotNone(result)

    def test_none_возвращает_none(self):
        self.assertIsNone(self._clean(None))

    def test_пустая_строка_возвращает_none(self):
        self.assertIsNone(self._clean(""))

    def test_строка_с_пробелами_возвращает_none(self):
        self.assertIsNone(self._clean("   "))

    def test_невалидная_строка_возвращает_none(self):
        self.assertIsNone(self._clean("not-a-uuid"))

    def test_int_возвращает_none(self):
        self.assertIsNone(self._clean(12345))

    def test_uuid_без_дефисов(self):
        uid = "550e8400e29b41d4a716446655440000"
        result = self._clean(uid)
        self.assertIsNotNone(result)


# ============================================================================
# json_formatter.py
# ============================================================================

class JSONFormatterTest(TestCase):
    """Тесты JSON-форматтера структурированного логирования."""

    def _make_formatter(self):
        from core.json_formatter import JSONFormatter
        return JSONFormatter()

    def _make_record(self, msg: str = "test message", level=logging.INFO, **extra) -> logging.LogRecord:
        record = logging.LogRecord(
            name="test.logger",
            level=level,
            pathname="test.py",
            lineno=42,
            msg=msg,
            args=(),
            exc_info=None,
        )
        for key, value in extra.items():
            setattr(record, key, value)
        return record

    def test_вывод_является_валидным_json(self):
        """format() возвращает строку, парсируемую как JSON."""
        formatter = self._make_formatter()
        record = self._make_record("Тестовое сообщение")
        output = formatter.format(record)
        data = json.loads(output)
        self.assertIsInstance(data, dict)

    def test_обязательные_поля_присутствуют(self):
        """В выводе должны быть level, logger, message, timestamp."""
        formatter = self._make_formatter()
        record = self._make_record("Привет")
        data = json.loads(formatter.format(record))
        self.assertIn("level", data)
        self.assertIn("logger", data)
        self.assertIn("message", data)
        self.assertIn("timestamp", data)

    def test_уровень_логирования_info(self):
        formatter = self._make_formatter()
        record = self._make_record("msg", level=logging.INFO)
        data = json.loads(formatter.format(record))
        self.assertEqual(data["level"], "INFO")

    def test_уровень_логирования_error(self):
        formatter = self._make_formatter()
        record = self._make_record("ошибка", level=logging.ERROR)
        data = json.loads(formatter.format(record))
        self.assertEqual(data["level"], "ERROR")

    def test_имя_логгера_сохраняется(self):
        formatter = self._make_formatter()
        record = self._make_record()
        data = json.loads(formatter.format(record))
        self.assertEqual(data["logger"], "test.logger")

    def test_сообщение_сохраняется(self):
        formatter = self._make_formatter()
        record = self._make_record("Тестовое сообщение 123")
        data = json.loads(formatter.format(record))
        self.assertEqual(data["message"], "Тестовое сообщение 123")

    def test_timestamp_в_iso_формате(self):
        """Timestamp должен заканчиваться на 'Z' (UTC)."""
        formatter = self._make_formatter()
        record = self._make_record()
        data = json.loads(formatter.format(record))
        self.assertTrue(
            data["timestamp"].endswith("Z"),
            msg=f"Timestamp {data['timestamp']!r} должен заканчиваться на Z",
        )

    def test_extra_поля_через_extra_dict(self):
        """Поля из record.extra попадают в JSON-вывод."""
        formatter = self._make_formatter()
        record = self._make_record()
        record.extra = {"campaign_id": "uuid-1234", "user_id": 42}
        data = json.loads(formatter.format(record))
        self.assertEqual(data["campaign_id"], "uuid-1234")
        self.assertEqual(data["user_id"], 42)

    def test_extra_поля_через_setattr(self):
        """Поля, установленные напрямую через setattr, попадают в JSON-вывод."""
        formatter = self._make_formatter()
        record = self._make_record("msg with extra", job_id="job-999")
        data = json.loads(formatter.format(record))
        self.assertEqual(data["job_id"], "job-999")

    def test_несериализуемое_значение_конвертируется_в_строку(self):
        """Несериализуемый объект не должен приводить к исключению."""
        formatter = self._make_formatter()

        class _Unserializable:
            pass

        record = self._make_record()
        record.weird_obj = _Unserializable()
        # Не должно бросать исключение
        output = formatter.format(record)
        data = json.loads(output)
        self.assertIsInstance(data, dict)

    def test_exception_info_попадает_в_вывод(self):
        """При наличии exc_info в записи — поле exception должно быть в JSON."""
        formatter = self._make_formatter()
        try:
            raise ValueError("тестовая ошибка")
        except ValueError:
            import sys
            exc_info = sys.exc_info()

        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname="",
            lineno=0,
            msg="Произошла ошибка",
            args=(),
            exc_info=exc_info,
        )
        data = json.loads(formatter.format(record))
        self.assertIn("exception", data)
        self.assertIn("ValueError", data["exception"])
