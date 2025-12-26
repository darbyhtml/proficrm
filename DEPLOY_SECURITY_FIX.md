# Исправление: Security.txt не подхватывает email из .env

## Проблема
Security.txt показывает дефолтный email `security@example.com` вместо указанного в `.env`.

## Решение
Добавлена загрузка `SECURITY_CONTACT_EMAIL` через Django settings для надежности.

## Что нужно сделать на VDS:

### 1. Обновите код:
```bash
cd /opt/proficrm
git pull
```

### 2. Убедитесь, что в `.env` указан email:
```bash
cat .env | grep SECURITY_CONTACT_EMAIL
# Должно показать: SECURITY_CONTACT_EMAIL=sdm@profi-cpr.ru
```

### 3. Перезапустите приложение:
```bash
docker-compose restart web
```

### 4. Проверьте:
```bash
curl https://crm.groupprofi.ru/.well-known/security.txt
```

Должно показать:
```
Contact: mailto:sdm@profi-cpr.ru
...
```

---

## Примечание о Tailwind CDN

В консоли браузера есть предупреждение:
> "cdn.tailwindcss.com should not be used in production"

Это **не критично для безопасности**, но для production рекомендуется:
1. Использовать скомпилированный Tailwind CSS
2. Или убрать `'unsafe-inline'` из CSP после перехода на скомпилированный CSS

**Текущее состояние**: Tailwind CDN работает, но добавляет `'unsafe-inline'` в CSP, что немного снижает защиту от XSS. Для внутренней CRM это приемлемо, но можно улучшить в будущем.

