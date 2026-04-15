# Архитектурные решения

## [2026-04-06] gthread вместо gevent для Gunicorn

**Контекст:** SSE-стримы (widget 25с + operator 30с + notifications 55с) полностью блокировали 2 sync-воркера Gunicorn. Нужен concurrency для параллельной обработки SSE и обычных API-запросов.

**Решение:** `--worker-class gthread --workers 4 --threads 8` (32 параллельных соединения).

**Альтернативы:**
- gevent (greenlets, тысячи соединений) — **отвергнуто**: monkey-patching ломает psycopg3 (`Queue[T]` не subscriptable, TypeError при старте worker)
- Увеличить sync workers до 8-10 — неэффективно, каждый worker = отдельный процесс с полной копией Django
- eventlet — те же проблемы с monkey-patching что и gevent

**Причина:** gthread использует реальные POSIX-потоки, совместим со всем стеком включая psycopg3. 4×8=32 потока достаточно для staging (3 SSE + остальные API).

---

## [2026-04-05] CORS для виджета: разделение nginx/Django

**Контекст:** Браузер получал дублирующиеся `Access-Control-Allow-Origin` заголовки — nginx и Django оба добавляли CORS. Chrome/Firefox отвергают ответ при дублях.

**Решение:** Разделение:
- Nginx: обработка OPTIONS preflight для `/api/widget/` (возвращает 204)
- Django: добавление CORS на ответы через `_add_widget_cors_headers()`
- django-cors-headers: только для основного API (`/api/`), НЕ для виджета

**Альтернативы:**
- Только django-cors-headers — **отвергнуто**: middleware перехватывает OPTIONS до view-кода, custom handler не работает для preflight
- Только nginx CORS — **отвергнуто**: nginx не знает какие origins разрешены для конкретного inbox

**Причина:** nginx быстро обрабатывает preflight (без обращения к Django), Django имеет контекст для валидации origin на ответах.

---

## [2026-04-02] SSE вместо WebSocket для real-time виджета

**Контекст:** Виджет встраивается на внешние сайты. Нужен real-time push сообщений от сервера к клиенту.

**Решение:** SSE (Server-Sent Events) с короткими соединениями (25-55 секунд) и автоматическим reconnect.

**Альтернативы:**
- WebSocket (Django Channels уже есть) — **отвергнуто для виджета**: сложнее CORS, EventSource API проще, SSE достаточно для server→client push
- Long polling — **отвергнуто**: больше запросов, больше latency

**Причина:** SSE работает через обычный HTTP (проще CORS), EventSource автоматически reconnect'ит, для one-directional push достаточно. WebSocket оставлен для оператор-панели (используется через Django Channels).

---

## [2026-03] PostgreSQL FTS вместо Elasticsearch/Typesense

**Контекст:** Нужен поиск компаний — нечёткий + полнотекстовый.

**Решение:** PostgreSQL FTS (tsvector/tsquery) + pg_trgm (similarity).

**Альтернативы:**
- Elasticsearch — тяжёлый, отдельный сервис, избыточен
- Typesense — был в docker-compose, но удалён (deprecated в settings.py)

**Причина:** Нет лишних зависимостей, достаточная производительность для текущего масштаба (~тысячи компаний). Переиндексация: 1 Celery-задача в день.

---

## [2026-03] Fernet для шифрования SMTP-паролей

**Контекст:** SMTP-пароли хранятся в БД, нужно шифрование.

**Решение:** Fernet symmetric encryption (библиотека `cryptography`).

**Альтернативы:**
- AES-GCM напрямую — больше кода, Fernet — wrapper над AES-CBC
- HashiCorp Vault — overkill для текущего масштаба

**Причина:** Fernet — простой, стандартный, обратимый (нужен для отправки). Ключ в env: `MAILER_FERNET_KEY`.

---

## [2026-03] Единая ветка main

**Контекст:** Ранее мессенджер разрабатывался в feature-ветке. После слияния (2026-04-02) — нужно ли продолжать feature-ветки?

**Решение:** Одна ветка `main` для всего.

**Альтернативы:**
- Git Flow (develop, feature/*, release/*) — избыточно для одного разработчика

**Причина:** Один разработчик + Claude Code. Staging защищает от ошибок на проде. Feature-ветки добавляют complexity без benefit.
