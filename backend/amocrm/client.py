from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from django.utils import timezone

from ui.models import AmoApiConfig


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
        if self._token_valid():
            return
        self.refresh_token()

    def authorize_url(self) -> str:
        # amo: /oauth?client_id=...&redirect_uri=...&response_type=code
        qs = urllib.parse.urlencode(
            {"client_id": self.cfg.client_id, "redirect_uri": self.cfg.redirect_uri, "response_type": "code"}
        )
        return f"{self.base}/oauth?{qs}"

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
            qs = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None}, doseq=True)
            url = url + ("&" if "?" in url else "?") + qs

        headers: dict[str, str] = {"Accept": "application/json"}
        data_bytes = None
        if json_body is not None:
            headers["Content-Type"] = "application/json"
            data_bytes = json.dumps(json_body).encode("utf-8")

        if auth:
            self.ensure_token()
            tok = self.cfg.access_token
            ttype = self.cfg.token_type or "Bearer"
            headers["Authorization"] = f"{ttype} {tok}"

        req = urllib.request.Request(url=url, method=method.upper(), headers=headers, data=data_bytes)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read() or b""
                data = _json_loads(raw)
                return AmoResponse(status=int(resp.status), data=data, headers={k.lower(): v for k, v in resp.headers.items()})
        except urllib.error.HTTPError as e:  # type: ignore[attr-defined]
            raw = e.read() or b""
            return AmoResponse(status=int(getattr(e, "code", 500) or 500), data=_json_loads(raw) or raw.decode("utf-8", errors="replace"), headers={})
        except Exception as e:
            raise AmoApiError(str(e))

    def get(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        url = f"{self.base}{path}"
        res = self._request("GET", url, params=params, auth=True)
        if res.status == 401:
            # один повтор после refresh
            self.refresh_token()
            res = self._request("GET", url, params=params, auth=True)
        if res.status >= 400:
            raise AmoApiError(f"GET {path} failed ({res.status}): {res.data}")
        return res.data

    def get_all_pages(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        limit: int = 250,
        max_pages: int = 200,
        embedded_key: str | None = None,
    ) -> list[dict]:
        """
        Возвращает склеенный список элементов из _embedded (v4).
        """
        out: list[dict] = []
        page = 1
        while True:
            if page > max_pages:
                break
            p = dict(params or {})
            p["page"] = page
            p["limit"] = limit
            data = self.get(path, params=p) or {}
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


