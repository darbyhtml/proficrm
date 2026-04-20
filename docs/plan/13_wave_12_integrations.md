# Волна 12. Интеграции

**Цель волны:** Подключить два критичных источника данных, которые сильно облегчают жизнь менеджера: Яндекс.Метрика (UTM→CRM), IMAP-мониторинг личных ящиков сотрудников.

**Параллелизация:** этапы независимы.

**Длительность:** 10–14 рабочих дней (IMAP — трудоёмкий).

**Требования:** Wave 6 (email infrastructure) завершена. Wave 11 (API keys) завершена для webhooks.

**V2** (отложено): 1С-интеграция, Google/Yandex Calendar sync, социальные сети.

---

## Этап 12.1. Яндекс.Метрика: UTM + goal conversion tracking

### Контекст
В Wave 3.5 добавили UTM capture в widget. Теперь — связка с Яндекс.Метрикой: передача conversions обратно, чтобы маркетинг мог оптимизировать кампании.

### Цель
Двусторонняя связка: UTM из Метрики → CRM → conversion события обратно → Метрика.

### Что делать
1. **UTM from Метрика**:
   - Widget JS сохраняет `_ym_uid`, `_ym_counter`, `utm_*` параметры в sessionStorage.
   - При создании лида — отправка на backend.
   - Сохранение в `LeadSource` (Wave 3.5).

2. **Conversion events to Метрика**:
   - При ключевом событии в CRM (deal won, sale closed) — отправка `goal` event в Метрику через measurement protocol.
   - Endpoint: `https://mc.yandex.ru/collect/<counter_id>?...`.
   - Параметры: `goal`, `offline_event_timestamp`, `client_id` (из `_ym_uid`).

3. **Offline conversions API** (если Метрика поддерживает):
   - Batch upload через REST API, с OAuth.
   - Daily cron — все conversions за день.

4. **Config**:
   - `YandexMetrikaConfig` модель: counter_id, token, goals mapping (deal_stage → goal_id).
   - Admin UI для настройки.

5. **Тесты**:
   - Mock Метрики endpoint.
   - Verification: event отправился с правильными параметрами.

### Инструменты
- `mcp__context7__*` — Yandex.Metrika API docs

### Definition of Done
- [ ] UTM + ym_uid сохраняется при лиде
- [ ] Conversions отправляются в Метрику
- [ ] Admin UI для настройки
- [ ] Реальная проверка: открыть счётчик Метрики, убедиться, что goals приходят

### Артефакты
- `backend/integrations/yandex_metrika/`
- `backend/integrations/yandex_metrika/services.py`
- `backend/integrations/yandex_metrika/models.py`
- `backend/celery/tasks/metrika_sync.py`
- `tests/integrations/test_yandex_metrika.py`
- `docs/integrations/yandex-metrika.md`

### Валидация
```bash
pytest tests/integrations/test_yandex_metrika.py
# Manual: trigger deal won → проверить в Метрике
```

### Откат
```bash
# Отключить Celery task
# Удалить конфиг — не отправляет events
```

### Обновить в документации
- `docs/integrations/yandex-metrika.md`

---

## Этап 12.2. IMAP мониторинг: личные ящики сотрудников

### Контекст
Менеджеры переписываются с клиентами из личных ящиков (Яндекс.Почта / GMail / корпоративные). Сейчас эта переписка теряется — в CRM её не видно. Нужна интеграция: IMAP listener → парсинг писем → создание Message в CRM, привязка к Contact/Company.

### Цель
Менеджер подключил свой почтовый ящик → в карточке клиента видна вся переписка автоматически.

### Что делать
1. **OAuth Yandex + Google**:
   - Яндекс OAuth: `mail:imap_full` scope. Application registration в Яндексе.
   - Google OAuth: `https://mail.google.com/` scope. GCP project с OAuth consent screen.
   - Для корпоративных (кастомных) IMAP — username + app password (с Fernet-шифрованием).

2. **Модели**:
   - `UserMailbox`: user, provider (yandex/google/imap), email, oauth_tokens_encrypted, imap_host, imap_port, is_active, last_synced_at, sync_errors_count.

3. **Sync service**:
   - Celery task `sync_user_mailbox(mailbox_id)` каждые 5 минут.
   - IMAP connect (lib: `imapclient`).
   - IDLE mode (push notifications) если поддерживается — или fallback polling.
   - Fetch messages `SINCE last_synced`.
   - Parse: from/to/cc, subject, body (text+html), attachments, Message-ID.

4. **Matching to CRM**:
   - `from` or `to` email → lookup в `ContactEmail`.
   - Если contact found → создать `Message` в existing Conversation (или new с inbox.type='email').
   - Если not found → create Contact? (config-driven: «создавать новых контактов автоматически»).

5. **Thread-ing**:
   - By `Message-ID` / `In-Reply-To` / `References` headers → группировка в thread.
   - Отображение в UI в виде conversation.

6. **Privacy**:
   - OPT-IN обязательно: юзер явно подключает ящик.
   - Юзер видит только свою переписку + CRM-wide переписку видимую его role (data scope).
   - Важно: приватные письма (личные) тоже попадают. Фильтры: `from` equals CRM domain → exclude? Или по folders (только Inbox, не Sent to self)?
   - Consent disclosure в UI ясный.

7. **Attachments**:
   - Download + save to S3 (Wave 10).
   - Preview if possible.

