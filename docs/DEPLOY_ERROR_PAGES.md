# Развертывание кастомных страниц ошибок Nginx

## Шаги для применения на сервере

### 1. Получить изменения из репозитория

```bash
cd /opt/proficrm
git pull origin main
```

### 2. Проверить, что файлы на месте

```bash
ls -la nginx/error_pages/
```

Должны быть файлы:
- `500.html`
- `502.html`
- `503.html`
- `504.html`
- `README.md`

### 3. Перезапустить nginx контейнер

```bash
docker-compose -f docker-compose.staging.yml restart nginx
```

Или если нужно пересоздать контейнер:

```bash
docker-compose -f docker-compose.staging.yml up -d --force-recreate nginx
```

### 4. Проверить логи nginx на ошибки

```bash
docker-compose -f docker-compose.staging.yml logs nginx | tail -20
```

### 5. Проверить, что конфигурация nginx валидна

```bash
docker-compose -f docker-compose.staging.yml exec nginx nginx -t
```

### 6. Проверить, что файлы смонтированы в контейнер

```bash
docker-compose -f docker-compose.staging.yml exec nginx ls -la /usr/share/nginx/html/error_pages/
```

Должны быть видны все HTML файлы.

### 7. Тестирование

Чтобы протестировать страницу ошибки, можно временно остановить web контейнер:

```bash
# Остановить web контейнер (будет 502 ошибка)
docker-compose -f docker-compose.staging.yml stop web

# Открыть в браузере: http://crm.groupprofi.ru/companies/
# Должна показаться кастомная страница 502

# Вернуть web контейнер
docker-compose -f docker-compose.staging.yml start web
```

## Возможные проблемы

### Проблема: Страницы не отображаются

**Решение:**
1. Проверьте, что файлы существуют на хосте: `ls -la nginx/error_pages/`
2. Проверьте, что файлы смонтированы в контейнер: `docker-compose exec nginx ls -la /usr/share/nginx/html/error_pages/`
3. Проверьте логи nginx: `docker-compose logs nginx | grep error`
4. Перезапустите nginx: `docker-compose restart nginx`

### Проблема: Nginx не запускается

**Решение:**
1. Проверьте синтаксис конфигурации: `docker-compose exec nginx nginx -t`
2. Проверьте логи: `docker-compose logs nginx`

### Проблема: Показывается старая страница

**Решение:**
1. Очистите кеш браузера (Ctrl+Shift+R или Ctrl+F5)
2. Проверьте, что конфигурация обновилась: `docker-compose exec nginx cat /etc/nginx/conf.d/default.conf | grep error_page`
3. Перезапустите nginx: `docker-compose restart nginx`
