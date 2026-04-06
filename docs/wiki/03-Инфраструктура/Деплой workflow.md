---
tags: [инфраструктура, деплой, workflow]
---

# Деплой Workflow

> [!danger] Железное правило
> **Прод** (`/opt/proficrm`) — НИКОГДА не трогать через Claude Code.
> Деплой на прод делает ТОЛЬКО пользователь вручную после полного QA.

## Workflow

```
1. Локально  →  Код + тесты
2. git push  →  GitHub (main)
3. Staging   →  git pull + docker build + up -d
4. QA        →  Тестирование на staging
5. Отчёт    →  Claude Code отчитывается пользователю
6. Прод      →  Пользователь деплоит ВРУЧНУЮ
```

## Staging деплой

```bash
# SSH на сервер
ssh -i ~/.ssh/id_proficrm_deploy root@5.181.254.172

# Вариант 1: полный деплой
cd /opt/proficrm-staging && ./deploy_staging.sh

# Вариант 2: быстрый (без переиндексации)
SKIP_INDEXING=1 ./deploy_staging.sh

# Вариант 3: только web-контейнер
cd /opt/proficrm-staging && git pull origin main
docker compose -f docker-compose.staging.yml build web
docker compose -f docker-compose.staging.yml up -d web
```

## Скрипт `deploy_staging.sh`

1. Проверка `.env.staging` (обязательные переменные)
2. `git pull origin main`
3. `docker compose build`
4. `up -d db redis typesense` + ожидание
5. `migrate --noinput`
6. `collectstatic --noinput` (от root для volume прав)
7. `rebuild_company_search_index` (если не SKIP_INDEXING)
8. `up -d` — все сервисы

## Серверы

| Среда | Путь | Доступ |
|-------|------|--------|
| Staging | `/opt/proficrm-staging/` | SSH root/sdm |
| Прод | `/opt/proficrm/` | **ЗАПРЕЩЕНО** |

## SSH

```bash
# Staging (root)
ssh -i ~/.ssh/id_proficrm_deploy root@5.181.254.172

# Staging (sdm)
ssh -i ~/.ssh/id_proficrm_deploy sdm@5.181.254.172
```

## Важные нюансы

> [!warning] docker compose up -d vs restart
> При изменении `.env` файла: `up -d` (пересоздаёт контейнер).
> `restart` НЕ перечитывает env_file!

> [!warning] Nginx reload после пересоздания web
> После `up -d web` нужно `restart nginx` — DNS имя контейнера сменилось.

---

Связано: [[Docker и сервисы]] · [[Nginx]] · [[Статус staging]]
