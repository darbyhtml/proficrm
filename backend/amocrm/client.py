from __future__ import annotations

import json
import logging
import os
import random
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

from django.utils import timezone

from ui.models import AmoApiConfig

logger = logging.getLogger(__name__)


class AmoApiError(RuntimeError):
    pass


class RateLimitError(AmoApiError):
    """Исключение при исчерпании попыток retry для rate limit (429)"""
    pass


def _now_ts() -> int:
    return int(time.time())


def _json_loads(b: bytes) -> Any:
    try:
        return json.loads(b.decode("utf-8", errors="replace"))
    except Exception:
        return None


@dataclass
class AmoResponse:
    status: int
    data: Any
    headers: dict[str, str]


class AmoClient:
    # Rate limiting: максимум 7 запросов в секунду (0.143 сек между запросами)
    # Используем 0.143 сек для максимальной скорости при соблюдении лимита
    MIN_REQUEST_INTERVAL = 1.0 / 7.0  # ~0.143 секунды между запросами (7 запросов/сек)
    
    def __init__(self, cfg: AmoApiConfig):
        self.cfg = cfg
        self._last_request_time: float = 0.0  # время последнего запроса
        self._request_count: int = 0  # счетчик API-запросов
        self._start_time: float = time.time()  # время создания клиента для метрик

    @property
    def base(self) -> str:
        dom = (self.cfg.domain or "").strip()
        dom = dom.replace("https://", "").replace("http://", "").strip("/")
        if not dom:
            raise AmoApiError("amoCRM domain is not set")
        return f"https://{dom}"

    def _token_valid(self) -> bool:
        # Long-lived token не истекает (для целей миграции)
        if self.cfg.long_lived_token:
            return True
        if not self.cfg.access_token:
            return False
        if not self.cfg.expires_at:
            return True
        # refresh заранее, чтобы не ловить race
        return self.cfg.expires_at > (timezone.now() + timezone.timedelta(seconds=60))

    def refresh_token(self) -> None:
        if not self.cfg.refresh_token:
            raise AmoApiError("refresh_token is empty")
        url = f"{self.base}/oauth2/access_token"
        payload = {
            "client_id": self.cfg.client_id,
            "client_secret": self.cfg.client_secret,
            "grant_type": "refresh_token",
            "refresh_token": self.cfg.refresh_token,
            "redirect_uri": self.cfg.redirect_uri,
        }
        res = self._request("POST", url, json_body=payload, auth=False)
        if res.status >= 400:
            raise AmoApiError(f"Token refresh failed ({res.status}): {res.data}")
        data = res.data or {}
        self.cfg.access_token = str(data.get("access_token") or "")
        self.cfg.refresh_token = str(data.get("refresh_token") or self.cfg.refresh_token)
        self.cfg.token_type = str(data.get("token_type") or "Bearer")
        expires_in = int(data.get("expires_in") or 0)
        if expires_in:
            self.cfg.expires_at = timezone.now() + timezone.timedelta(seconds=expires_in)
        self.cfg.last_error = ""
        self.cfg.save(update_fields=["access_token", "refresh_token", "token_type", "expires_at", "last_error", "updated_at"])

    def ensure_token(self) -> None:
        if self.cfg.long_lived_token:
            if not self.cfg.long_lived_token.strip():
                raise AmoApiError("Long-lived token пустой. Проверьте настройки AmoCRM в админке.")
            return
        if not self.cfg.access_token:
            raise AmoApiError("Access token не настроен. Проверьте настройки AmoCRM в админке или переавторизуйтесь.")
        if self._token_valid():
            return
        self.refresh_token()

    def authorize_url(self) -> str:
        # amo: https://www.amocrm.ru/oauth?client_id=...&state=...&mode=popup
        # Согласно документации AmoCRM: https://www.amocrm.ru/developers/content/oauth/step-by-step
        # Правильный endpoint: https://www.amocrm.ru/oauth (не поддомен!)
        # Параметры: client_id, state, mode (redirect_uri и response_type НЕ нужны в URL)
        # redirect_uri указывается только при регистрации интеграции и при обмене кода на токен
        qs = urllib.parse.urlencode(
            {
                "client_id": self.cfg.client_id,
                "state": "proficrm_migrate",  # для CSRF защиты
                "mode": "popup",  # или "post_message" - контролирует поведение UI
            }
        )
        # Важно: используем www.amocrm.ru, а не поддомен пользователя
        return f"https://www.amocrm.ru/oauth?{qs}"

    def exchange_code(self, code: str) -> None:
        url = f"{self.base}/oauth2/access_token"
        payload = {
            "client_id": self.cfg.client_id,
            "client_secret": self.cfg.client_secret,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.cfg.redirect_uri,
        }
        
        # Логируем запрос (без секретов)
        logger.info(f"Exchanging OAuth code. URL: {url}, Client ID: {self.cfg.client_id[:10]}..., Redirect URI: {self.cfg.redirect_uri}")
        
        res = self._request("POST", url, json_body=payload, auth=False)
        if res.status >= 400:
            error_details = f"Token exchange failed ({res.status}): {res.data}"
            logger.error(f"{error_details}. URL: {url}, Redirect URI: {self.cfg.redirect_uri}")
            raise AmoApiError(error_details)
        data = res.data or {}
        self.cfg.access_token = str(data.get("access_token") or "")
        self.cfg.refresh_token = str(data.get("refresh_token") or "")
        self.cfg.token_type = str(data.get("token_type") or "Bearer")
        expires_in = int(data.get("expires_in") or 0)
        self.cfg.expires_at = timezone.now() + timezone.timedelta(seconds=expires_in) if expires_in else None
        self.cfg.last_error = ""
        self.cfg.save(update_fields=["access_token", "refresh_token", "token_type", "expires_at", "last_error", "updated_at"])
        logger.info("OAuth token exchange successful")

    def _request(self, method: str, url: str, *, params: dict[str, Any] | None = None, json_body: Any | None = None, auth: bool = True) -> AmoResponse:
        # Rate limiting: соблюдаем интервал между запросами
        current_time = time.time()
        time_since_last = current_time - self._last_request_time
        if time_since_last < self.MIN_REQUEST_INTERVAL:
            sleep_time = self.MIN_REQUEST_INTERVAL - time_since_last
            time.sleep(sleep_time)
        self._last_request_time = time.time()
        
        # Увеличиваем счетчик запросов (только для авторизованных запросов к API)
        if auth:
            self._request_count += 1
        
        if params:
            # AmoCRM API требует специальный формат для массивов: filter[id][]=1&filter[id][]=2
            # urllib.parse.urlencode не поддерживает это напрямую, поэтому обрабатываем вручную
            query_parts = []
            for key, value in params.items():
                if value is None:
                    continue
                # Если значение - список, формируем параметры для каждого элемента
                if isinstance(value, list):
                    for item in value:
                        if item is not None:
                            # Для ключей вида "filter[id]" формируем "filter[id][]"
                            # Для остальных ключей просто повторяем ключ
                            if "[" in str(key) and "]" in str(key):
                                # Ключ уже содержит [], добавляем еще []
                                encoded_key = urllib.parse.quote(str(key) + "[]", safe='[]=')
                            else:
                                # Обычный ключ, просто повторяем
                                encoded_key = urllib.parse.quote(str(key), safe='[]=')
                            encoded_value = urllib.parse.quote(str(item), safe='')
                            query_parts.append(f"{encoded_key}={encoded_value}")
                else:
                    # Обычный параметр
                    encoded_key = urllib.parse.quote(str(key), safe='[]=')
                    encoded_value = urllib.parse.quote(str(value), safe='')
                    query_parts.append(f"{encoded_key}={encoded_value}")
            qs = "&".join(query_parts)
            url = url + ("&" if "?" in url else "?") + qs

        headers: dict[str, str] = {"Accept": "application/json"}
        data_bytes = None
        if json_body is not None:
            headers["Content-Type"] = "application/json"
            data_bytes = json.dumps(json_body).encode("utf-8")

        if auth:
            self.ensure_token()
            tok = self.cfg.long_lived_token or self.cfg.access_token
            ttype = self.cfg.token_type or "Bearer"
            headers["Authorization"] = f"{ttype} {tok}"

        req = urllib.request.Request(url=url, method=method.upper(), headers=headers, data=data_bytes)
        try:
            # Поддержка прокси для обхода блокировки IP
            # Можно настроить через переменные окружения: HTTP_PROXY, HTTPS_PROXY
            # Или через настройки AmoApiConfig (если добавим поле proxy_url)
            proxy_handler = None
            proxy_url = getattr(self.cfg, 'proxy_url', None) or None
            if proxy_url:
                proxy_handler = urllib.request.ProxyHandler({
                    'http': proxy_url,
                    'https': proxy_url,
                })
            elif os.getenv('HTTP_PROXY') or os.getenv('HTTPS_PROXY'):
                # Используем системные переменные окружения
                proxy_handler = urllib.request.ProxyHandler()
            
            opener = urllib.request.build_opener(proxy_handler) if proxy_handler else urllib.request.build_opener()
            
            with opener.open(req, timeout=15) as resp:  # уменьшили таймаут с 30 до 15 сек
                raw = resp.read() or b""
                data = _json_loads(raw)
                return AmoResponse(status=int(resp.status), data=data, headers={k.lower(): v for k, v in resp.headers.items()})
        except urllib.error.HTTPError as e:  # type: ignore[attr-defined]
            raw = e.read() or b""
            return AmoResponse(status=int(getattr(e, "code", 500) or 500), data=_json_loads(raw) or raw.decode("utf-8", errors="replace"), headers={})
        except Exception as e:
            raise AmoApiError(str(e))

    def get(self, path: str, *, params: dict[str, Any] | None = None, retry_on_429: bool = True) -> Any:
        """
        Выполняет GET запрос с обработкой rate limit (429) и retry логикой.
        
        При 429:
        - Использует Retry-After header если есть (с лимитом 60s)
        - Иначе экспоненциальный backoff с jitter
        - Максимум 8 попыток
        - Если все попытки исчерпаны - поднимает RateLimitError
        """
        url = f"{self.base}{path}"
        max_retries = 8  # Увеличиваем количество попыток
        base_delay = 0.5  # Базовая задержка в секундах
        max_retry_after = 60  # Максимальное значение Retry-After (секунды)
        jitter_range = 0.25  # ±25% jitter
        
        for attempt in range(max_retries):
            res = self._request("GET", url, params=params, auth=True)
            
            # Обработка 401 - обновляем токен и повторяем
            if res.status == 401:
                logger.warning(f"Unauthorized (401) для {path}, обновляем токен...")
                self.refresh_token()
                res = self._request("GET", url, params=params, auth=True)
            
            # Обработка 403 - Forbidden (проблемы с правами доступа или токеном)
            if res.status == 403:
                error_msg = f"403 Forbidden для {path}"
                # Проверяем наличие токена
                has_token = bool(self.cfg.long_lived_token or self.cfg.access_token)
                if not has_token:
                    error_msg += ". Токен не настроен. Проверьте настройки AmoCRM в админке."
                else:
                    error_msg += ". Возможные причины:\n"
                    error_msg += "  - Недостаточно прав доступа у пользователя в AmoCRM\n"
                    error_msg += "  - Токен недействителен или истек (попробуйте переавторизоваться)\n"
                    error_msg += "  - IP адрес заблокирован\n"
                    error_msg += "  - Неправильный домен AmoCRM\n"
                    error_msg += f"  - Домен: {self.cfg.domain}\n"
                    error_msg += f"  - Используется long_lived_token: {bool(self.cfg.long_lived_token)}"
                logger.error(error_msg)
                raise AmoApiError(error_msg)
            
            # Обработка 429 - Too Many Requests (rate limit)
            if res.status == 429 and retry_on_429:
                if attempt >= max_retries - 1:
                    # Все попытки исчерпаны - поднимаем исключение
                    error_msg = f"GET {path} failed (429): Rate limit exceeded after {max_retries} attempts"
                    logger.error(f"{error_msg}. Получено элементов: 0")
                    raise RateLimitError(error_msg)
                
                # Определяем задержку: сначала проверяем Retry-After header
                delay = None
                retry_after_header = res.headers.get("retry-after") or res.headers.get("Retry-After")
                if retry_after_header:
                    try:
                        delay = int(float(retry_after_header))
                        # Ограничиваем максимальное значение
                        delay = min(delay, max_retry_after)
                        logger.info(f"Rate limit (429) для {path}: используем Retry-After={delay}s")
                    except (ValueError, TypeError):
                        pass
                
                # Если Retry-After не указан или невалиден - используем экспоненциальный backoff
                if delay is None:
                    delay = base_delay * (2 ** attempt)
                
                # Добавляем jitter (±25%)
                jitter = delay * jitter_range * (2 * random.random() - 1)  # от -25% до +25%
                delay = max(0.1, delay + jitter)  # Минимум 0.1 секунды
                
                logger.warning(
                    f"Rate limit (429) для {path}, повтор через {delay:.2f}s "
                    f"(попытка {attempt + 1}/{max_retries}, endpoint={path})"
                )
                time.sleep(delay)
                continue
            
            # Обработка 5xx ошибок - тоже делаем retry с backoff
            if res.status >= 500 and attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt)
                jitter = delay * jitter_range * (2 * random.random() - 1)
                delay = max(0.1, delay + jitter)
                logger.warning(
                    f"Server error ({res.status}) для {path}, повтор через {delay:.2f}s "
                    f"(попытка {attempt + 1}/{max_retries})"
                )
                time.sleep(delay)
                continue
            
            if res.status >= 400:
                raise AmoApiError(f"GET {path} failed ({res.status}): {res.data}")
            
            return res.data
        
        # Если все попытки исчерпаны (для 5xx)
        raise AmoApiError(f"GET {path} failed: Max retries ({max_retries}) exceeded")

    def get_all_pages(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        limit: int = 50,  # Оптимальный размер страницы: не слишком большой (504), не слишком маленький (много запросов)
        max_pages: int | None = None,  # None = безлимитно (с safety cap 10_000), int = ограничение
        embedded_key: str | None = None,
        early_stop_callback: Callable[[list[dict]], bool] | None = None,
        extra_delay: float = 0.0,  # Дополнительная задержка между страницами (для заметок)
        return_meta: bool = False,  # Если True, возвращает tuple (list, meta), иначе только list (обратная совместимость)
    ) -> list[dict] | tuple[list[dict], dict[str, Any]]:
        """
        Возвращает склеенный список элементов из _embedded (v4).
        Rate limiting применяется автоматически через _rate_limit() в _request().
        
        Args:
            early_stop_callback: Функция, которая принимает текущий список элементов и возвращает True,
                                если нужно прервать пагинацию. Вызывается после каждой страницы.
            extra_delay: Дополнительная задержка между страницами в секундах (для снижения нагрузки на API).
            return_meta: Если True, возвращает tuple (list, meta) с метаданными пагинации.
        
        Returns:
            list[dict] или tuple[list[dict], dict]: Список элементов или (список, метаданные)
            Метаданные содержат: pages_fetched, elements_fetched, truncated, limit
        """
        out: list[dict] = []
        page = 1
        truncated = False
        
        # Safety cap: максимальное количество страниц для защиты от бесконечных циклов
        # Используется только если max_pages=None (безлимитно)
        SAFETY_CAP_PAGES = 10_000
        
        # Определяем реальный лимит страниц
        effective_max_pages = max_pages if max_pages is not None else SAFETY_CAP_PAGES
        
        while True:
            if page > effective_max_pages:
                # Soft cap: логируем WARNING, устанавливаем флаг truncated
                truncated = True
                if max_pages is None:
                    logger.warning(
                        f"get_all_pages: достигнут safety cap={SAFETY_CAP_PAGES} страниц для {path}, "
                        f"получено элементов: {len(out)}. Возможна неполная выборка (truncated=True)."
                    )
                else:
                    logger.warning(
                        f"get_all_pages: достигнут max_pages={max_pages} для {path}, "
                        f"получено элементов: {len(out)}. Возможна неполная выборка (truncated=True)."
                    )
                break
            p = dict(params or {})
            p["page"] = page
            p["limit"] = limit
            
            try:
                # Rate limiting применяется автоматически в self.get() -> self._request() -> self._rate_limit()
                data = self.get(path, params=p, retry_on_429=True) or {}
                logger.debug(f"get_all_pages: endpoint={path}, page={page}, получено элементов на странице: {len(data.get('_embedded', {}).get(embedded_key or 'items', [])) if isinstance(data, dict) else 0}")
            except RateLimitError as e:
                # При 429 после всех retry - поднимаем исключение, НЕ возвращаем пустой список
                logger.error(
                    f"get_all_pages: Rate limit достигнут при получении страницы {page} для {path}. "
                    f"Получено элементов: {len(out)}. Прерываем импорт."
                )
                raise
            except AmoApiError as e:
                # Для других ошибок API - тоже поднимаем исключение
                logger.error(
                    f"get_all_pages: API ошибка при получении страницы {page} для {path}: {e}. "
                    f"Получено элементов: {len(out)}"
                )
                raise
            
            embedded = (data.get("_embedded") or {}) if isinstance(data, dict) else {}
            if embedded_key:
                items = embedded.get(embedded_key) if isinstance(embedded.get(embedded_key), list) else None
            else:
                # guess list key: companies/users/notes/tasks...
                items = None
                for _k, v in embedded.items():
                    if isinstance(v, list):
                        items = v
                        break
            if not items:
                logger.debug(f"get_all_pages: нет элементов на странице {page} для {path}, завершаем пагинацию")
                break
            out.extend(items)
            
            # ОПТИМИЗАЦИЯ: проверяем, нужно ли прервать пагинацию
            if early_stop_callback and early_stop_callback(out):
                logger.info(f"get_all_pages: раннее прерывание пагинации для {path} на странице {page} (получено элементов: {len(out)})")
                break
            
            if len(items) < limit:
                logger.debug(f"get_all_pages: получено меньше limit ({len(items)} < {limit}) для {path}, завершаем пагинацию")
                break
            
            # Дополнительная задержка между страницами (для заметок и других тяжелых endpoints)
            if extra_delay > 0:
                time.sleep(extra_delay)
            
            page += 1
        
        pages_fetched = page - 1
        logger.info(f"get_all_pages: завершена пагинация для {path}, всего страниц: {pages_fetched}, элементов: {len(out)}")
        
        if return_meta:
            pagination_meta = {
                "pages_fetched": pages_fetched,
                "elements_fetched": len(out),
                "truncated": truncated,
                "limit": limit,
            }
            return out, pagination_meta
        
        return out
    
    def get_metrics(self) -> dict[str, Any]:
        """
        Возвращает метрики использования клиента:
        - request_count: количество API-запросов
        - elapsed_time: время работы клиента в секундах
        - avg_rps: средний RPS (запросов в секунду)
        """
        elapsed = time.time() - self._start_time
        avg_rps = self._request_count / elapsed if elapsed > 0 else 0.0
        return {
            "request_count": self._request_count,
            "elapsed_time": elapsed,
            "avg_rps": avg_rps,
        }
    
    def reset_metrics(self) -> None:
        """Сбрасывает счетчики метрик (для нового этапа импорта)"""
        self._request_count = 0
        self._start_time = time.time()


