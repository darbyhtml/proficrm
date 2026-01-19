from __future__ import annotations

import json
import logging
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from django.utils import timezone

from ui.models import AmoApiConfig

logger = logging.getLogger(__name__)


class AmoApiError(RuntimeError):
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
    def __init__(self, cfg: AmoApiConfig):
        self.cfg = cfg

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
        res = self._request("POST", url, json_body=payload, auth=False)
        if res.status >= 400:
            raise AmoApiError(f"Token exchange failed ({res.status}): {res.data}")
        data = res.data or {}
        self.cfg.access_token = str(data.get("access_token") or "")
        self.cfg.refresh_token = str(data.get("refresh_token") or "")
        self.cfg.token_type = str(data.get("token_type") or "Bearer")
        expires_in = int(data.get("expires_in") or 0)
        self.cfg.expires_at = timezone.now() + timezone.timedelta(seconds=expires_in) if expires_in else None
        self.cfg.last_error = ""
        self.cfg.save(update_fields=["access_token", "refresh_token", "token_type", "expires_at", "last_error", "updated_at"])

    def _request(self, method: str, url: str, *, params: dict[str, Any] | None = None, json_body: Any | None = None, auth: bool = True) -> AmoResponse:
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
                            # Экранируем ключ и значение
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
            with urllib.request.urlopen(req, timeout=15) as resp:  # уменьшили таймаут с 30 до 15 сек
                raw = resp.read() or b""
                data = _json_loads(raw)
                return AmoResponse(status=int(resp.status), data=data, headers={k.lower(): v for k, v in resp.headers.items()})
        except urllib.error.HTTPError as e:  # type: ignore[attr-defined]
            raw = e.read() or b""
            return AmoResponse(status=int(getattr(e, "code", 500) or 500), data=_json_loads(raw) or raw.decode("utf-8", errors="replace"), headers={})
        except Exception as e:
            raise AmoApiError(str(e))

    def get(self, path: str, *, params: dict[str, Any] | None = None, retry_on_429: bool = True) -> Any:
        url = f"{self.base}{path}"
        max_retries = 3
        retry_delay = 2  # начальная задержка в секундах
        
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
            if res.status == 429 and retry_on_429 and attempt < max_retries - 1:
                # Экспоненциальная задержка: 2, 4, 8 секунд
                delay = retry_delay * (2 ** attempt)
                logger.warning(f"Rate limit (429) для {path}, повтор через {delay} сек (попытка {attempt + 1}/{max_retries})")
                time.sleep(delay)
                continue
            
            if res.status >= 400:
                raise AmoApiError(f"GET {path} failed ({res.status}): {res.data}")
            
            return res.data
        
        # Если все попытки исчерпаны
        raise AmoApiError(f"GET {path} failed (429): Rate limit exceeded after {max_retries} attempts")

    def get_all_pages(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        limit: int = 100,  # Уменьшено с 250 до 100 для избежания 504 Gateway Timeout
        max_pages: int = 200,
        embedded_key: str | None = None,
        delay_between_pages: float = 0.5,  # Задержка между страницами для избежания rate limit
    ) -> list[dict]:
        """
        Возвращает склеенный список элементов из _embedded (v4).
        Добавлена задержка между запросами для избежания rate limit (429).
        """
        out: list[dict] = []
        page = 1
        while True:
            if page > max_pages:
                break
            p = dict(params or {})
            p["page"] = page
            p["limit"] = limit
            
            # Задержка перед запросом (кроме первой страницы)
            if page > 1 and delay_between_pages > 0:
                time.sleep(delay_between_pages)
            
            try:
                data = self.get(path, params=p, retry_on_429=True) or {}
            except AmoApiError as e:
                # Если получили 429 после всех retry, логируем и прерываем
                if "429" in str(e) or "Rate limit" in str(e):
                    logger.warning(f"Rate limit достигнут при получении страницы {page} для {path}. Получено элементов: {len(out)}")
                    break
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
                break
            out.extend(items)
            if len(items) < limit:
                break
            page += 1
        return out


