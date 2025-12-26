# Исправление: Security.txt email не подхватывается в Docker

## Проблема
После добавления `SECURITY_CONTACT_EMAIL` в `docker-compose.yml` email все еще показывает дефолтный `security@example.com`.

## Причина
Docker Compose не всегда автоматически загружает переменные из `.env` файла в контейнер. Нужно явно указать `env_file`.

## Решение
Добавлен `env_file: - .env` в секцию `web` в `docker-compose.yml`.

## Что нужно сделать на VDS:

### 1. Обновите код:
```bash
cd /opt/proficrm
git pull
```

### 2. Пересоздайте контейнер (не просто restart):
```bash
docker-compose down
docker-compose up -d
```

Или если используете override файлы:
```bash
docker-compose -f docker-compose.yml -f docker-compose.vds.yml down
docker-compose -f docker-compose.yml -f docker-compose.vds.yml up -d
```

**Важно**: Используйте `down` и `up -d`, а не `restart`, чтобы переменные окружения перезагрузились.

### 3. Проверьте:
```bash
curl https://crm.groupprofi.ru/.well-known/security.txt
```

Должно показать:
```
Contact: mailto:sdm@profi-cpr.ru
...
```

### 4. Если все еще не работает, проверьте:
```bash
# Проверьте, что переменная есть в .env
cat .env | grep SECURITY_CONTACT_EMAIL

# Проверьте переменные в контейнере
docker-compose exec web env | grep SECURITY_CONTACT_EMAIL
```

Если переменная не видна в контейнере, убедитесь что:
- `.env` файл находится в корне проекта (`/opt/proficrm/.env`)
- В файле есть строка `SECURITY_CONTACT_EMAIL=sdm@profi-cpr.ru` (без пробелов вокруг `=`)
- После изменений выполнен `docker-compose down` и `docker-compose up -d`

