# Ревью репозитория и очистка

## Проблема с security.txt

**Проблема**: Email не подхватывается из `.env` в Docker.

**Причина**: В `docker-compose.yml` не передается `SECURITY_CONTACT_EMAIL` в environment.

**Решение**: Добавлено `SECURITY_CONTACT_EMAIL: ${SECURITY_CONTACT_EMAIL:-}` в docker-compose.yml.

**Что нужно сделать на VDS**:
1. Убедитесь, что в `.env` указан `SECURITY_CONTACT_EMAIL=sdm@profi-cpr.ru`
2. Обновите код: `git pull`
3. Перезапустите: `docker-compose restart web`

---

## Файлы для удаления из репозитория

### Временные файлы (не используются в коде):
1. ✅ `export_columns.txt` - временный файл для импорта, не используется
2. ✅ `export_schema.json` - временный файл для импорта, не используется

### Файлы, которые уже в .gitignore, но были закоммичены ранее:
Эти файлы нужно удалить из git (но оставить локально):
- `debug_types.py`, `debug_types2.py` - уже в .gitignore
- `count_types.py` - уже в .gitignore  
- `inspect_csv.py`, `inspect_export.py` - уже в .gitignore
- `scan_rows.py` - уже в .gitignore
- `cloudflared.exe` - уже в .gitignore

### Файлы для обсуждения:

**docker-compose.dev.yml** и **docker-compose.vds.yml**:
- Это override файлы для разных окружений
- Могут быть полезны для разработки и деплоя
- **Вопрос**: Используете ли вы эти файлы? Если нет - можно удалить.

**Новинки.txt**:
- Уже в .gitignore
- Если был закоммичен ранее - нужно удалить из git

---

## Рекомендации

1. Удалить временные файлы экспорта
2. Удалить из git файлы, которые уже в .gitignore
3. Решить судьбу docker-compose override файлов

