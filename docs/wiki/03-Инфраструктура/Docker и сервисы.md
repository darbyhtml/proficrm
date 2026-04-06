---
tags: [инфраструктура, docker]
---

# Docker и сервисы

## Контейнеры (staging)

| Сервис | Образ | Порт | Назначение |
|--------|-------|------|-----------|
| **web** | python:3.13-slim | 8000 | Django + Gunicorn (gthread 4w×8t) |
| **nginx** | nginx:alpine | 127.0.0.1:8080→80 | Reverse proxy |
| **db** | postgres:16 | 5432 | PostgreSQL |
| **redis** | redis:7-alpine | 6379 | Кэш, брокер, pub/sub |
| **celery** | python:3.13-slim | — | Воркер фоновых задач |
| **celery-beat** | python:3.13-slim | — | Планировщик задач |
| **websocket** | python:3.13-slim | 8000 | Daphne (Django Channels) |

## Compose-файлы

| Файл | Среда |
|------|-------|
| `docker-compose.yml` | Разработка |
| `docker-compose.staging.yml` | Staging |
| `docker-compose.prod.yml` | Продакшен |
| `docker-compose.test.yml` | Тесты |

## Volumes

- `pgdata_staging` — данные PostgreSQL
- `redisdata_staging` — данные Redis
- `media_staging` — загруженные файлы
- `static_staging` — собранная статика

## Dockerfile.staging

```
FROM python:3.13-slim
├── gcc, postgresql-client, curl, gosu
├── pip install requirements.txt
├── entrypoint.sh (migrate, collectstatic, gosu)
├── useradd crmuser (UID 1000)
└── CMD: gunicorn --worker-class gthread --workers 4 --threads 8
```

## Gunicorn

```
worker-class: gthread
workers: 4
threads: 8 (per worker)
timeout: 120с
= 32 параллельных соединения
```

> [!important] Почему gthread, а не gevent
> gevent конфликтует с psycopg3 (monkey-patching ломает `Queue[T]`).
> gthread использует реальные потоки — совместим со всем стеком.

## Entrypoint

`docker/entrypoint.sh`:
1. Проверка обязательных env-переменных
2. `migrate --noinput`
3. `collectstatic --noinput`
4. `gosu crmuser` → запуск от непривилегированного пользователя

---

Связано: [[Nginx]] · [[Деплой workflow]] · [[Celery задачи]]
