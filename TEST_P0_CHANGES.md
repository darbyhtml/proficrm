# Тестирование P0 Security Changes

## Что было изменено

Добавлены проверки безопасности в `backend/crm/settings.py`:
1. **SEC-001:** Проверка DEBUG в production
2. **SEC-002:** Проверка MAILER_FERNET_KEY
3. **SEC-003:** Проверка CORS_ALLOWED_ORIGINS

Все проверки выдают **только предупреждения (warnings)**, не блокируют запуск.

---

## Шаг 1: Отправка изменений на сервер

```bash
# На локальной машине
git push
```

---

## Шаг 2: Обновление на сервере

```bash
# На VDS сервере
cd /opt/proficrm
git pull
docker-compose -f docker-compose.yml -f docker-compose.vds.yml restart web
```

---

## Шаг 3: Проверка логов при запуске

### 3.1. Проверка, что приложение запустилось

```bash
# На VDS сервере
docker-compose -f docker-compose.yml -f docker-compose.vds.yml logs web --tail=50
```

**Ожидаемый результат:** 
- ✅ Приложение запустилось без ошибок
- ✅ Нет `ImproperlyConfigured` или других критических ошибок
- ⚠️ Могут быть предупреждения (warnings) - это нормально

### 3.2. Поиск предупреждений безопасности

```bash
# На VDS сервере
docker-compose -f docker-compose.yml -f docker-compose.vds.yml logs web | grep -i "SECURITY WARNING"
```

**Что искать:**
- `⚠️ SECURITY WARNING: DEBUG=True detected in production-like environment` - если DEBUG=1 в production
- `⚠️ SECURITY WARNING: MAILER_FERNET_KEY is not set in production` - если ключ не установлен
- `⚠️ SECURITY WARNING: CORS_ALLOWED_ORIGINS contains localhost origins` - если есть localhost в CORS

**Если предупреждения есть:**
- Это нормально - они информируют о потенциальных проблемах
- Нужно исправить конфигурацию (см. раздел "Исправление предупреждений")

**Если предупреждений нет:**
- ✅ Отлично! Конфигурация правильная

---

## Шаг 4: Проверка работоспособности приложения

### 4.1. Health Check

```bash
# С локальной машины или с сервера
curl https://crm.groupprofi.ru/health/
```

**Ожидаемый результат:**
```json
{"status": "ok", "database": "ok", "cache": "ok", "celery": "ok"}
```

### 4.2. Проверка входа в систему

1. Откройте в браузере: `https://crm.groupprofi.ru/login/`
2. Войдите с вашими учетными данными
3. Проверьте, что вход работает

**Ожидаемый результат:**
- ✅ Страница логина загружается
- ✅ Можно войти в систему
- ✅ После входа перенаправляет на главную страницу

### 4.3. Проверка основных функций

Проверьте несколько ключевых функций:
- ✅ Просмотр списка компаний
- ✅ Просмотр деталей компании
- ✅ Создание/редактирование компании (если есть права)
- ✅ Просмотр задач
- ✅ API endpoints (если используются)

---

## Шаг 5: Исправление предупреждений (если есть)

### Если видите предупреждение о DEBUG:

```bash
# На VDS сервере, проверьте .env файл
cat .env | grep DJANGO_DEBUG

# Должно быть:
DJANGO_DEBUG=0

# Если нет или =1, исправьте:
# Отредактируйте .env и установите DJANGO_DEBUG=0
# Затем перезапустите:
docker-compose -f docker-compose.yml -f docker-compose.vds.yml restart web
```

### Если видите предупреждение о MAILER_FERNET_KEY:

```bash
# На VDS сервере, проверьте .env файл
cat .env | grep MAILER_FERNET_KEY

# Если пусто, сгенерируйте ключ:
docker-compose -f docker-compose.yml -f docker-compose.vds.yml exec web python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Скопируйте результат и добавьте в .env:
# MAILER_FERNET_KEY=<скопированный_ключ>

# Затем перезапустите:
docker-compose -f docker-compose.yml -f docker-compose.vds.yml restart web
```

### Если видите предупреждение о CORS_ALLOWED_ORIGINS:

```bash
# На VDS сервере, проверьте .env файл
cat .env | grep CORS_ALLOWED_ORIGINS

# Если содержит localhost, исправьте:
# CORS_ALLOWED_ORIGINS=https://crm.groupprofi.ru

# Затем перезапустите:
docker-compose -f docker-compose.yml -f docker-compose.vds.yml restart web
```

---

## Шаг 6: Проверка логов после работы

После нескольких минут работы проверьте логи на наличие ошибок:

```bash
# На VDS сервере
docker-compose -f docker-compose.yml -f docker-compose.vds.yml logs web --tail=100 | grep -i error
```

**Ожидаемый результат:**
- ✅ Нет новых ошибок, связанных с нашими изменениями
- ⚠️ Могут быть другие ошибки (не связанные с нашими изменениями)

---

## Чеклист проверки

- [ ] Изменения отправлены на сервер (`git pull` выполнен)
- [ ] Контейнер перезапущен (`docker-compose restart web`)
- [ ] Приложение запустилось без критических ошибок
- [ ] Health check работает (`/health/` возвращает OK)
- [ ] Вход в систему работает
- [ ] Основные функции работают (компании, задачи и т.д.)
- [ ] Проверены предупреждения безопасности (если есть - исправлены)
- [ ] Логи не содержат новых ошибок

---

## Что делать, если что-то не работает

### Если приложение не запускается:

1. Проверьте логи:
   ```bash
   docker-compose -f docker-compose.yml -f docker-compose.vds.yml logs web --tail=100
   ```

2. Проверьте синтаксис settings.py:
   ```bash
   docker-compose -f docker-compose.yml -f docker-compose.vds.yml exec web python -m py_compile /app/backend/crm/settings.py
   ```

3. Если есть ошибка в settings.py - откатите изменения:
   ```bash
   cd /opt/proficrm
   git log --oneline -5  # найти коммит до наших изменений
   git revert HEAD  # или git reset --hard <commit_before_changes>
   docker-compose -f docker-compose.yml -f docker-compose.vds.yml restart web
   ```

### Если есть предупреждения, но приложение работает:

- Это нормально! Предупреждения информируют о потенциальных проблемах конфигурации
- Исправьте конфигурацию согласно разделу "Исправление предупреждений"
- После исправления предупреждения исчезнут

---

## Отчет о тестировании

После тестирования заполните:

- [ ] Тестирование пройдено успешно
- [ ] Найдены предупреждения (какие): ________________
- [ ] Предупреждения исправлены: [ ] Да [ ] Нет
- [ ] Приложение работает корректно: [ ] Да [ ] Нет
- [ ] Готовы к продолжению (P1 правки): [ ] Да [ ] Нет

---

## Контакты для вопросов

Если возникли проблемы, проверьте:
1. Логи контейнера (`docker-compose logs web`)
2. Файл `.env` на сервере
3. Отчет `SECURITY_AUDIT_REPORT.md` для понимания изменений
