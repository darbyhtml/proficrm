# Команды для применения раздела "Лог ошибок" на сервере

## 1. Перейти в директорию проекта и обновить код
```bash
cd /opt/proficrm
git pull origin main
```

## 2. Применить миграции базы данных
```bash
docker-compose exec web python manage.py migrate audit
```

## 3. Проверить, что миграции применены успешно
```bash
docker-compose exec web python manage.py showmigrations audit
```

## 4. Перезапустить веб-сервер (если нужно)
```bash
docker-compose restart web
```

## 5. Проверить логи (опционально)
```bash
docker-compose logs web --tail 50
```

## Полная последовательность команд (одной строкой)
```bash
cd /opt/proficrm && git pull origin main && docker-compose exec web python manage.py migrate audit && docker-compose exec web python manage.py showmigrations audit
```

## Проверка работы
После применения миграций:
1. Войдите в админ-панель Django (если нужно проверить модель)
2. Перейдите в Настройки → Лог ошибок
3. Раздел должен быть доступен и работать

## Примечания
- Middleware автоматически начнет логировать ошибки после перезапуска
- Если возникнут ошибки при миграции, проверьте логи: `docker-compose logs web`
