"""
Нагрузочные тесты для Messenger widget API (Locust).

Сценарий моделирует работу браузера с виджетом:
- bootstrap: создание/получение сессии;
- send: отправка входящих сообщений;
- poll: периодический опрос новых OUT-сообщений.

Требования:
- запущен backend (например, `python backend/manage.py runserver 0.0.0.0:8000`);
- создан тестовый Inbox с включённым Messenger.

Переменные окружения:
- MESSENGER_LOADTEST_BASE_URL   — базовый URL backend (по умолчанию http://localhost:8000);
- MESSENGER_LOADTEST_WIDGET_TOKEN — widget_token тестового Inbox (обязателен);
- MESSENGER_LOADTEST_SINCE_ID  — начальное значение since_id (по умолчанию 0).

Запуск:

    pip install "locust>=2.0"
    locust -f locustfile.py --host=%MESSENGER_LOADTEST_BASE_URL%
"""

import os
import time
import uuid

from locust import HttpUser, between, task


BASE_PATH = os.getenv("MESSENGER_LOADTEST_BASE_URL", "http://localhost:8000")
WIDGET_TOKEN = os.getenv("MESSENGER_LOADTEST_WIDGET_TOKEN") or ""
DEFAULT_SINCE_ID = int(os.getenv("MESSENGER_LOADTEST_SINCE_ID", "0") or "0")


class MessengerWidgetUser(HttpUser):
    """
    Один пользователь Locust имитирует одного посетителя с виджетом.
    """

    wait_time = between(1, 5)

    def on_start(self) -> None:
        if not WIDGET_TOKEN:
            # Без токена нет смысла гонять нагрузку по widget API
            raise RuntimeError("MESSENGER_LOADTEST_WIDGET_TOKEN is required for load tests")

        self.base_path = BASE_PATH.rstrip("/")
        self.widget_token = WIDGET_TOKEN
        self.contact_external_id = str(uuid.uuid4())
        self.session_token: str | None = None
        self.since_id: int = DEFAULT_SINCE_ID

        # Первичный bootstrap
        self._bootstrap()

    def _bootstrap(self) -> None:
        """
        POST /api/widget/bootstrap/ — создаёт/получает сессию виджета.
        """
        payload = {
            "widget_token": self.widget_token,
            "contact_external_id": self.contact_external_id,
        }
        with self.client.post(
            "/api/widget/bootstrap/",
            json=payload,
            name="widget_bootstrap",
            catch_response=True,
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"bootstrap failed: {resp.status_code} {resp.text}")
                return

            try:
                data = resp.json()
            except Exception as exc:  # noqa: BLE001
                resp.failure(f"bootstrap invalid JSON: {exc}")
                return

            token = data.get("widget_session_token")
            if not token:
                resp.failure("bootstrap: no widget_session_token in response")
                return

            self.session_token = token
            # Сброс since_id — как в widget.js
            self.since_id = 0
            resp.success()

    @task(3)
    def send_message(self) -> None:
        """
        POST /api/widget/send/ — отправка входящего сообщения.

        Вес 3: чаще отправляем сообщения, чем просто poll.
        """
        if not self.session_token:
            self._bootstrap()
            if not self.session_token:
                return

        body = f"[loadtest] message at {time.time()}"
        payload = {
            "widget_token": self.widget_token,
            "widget_session_token": self.session_token,
            "body": body,
        }

        with self.client.post(
            "/api/widget/send/",
            json=payload,
            name="widget_send",
            catch_response=True,
        ) as resp:
            # Для нагрузки интересны и 429 (throttle), поэтому считаем не только 200 успешным.
            if resp.status_code not in (200, 201, 202, 204, 429):
                resp.failure(f"send failed: {resp.status_code} {resp.text}")
            else:
                resp.success()

    @task(2)
    def poll_updates(self) -> None:
        """
        GET /api/widget/poll/ — опрос новых OUT-сообщений и событий.
        """
        if not self.session_token:
            self._bootstrap()
            if not self.session_token:
                return

        params = {
            "widget_token": self.widget_token,
            "widget_session_token": self.session_token,
            "since_id": str(self.since_id or 0),
        }

        with self.client.get(
            "/api/widget/poll/",
            params=params,
            name="widget_poll",
            catch_response=True,
        ) as resp:
            if resp.status_code != 200:
                # 401 / 403 / 5xx фиксируем как ошибки
                resp.failure(f"poll failed: {resp.status_code} {resp.text}")
                return

            try:
                data = resp.json()
            except Exception as exc:  # noqa: BLE001
                resp.failure(f"poll invalid JSON: {exc}")
                return

            # Обновляем since_id по максимальному id сообщений (как в widget.js)
            messages = data.get("messages") or []
            max_id = self.since_id or 0
            for msg in messages:
                mid = msg.get("id")
                if isinstance(mid, int) and mid > max_id:
                    max_id = mid
            self.since_id = max_id
            resp.success()

