# Волна 11. API split: public / internal

**Цель волны:** Разделить API на 3 контура: public (для внешних интеграций, API keys, rate limits), internal (session auth, для SPA в будущем), mobile (JWT, для Android app). Каждый контур — со своими правилами.

**Параллелизация:** нет, короткая волна.

**Длительность:** 5–7 рабочих дней.

**Требования:** Wave 1 (api/v1/ split) завершена. Wave 2 (auth, rate limiting) завершена.

---

## Этап 11.1. Структура /api/v1/ разделение

### Контекст
Сейчас всё в `/api/v1/*`. Нужно: `/api/v1/public/*` (external, API keys), `/api/v1/internal/*` (session), `/api/v1/mobile/*` (JWT device-bound).

### Цель
Чёткое разделение endpoint'ов по назначению и способу auth.

### Что делать
1. **URL structure**:
   ```
   /api/v1/public/           # API key auth, strict rate limits
     /companies/
     /leads/                 # POST для создания лидов с сайта
     /events/                # POST для отправки custom events
   
   /api/v1/internal/         # session auth, для web-frontend
     /companies/
     /deals/
     /tasks/
     /chat/
     /analytics/
     ...
   
   /api/v1/mobile/           # JWT device-bound, для Android app
     /phone/                 # phonebridge endpoints
     /profile/
     /companies/             # urgent-only для offline work
   ```

2. **Common schemas** — одни и те же serializer'ы могут использоваться в разных контурах, но с разными permissions.

3. **Versioning**: префикс `/api/v1/`, future `/api/v2/`.

4. **OpenAPI** — 3 отдельные схемы:
   - `/api/schema/public.yaml`
   - `/api/schema/internal.yaml`
   - `/api/schema/mobile.yaml`

5. **Migration**:
   - Все существующие `/api/v1/*` → сейчас попадают в `internal/`.
   - Permanent redirect старых URL на новые (3-6 месяцев), затем удаление.

### Инструменты
- `mcp__context7__*`

### Definition of Done
- [ ] 3 контура работают
- [ ] OpenAPI для каждого
- [ ] Legacy redirects настроены
- [ ] Документация обновлена

### Артефакты
- `backend/api/v1/public/`
- `backend/api/v1/internal/`
- `backend/api/v1/mobile/`
- `backend/api/v1/urls.py` (root)
- `docs/api/public-api.md`
- `docs/api/internal-api.md`
- `docs/api/mobile-api.md`

### Валидация
```bash
curl http://localhost:8001/api/v1/public/
curl http://localhost:8001/api/v1/internal/
curl http://localhost:8001/api/v1/mobile/
```

### Откат
```bash
git revert
```

### Обновить в документации
- `docs/api/README.md`

---

## Этап 11.2. API keys для public API

### Контекст
Для external интеграций нужен API key authentication.

### Цель
Управление API keys с rate limits, scope permissions.

### Что делать
1. **Model `APIKey`**:
   - `key` (hash: bcrypt или argon2, plain — только показывается юзеру при создании).
   - `owner` (User FK).
   - `name` (human-readable).
   - `scopes` (list): `leads:write`, `companies:read`, `events:write`.
   - `rate_limit_tier`: basic / extended / unlimited.
   - `created_at`, `last_used_at`, `expires_at` (optional).
   - `is_active`.
   - `allowed_ips` (optional whitelist).

2. **Auth backend**:
   - Header: `Authorization: ApiKey <key>`.
   - Middleware: parse, verify hash, populate `request.api_key`, `request.user = api_key.owner`.

3. **Permissions**:
   - DRF `HasAPIKeyScope('leads:write')` permission class.

4. **Rate limits per tier**:
   - basic: 60 req/min.
   - extended: 600 req/min.
   - unlimited: опасно, только для особых случаев.

5. **Management UI** (`/api-keys/`, ADMIN / SALES_HEAD+):
   - List, create, revoke, rotate.
   - Usage stats: requests count, last used, most common endpoints.

6. **Audit**:
   - Каждый API call — в `APIKeyUsageLog` (sampled 1% + все failures).
   - Alert при unusual pattern (spike, new IP).

### Definition of Done
- [ ] APIKey model + auth backend
- [ ] Scope permissions работают
- [ ] Rate limits per tier
- [ ] Management UI
- [ ] Audit log

### Артефакты
- Миграции
- `backend/api/v1/auth/api_key.py`
- `backend/api/v1/permissions/scopes.py`
- `backend/api/v1/throttling/api_key.py`
- `backend/ui/views/pages/api_keys.py`
- `backend/templates/api_keys/*.html`
- `tests/api/test_api_keys.py`
- `docs/api/authentication.md`

