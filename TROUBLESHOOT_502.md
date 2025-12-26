# Решение проблемы 502 Bad Gateway

## Диагностика

502 Bad Gateway означает, что Nginx не может подключиться к Django приложению.

### Шаг 1: Проверьте статус контейнеров

```bash
cd /opt/proficrm
docker-compose ps
```

Должны быть запущены оба контейнера: `db` и `web`.

### Шаг 2: Проверьте логи контейнера web

```bash
docker-compose logs web --tail=50
```

Ищите ошибки, особенно связанные с:
- Импортами модулей
- Загрузкой .env файла
- Запуском Django

### Шаг 3: Проверьте, что приложение слушает порт

```bash
docker-compose exec web netstat -tlnp | grep 8000
# или
docker-compose exec web ps aux | grep runserver
```

### Шаг 4: Проверьте конфигурацию Nginx

```bash
# Проверьте, что Nginx проксирует на правильный порт
cat /etc/nginx/sites-enabled/crm.groupprofi.ru
# или
nginx -t
```

Должно быть что-то вроде:
```nginx
proxy_pass http://127.0.0.1:8001;
```

### Шаг 5: Перезапустите контейнеры

```bash
docker-compose down
docker-compose up -d
docker-compose logs -f web
```

---

## Возможные причины и решения

### 1. Ошибка в коде после последних изменений

Если в логах есть ошибки Python, проверьте:
- Синтаксические ошибки
- Импорты модулей
- Загрузку .env файла

**Решение**: Исправьте ошибки или откатите последний коммит:
```bash
git log --oneline -5
git revert HEAD  # если последний коммит сломал приложение
```

### 2. Контейнер не запустился

**Решение**: Пересоздайте контейнеры:
```bash
docker-compose down
docker-compose up -d --build
```

### 3. Проблема с .env файлом

Если Django не может загрузить .env, проверьте:
```bash
# Проверьте, что файл существует
ls -la /opt/proficrm/.env

# Проверьте права доступа
chmod 644 /opt/proficrm/.env

# Проверьте содержимое (осторожно, не показывайте секреты)
head -5 /opt/proficrm/.env
```

### 4. Порт изменился

Если используете `docker-compose.vds.yml`, проверьте порт:
```bash
cat docker-compose.vds.yml
# Должно быть: "127.0.0.1:8001:8000"
```

И в Nginx должно быть:
```nginx
proxy_pass http://127.0.0.1:8001;
```

---

## Быстрое решение

Если нужно быстро восстановить работу:

```bash
cd /opt/proficrm

# 1. Остановите контейнеры
docker-compose down

# 2. Проверьте код на ошибки
git status
git log --oneline -3

# 3. Если последний коммит сломал приложение, откатите его
# git revert HEAD

# 4. Запустите контейнеры
docker-compose up -d

# 5. Смотрите логи в реальном времени
docker-compose logs -f web
```

Если видите ошибки в логах - пришлите их, и я помогу исправить.