8. **Rate limiting**:
   - Per-mailbox limits чтобы не банили от провайдера.

9. **Error handling**:
   - Auth expired → refresh OAuth token.
   - IMAP errors → exponential backoff, alert after 3 consecutive.

10. **UI**:
    - Settings page: «Подключить почту». OAuth flow.
    - Carousel setup wizard.

### Инструменты
- `imapclient`, `aiohttp-oauth2`, `google-auth`
- `mcp__context7__*`

### Definition of Done
- [ ] OAuth flow для Yandex + Google работает
- [ ] IMAP connect + fetch работает
- [ ] Thread-ing корректный
- [ ] Matching to contacts работает
- [ ] Attachments сохраняются в S3
- [ ] Errors handled with retries + alerts
- [ ] Privacy disclosure понятный
- [ ] Тесты на парсер (с fixture'ами real emails)

### Артефакты
- Миграции для `UserMailbox`
- `backend/integrations/imap/`
- `backend/integrations/imap/oauth_yandex.py`
- `backend/integrations/imap/oauth_google.py`
- `backend/integrations/imap/sync_service.py`
- `backend/integrations/imap/parser.py`
- `backend/integrations/imap/matcher.py`
- `backend/celery/tasks/imap_sync.py`
- `backend/ui/views/pages/profile/mailboxes.py`
- `backend/templates/profile/mailboxes/*.html`
- `tests/integrations/imap/`
- `docs/integrations/imap.md`
- `docs/integrations/oauth-yandex-setup.md`
- `docs/integrations/oauth-google-setup.md`

### Валидация
```bash
pytest tests/integrations/imap/
# Manual: connect real mailbox, send test email from outside, see it in CRM
```

### Откат
```bash
# Disable Celery tasks
# Users can disconnect mailbox from UI
```

### Обновить в документации
- `docs/integrations/imap.md`

---

## Этап 12.3. Слияние каналов: чат + email + звонки в единый timeline

### Контекст
После интеграции IMAP — в карточке клиента будет чат + email + phone history. Нужно их объединить в единую timeline.

### Цель
В карточке контакта — единая hronologическая timeline: все коммуникации с этим клиентом.

### Что делать
1. **Unified model** (или unified view):
   - Option A: новая модель `CommunicationEvent` с polymorphic link на Message (chat) / EmailMessage (imap) / CallRequest (phone). Дублирование данных, но просто.
   - Option B: view/queryset, который делает UNION ALL всех трёх.
   - Рекомендую B (меньше миграций).

2. **UI**:
   - В карточке компании / контакта — tab «Коммуникации».
   - Иконки (chat bubble / envelope / phone).
   - Фильтры: по типу, по каналу, по дате.
   - Infinite scroll.

3. **Search**:
   - Full-text search по communications контакта.

4. **Export**:
   - PDF печатный отчёт «Вся переписка с клиентом X» для суда / compliance.

### Definition of Done
- [ ] Unified timeline отображается
- [ ] Фильтры работают
- [ ] Search работает
- [ ] Export в PDF работает

### Артефакты
- `backend/core/communications/service.py`
- `backend/ui/views/pages/company/communications.py`
- `backend/templates/company/communications.html`
- `tests/core/test_communications.py`
- `docs/features/communications-timeline.md`

### Валидация
```bash
pytest tests/core/test_communications.py
```

### Откат
```bash
git revert
```

### Обновить в документации
- `docs/features/communications-timeline.md`

---

## Этап 12.4. Интеграционные webhooks — стандартные события

### Контекст
Wave 11.3 — webhooks framework есть. Теперь — реализовать стандартный набор events для external.

### Цель
20+ типов событий готовы к подписке через webhooks.

### Что делать
1. **Events catalog**:
   - `company.created`, `company.updated`, `company.deleted`, `company.transferred`
   - `contact.created`, `contact.updated`, `contact.merged`
   - `deal.created`, `deal.stage_changed`, `deal.won`, `deal.lost`
   - `task.created`, `task.completed`, `task.overdue`
   - `message.new` (chat), `message.read`
   - `campaign.sent`, `campaign.completed`
   - `call.started`, `call.ended`, `call.missed`
   - `user.login`, `user.logout`
   - `consent.given`, `consent.revoked`

2. **Payload schemas**: описать в `docs/api/webhook-events.md` с примерами.

3. **Payload versioning**: `schema_version: "1.0"` в каждом payload.

4. **Testing sandbox**:
   - Admin endpoint `/api/v1/webhooks/test` — триггерит fake event на your webhook.

### Definition of Done
- [ ] 20+ events catalogued
- [ ] Все events отправляются через dispatcher
- [ ] Payload schemas документированы
- [ ] Testing sandbox работает

### Артефакты
- `backend/webhooks/events/*.py`
- `docs/api/webhook-events.md`

### Валидация
```bash
pytest tests/webhooks/
# Manual: subscribe external webhook → trigger events → verify
```

### Откат
```bash
git revert
```

### Обновить в документации
- `docs/api/webhook-events.md`

---

## Checklist завершения волны 12

- [ ] Яндекс.Метрика интеграция
- [ ] IMAP sync личных ящиков (Yandex + Google OAuth)
- [ ] Unified communications timeline в карточке клиента
- [ ] 20+ webhook events catalogued

**Отложено в V2**: 1С, Google Calendar, iOS-сторона Android app, social media.
