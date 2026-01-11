# Проверка исправления CORS

## Проблема

Предупреждение все еще появляется после изменения `.env`. Возможные причины:

1. **Старые логи** - предупреждение могло появиться при старой загрузке
2. **Переменная не применилась** - нужно проверить, что значение действительно изменилось
3. **Дефолтное значение** - если переменная не установлена, используется дефолт `http://localhost:5173`

## Проверка

### 1. Проверьте, что переменная установлена в `.env`

```bash
cd /opt/proficrm
grep CORS_ALLOWED_ORIGINS .env
```

**Ожидаемый результат:**
- `CORS_ALLOWED_ORIGINS=` (пустое значение)
- Или строка отсутствует вообще

**Если строка отсутствует**, добавьте:
```bash
echo "CORS_ALLOWED_ORIGINS=" >> .env
```

### 2. Проверьте, что переменная применилась в контейнере

```bash
docker-compose -f docker-compose.yml -f docker-compose.vds.yml exec web env | grep CORS
```

**Ожидаемый результат:**
- `CORS_ALLOWED_ORIGINS=` (пустое значение)
- Или переменная отсутствует

### 3. Полный перезапуск контейнера (не просто restart)

```bash
docker-compose -f docker-compose.yml -f docker-compose.vds.yml down
docker-compose -f docker-compose.yml -f docker-compose.vds.yml up -d
```

Это полностью пересоздаст контейнер и загрузит новые переменные окружения.

### 4. Проверьте свежие логи (только новые записи)

```bash
docker-compose -f docker-compose.yml -f docker-compose.vds.yml logs web --tail=20 --since=1m | grep -i "SECURITY WARNING"
```

Или просто посмотрите последние логи:
```bash
docker-compose -f docker-compose.yml -f docker-compose.vds.yml logs web --tail=50
```

**Если предупреждение все еще есть в новых логах**, значит переменная не применилась.

## Решение

### Вариант 1: Установить пустое значение явно

В `.env` файле:
```
CORS_ALLOWED_ORIGINS=
```

### Вариант 2: Установить production домен

Если CORS нужен, укажите правильный домен:
```
CORS_ALLOWED_ORIGINS=https://crm.groupprofi.ru
```

### Вариант 3: Изменить дефолт в settings.py (если CORS не нужен)

Если CORS вообще не используется, можно изменить дефолт в `settings.py`:

```python
# Было:
CORS_ALLOWED_ORIGINS = [o.strip() for o in os.getenv("CORS_ALLOWED_ORIGINS", "http://localhost:5173").split(",") if o.strip()]

# Стало:
CORS_ALLOWED_ORIGINS = [o.strip() for o in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",") if o.strip()]
```

Но это требует изменения кода и перезапуска.

## Быстрое решение

Если предупреждение появляется только в старых логах, а новые логи чистые - значит все исправлено. Предупреждения в старых логах можно игнорировать.

Проверьте свежие логи после полного перезапуска:
```bash
docker-compose -f docker-compose.yml -f docker-compose.vds.yml down
docker-compose -f docker-compose.yml -f docker-compose.vds.yml up -d
sleep 5
docker-compose -f docker-compose.yml -f docker-compose.vds.yml logs web --tail=30 | grep -i "SECURITY WARNING"
```

Если в новых логах нет предупреждения - все исправлено! ✅
