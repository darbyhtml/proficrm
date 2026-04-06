---
tags: [инфраструктура, nginx, cors, ssl]
---

# Nginx

## Два уровня Nginx

### 1. Host Nginx (`/etc/nginx/sites-available/crm-staging`)
- SSL termination (Let's Encrypt)
- IP whitelist (allow/deny)
- Proxy → Docker Nginx (127.0.0.1:8080)
- **НЕ добавляет CORS заголовки** (иначе дубли)

### 2. Docker Nginx (`nginx/staging.conf`)
- Reverse proxy → Django (web:8000)
- Static files (`/static/`, `/media/`)
- WebSocket proxy (`/ws/`)
- SSE proxy (proxy_buffering off)
- CORS preflight для Widget API

## CORS архитектура (Widget)

```
Browser → Host Nginx → Docker Nginx → Django
                         │                │
                    OPTIONS: CORS      POST/GET: CORS
                    (preflight)        (_add_widget_cors_headers)
```

> [!warning] Разделение обязанностей
> - **Nginx** обрабатывает OPTIONS preflight для `/api/widget/`
> - **Django** добавляет CORS на ответы через `_add_widget_cors_headers()`
> - **django-cors-headers** используется только для основного API, НЕ для виджета

## SSE-специфичные настройки

```nginx
location ~ ^/api/widget/stream/ {
    proxy_buffering off;
    proxy_cache off;
    proxy_read_timeout 60s;
}

location ~ ^/api/conversations/.+/stream/ {
    proxy_buffering off;
    proxy_cache off;
    proxy_read_timeout 60s;
}
```

## Конфиги

| Файл | Описание |
|------|---------|
| `nginx/staging.conf` | Docker Nginx staging |
| `nginx/production.conf` | Docker Nginx прод |
| `nginx/snippets/` | Переиспользуемые сниппеты |
| `nginx/errors/` | Кастомные страницы ошибок |

---

Связано: [[Docker и сервисы]] · [[Мессенджер]] · [[API]]
