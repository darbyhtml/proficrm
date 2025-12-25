# Удаление заметок типа amomail на VDS

## Шаг 1: Подключитесь к VDS

```bash
ssh user@your-vds-ip
```

## Шаг 2: Перейдите в директорию проекта

```bash
cd /path/to/your/crm/project
```

## Шаг 3: Активируйте виртуальное окружение (если используется)

```bash
source venv/bin/activate
# или
source .venv/bin/activate
```

## Шаг 4: Сначала проверьте, сколько заметок будет удалено (dry-run)

```bash
cd backend
python manage.py delete_amomail_notes --dry-run
```

Это покажет:
- Сколько заметок найдено
- Примеры заметок, которые будут удалены
- Но НЕ удалит их

## Шаг 5: Если всё правильно, удалите заметки

```bash
python manage.py delete_amomail_notes
```

Команда удалит все заметки, которые:
- Имеют `external_source` содержащий "amomail"
- Или имеют текст содержащий "type: amomail"
- Или имеют текст содержащий "Письмо (amoMail)"

## Если используете Docker

```bash
# Проверка (dry-run)
docker-compose exec web python manage.py delete_amomail_notes --dry-run

# Удаление
docker-compose exec web python manage.py delete_amomail_notes
```

## Важно

- Команда удаляет заметки **безвозвратно**
- Рекомендуется сначала сделать backup базы данных
- После удаления заметки нельзя восстановить