### Валидация
```bash
pytest tests/api/test_api_keys.py
curl -H "Authorization: ApiKey test_key" http://staging/api/v1/public/
```

### Откат
```bash
git revert
```

### Обновить в документации
- `docs/api/authentication.md`

---

## Этап 11.3. Webhooks (outbound API)

### Контекст
Для интеграций часто нужны webhooks: внешняя система хочет узнать, когда создан новый контакт.

### Цель
Webhook system: подписка на события → POST на URL при событии.

### Что делать
1. **Model `Webhook`**:
   - `url`, `secret` (for HMAC signature).
   - `events` (list): `company.created`, `deal.won`, `message.new`, etc.
   - `is_active`, `owner`.
   - `created_at`, `last_triggered_at`.

2. **Event dispatcher**:
   - При событии в CRM → проверка всех active webhooks с подписью на event → send POST (async Celery).
   - Body: JSON с payload.
   - Headers: `X-Webhook-Signature: sha256=<hmac>`, `X-Webhook-Event: company.created`.

3. **Retry logic**:
   - На 5xx / timeout — retry с exp backoff (5 попыток).
   - После исчерпания — disable webhook + alert owner.

4. **Delivery log**:
   - `WebhookDelivery`: status_code, response_body (first 500 chars), delivered_at, attempt_number, error.

5. **Management UI**:
   - List webhooks, create, edit events, view delivery log, replay.

6. **Security**:
   - HMAC signature verification (клиент на своей стороне проверяет).
   - Allowed destinations: only HTTPS.
   - SSRF protection (Wave 2.5): no internal IPs.

### Definition of Done
- [ ] Webhook model + dispatcher
- [ ] Retry logic
- [ ] Delivery log
- [ ] Management UI
- [ ] HMAC signatures работают

### Артефакты
- Миграции
- `backend/webhooks/models.py`
- `backend/webhooks/dispatcher.py`
- `backend/webhooks/tasks.py` (Celery)
- `backend/ui/views/pages/webhooks.py`
- `tests/webhooks/test_dispatcher.py`
- `docs/api/webhooks.md`

### Валидация
```bash
pytest tests/webhooks/
# Manual: create webhook с webhook.site URL, trigger event, проверить delivery
```

### Откат
```bash
git revert
```

### Обновить в документации
- `docs/api/webhooks.md`

---

## Этап 11.4. Full OpenAPI documentation + Scalar docs UI

### Контекст
drf-spectacular есть, но OpenAPI может быть кривой (missing descriptions, examples, tags).

### Цель
Production-grade OpenAPI specs + beautiful docs UI.

### Что делать
1. **drf-spectacular config**:
   - Settings: `TITLE`, `DESCRIPTION`, `VERSION`, `CONTACT`, `LICENSE`.
   - Tags per app.
   - Security schemes: ApiKey, Session, JWT.

2. **Spec quality**:
   - Все viewsets имеют `@extend_schema` decorator с description, examples.
   - Все serializers с field descriptions.
   - No `unknown` types.
   - Lint with `redocly lint schema.yaml`.

3. **Docs UI**:
   - **Scalar** (современная альтернатива Swagger UI — очень красивая).
   - `/api/docs/` → Scalar UI loading spec.
   - Per-audience: `/api/docs/public/`, `/api/docs/internal/`, `/api/docs/mobile/`.

4. **Code samples**:
   - Примеры на curl, Python, JavaScript.
   - Auto-generated или hand-written для ключевых endpoints.

5. **Changelog**:
   - `docs/api/CHANGELOG.md` — каждое API изменение.
   - В UI — кнопка «What's new».

### Инструменты
- `drf-spectacular`, `Scalar` (CDN script)
- `redocly` CLI

### Definition of Done
- [ ] OpenAPI specs чистые (redocly lint passing)
- [ ] Scalar UI работает красиво
- [ ] Code samples для главных endpoints
- [ ] Changelog ведётся

### Артефакты
- `backend/api/v1/schema/*.py` — custom extensions
- `backend/templates/api_docs.html`
- `docs/api/CHANGELOG.md`
- `docs/api/README.md`

### Валидация
```bash
python manage.py spectacular --file docs/api/schema.yaml
redocly lint docs/api/schema.yaml  # no errors
```

### Откат
```bash
git revert
```

### Обновить в документации
- `docs/api/README.md`

---

## Checklist завершения волны 11

- [ ] 3 контура API разделены (public / internal / mobile)
- [ ] API keys с scopes и rate limits
- [ ] Webhooks outbound
- [ ] OpenAPI + Scalar docs UI

**После этого** — CRM готова быть частью интеграций.
