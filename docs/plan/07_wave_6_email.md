# Волна 6. Email-рассылка

**Цель волны:** Привести email-инфраструктуру в состояние, при котором рассылки не попадают в спам, bounces обрабатываются автоматически, unsubscribe работает по 152-ФЗ, а SPF/DKIM/DMARC настроены правильно.

**Параллелизация:** средняя. Этапы 6.1–6.3 последовательны (аудит → код → пост-обработка). 6.4–6.7 — параллельно с предыдущими.

**Длительность:** 10–12 рабочих дней.

**Требования:** Wave 2 завершена (opt-in, 152-ФЗ модель данных). Wave 4.2 (Notification Hub) завершена.

**Важно:** Drag-n-drop редактор — V2. В этой волне делаем «богатый» HTML-редактор с переменными и превью, без drag-n-drop.

---

## Этап 6.1. Аудит deliverability и DNS

### Контекст
DKIM-подпись настроена (`DKIM_SELECTOR`, `DKIM_PRIVATE_KEY` в env). SMTP.bz используется через шифрованные Fernet-креды. SPF / DMARC DNS-записи — надо проверить.

### Цель
Убедиться, что DNS и почтовая инфраструктура настроены правильно, и рассылка не попадает в спам.

### Что делать
1. **Проверка DNS**:
   - SPF: `dig TXT crm.groupprofi.ru` — должен содержать `v=spf1 include:smtp.bz ~all` (или аналог).
   - DKIM: `dig TXT <selector>._domainkey.crm.groupprofi.ru` — публичный ключ.
   - DMARC: `dig TXT _dmarc.crm.groupprofi.ru` — `v=DMARC1; p=quarantine; rua=mailto:dmarc@groupprofi.ru`.
   - Если чего-то нет — добавить в DNS (это вне кода, но в runbook пошагово).

2. **Mail-tester**:
   - Отправить тестовое письмо на `test-xxx@mail-tester.com` — получить score 10/10.
   - Если ниже — разобрать каждую претензию (SpamAssassin score, SPF fail, DKIM invalid, HTML ratio).

3. **Check MX и reverse DNS** сервера-отправителя:
   - PTR запись должна резолвиться в hostname, который есть в SPF.

4. **List-Unsubscribe header** (RFC 8058):
   - Header `List-Unsubscribe: <mailto:unsub@groupprofi.ru>, <https://crm.groupprofi.ru/unsubscribe?token=XXX>`.
   - `List-Unsubscribe-Post: List-Unsubscribe=One-Click` — для Gmail/Yahoo.
   - Это сильно влияет на placement в inbox vs spam.

5. **MTA-STS** (опционально, но добавляет баллов):
   - Policy на `mta-sts.groupprofi.ru/.well-known/mta-sts.txt`.

6. **Runbook** `docs/runbooks/email-deliverability.md` с полной проверкой DNS, mail-tester, troubleshooting.

### Инструменты
- `Bash`: `dig`, `host`, `openssl`
- `mcp__context7__*`

### Definition of Done
- [ ] Mail-tester score ≥ 9/10
- [ ] SPF, DKIM, DMARC настроены и валидны
- [ ] List-Unsubscribe headers добавлены к письмам
- [ ] Runbook написан
- [ ] Письма с основных email-адресов (info@, noreply@, personal manager email) проверены

### Артефакты
- `docs/runbooks/email-deliverability.md`
- `docs/runbooks/dns-records.md`
- `backend/mailer/services/headers.py` — добавление List-Unsubscribe, Message-ID, Precedence

### Валидация
```bash
dig TXT crm.groupprofi.ru
dig TXT default._domainkey.crm.groupprofi.ru
dig TXT _dmarc.crm.groupprofi.ru
# Отправить тестовое письмо на mail-tester.com
```

### Откат
DNS изменения — откатываемые.

### Обновить в документации
- `docs/runbooks/email-deliverability.md`

---

## Этап 6.2. Bounce handling — webhook (основной) или IMAP (fallback)

### Контекст
Сейчас bounces (Mail Delivery Failed от чужих MX-серверов) никто не обрабатывает. Следствие: письма продолжают отправляться на мёртвые адреса, репутация домена падает.

**Первый шаг — проверить, что даёт smtp.bz.** Большинство современных SMTP-провайдеров имеют webhook API для bounces/complaints — это на порядок проще IMAP.

### Цель
SuppressionList пополняется автоматически из bounces (hard, soft×3, complaints). Рассылка пропускает suppressed адреса.

### Что делать

#### Шаг 0. Определить, какая ветка

```bash
# Проверить документацию smtp.bz на наличие webhooks
curl -s https://api.smtp.bz/docs | grep -i webhook
# Или зайти в панель smtp.bz → настройки → есть раздел «Webhooks»?
```

- **Если smtp.bz даёт webhook** → **Ветка A (рекомендуемая)**
- **Если НЕТ** → **Ветка B (IMAP fallback)**

Зафиксировать решение в `docs/decisions.md` (ADR-???) и в `docs/open-questions.md`.

---

### Ветка A. Webhook от smtp.bz (основная)

Сложность: низкая. Время: 2–3 дня.

1. **Настроить webhook в панели smtp.bz**:
   - URL: `https://crm.groupprofi.ru/api/v1/mailer/webhooks/smtpbz/`
   - События: `bounce`, `complaint`, `unsubscribe` (если поддерживается).
   - Secret token для HMAC-валидации (если есть).

2. **Эндпоинт** `POST /api/v1/mailer/webhooks/smtpbz/`:
   - `@csrf_exempt`
   - Валидация HMAC от smtp.bz (сравнить `X-Smtpbz-Signature` с HMAC от body).
   - Парсинг JSON: `event_type`, `email`, `reason`, `timestamp`, `message_id`.
   - Определение hard/soft по `reason`:
     - Hard: `invalid_recipient`, `user_unknown`, `domain_not_found`, `550_*` коды SMTP.
     - Soft: `mailbox_full`, `temporary_failure`, `deferred`, `421_*`, `4xx_*` коды.
   - Обновить `CampaignRecipient.status` → `bounced_hard` / `bounced_soft` / `complained`.

3. **Модель `SuppressionList`**:
   - Поля: `email`, `reason` (hard_bounce/complaint/unsubscribe/manual), `added_at`, `added_by` (если manual), `expires_at` (опционально для soft bounces на 30 дней).
   - Индекс на `email` уникальный.
   - Глобальный и per-`GlobalMailAccount`.

4. **Hard bounce → immediate suppression**.
5. **Soft bounce → счётчик в `BounceEvent`; после 3 подряд → suppression**.
6. **Complaint (жалоба на спам) → immediate suppression + alert в GlitchTip** — это критично, цифра complaints = убийца репутации.
7. **Deduplication**: если один и тот же email пришёл в webhook 2 раза за секунду — обработать один.
8. **Admin dashboard**:
   - Bounces за 24h/7d.
   - Suppression list с поиском.
   - Возможность ручного удаления из suppression (с audit log).

#### Инструменты ветки A
- `mcp__context7__*` — docs smtp.bz (если есть)
- `requests` + `hmac` для валидации

#### Артефакты ветки A
- `backend/mailer/views/webhooks.py`
- `backend/mailer/services/suppression_service.py`
- `backend/mailer/models.py` — SuppressionList, BounceEvent
- `backend/ui/views/pages/admin/suppression_list.py`
- `tests/mailer/test_smtpbz_webhook.py` (с fixture-JSON от smtp.bz)
- `docs/features/bounce-handling.md` (ветка A)

---

### Ветка B. IMAP fallback (если webhook недоступен)

Сложность: средняя. Время: 5–7 дней.

1. **Постмастер ящик**:
   - Настроить `bounces@groupprofi.ru` с IMAP-доступом.
   - В каждом отправленном письме: `Return-Path: bounces@groupprofi.ru` (VERP можно опционально: `bounces+<recipient_id>@groupprofi.ru`).

2. **Celery beat task** `process_email_bounces`:
   - Каждые 5 минут — IMAP connect, fetch UNSEEN.
   - Парсинг каждого письма:
     - Шаблоны hard bounce: "550 5.1.1 User unknown", "Mailbox not found", "does not exist".
     - Шаблоны soft bounce: "Mailbox full", "Temporarily unavailable", "Deferred".
     - DSN-сообщения (RFC 3464) — стандартный формат.
     - Failure detection через `email.mime` парсинг.
   - Извлечь original recipient из `Final-Recipient` header или из цитируемого оригинального письма.
   - Обновить `CampaignRecipient.status` → `bounced_hard` / `bounced_soft`.
   - Пометить `Message-ID` (если есть в headers `In-Reply-To` / VERP).

3. Модели, правила hard/soft, dashboard — идентично ветке A.

4. **FBL (Feedback Loop)**: для жалоб на спам — отдельный ящик `fbl@groupprofi.ru` (если настроено у провайдера, Gmail и Yahoo поддерживают).

5. **Circuit breaker**: если IMAP падает 3 раза подряд — алерт в GlitchTip, стоп listener на 1 час, retry.

#### Инструменты ветки B
- `imapclient` lib (или stdlib `imaplib`)
- `email` stdlib
- `mcp__context7__*`

#### Артефакты ветки B
- Миграции для `SuppressionList`, `BounceEvent`
- `backend/mailer/services/bounce_parser.py`
- `backend/mailer/services/imap_listener.py`
- `backend/celery/tasks/process_bounces.py`
- `backend/ui/views/pages/admin/suppression_list.py`
- `tests/mailer/test_bounce_parser.py` (с fixture-ами real DSN)
- `docs/features/bounce-handling.md` (ветка B)
- `docs/runbooks/bounce-troubleshooting.md`

---

### Definition of Done (общий для обеих веток)
- [ ] Решение (A или B) зафиксировано в ADR
- [ ] SuppressionList заполняется автоматически
- [ ] Рассылка skipает suppressed адреса
- [ ] Hard/soft bounce distinction работает
- [ ] Complaint → immediate suppression + alert
- [ ] Admin dashboard
- [ ] Unit-тесты на парсер/валидатор
- [ ] E2E: отправить на заведомо плохой адрес (например `test@invalid-domain-zzzzz-12345.com`) → через max 10 мин видно suppression

### Валидация
```bash
# Ветка A
pytest tests/mailer/test_smtpbz_webhook.py
curl -X POST http://staging.url/api/v1/mailer/webhooks/smtpbz/ \
  -H 'Content-Type: application/json' \
  -H 'X-Smtpbz-Signature: <valid_hmac>' \
  -d '{"event":"bounce","email":"test@x.com","reason":"invalid_recipient"}'

# Ветка B
pytest tests/mailer/test_bounce_parser.py
# Manual: отправить на заведомо плохой адрес → через 5 минут проверить suppression
```

### Откат
- **A:** отключить webhook в панели smtp.bz, оставить endpoint (не мешает).
- **B:** Celery beat task отключить, IMAP creds отозвать.

### Обновить в документации
- `docs/features/bounce-handling.md` — финальная версия (ветка A или B)
- `docs/decisions.md` — ADR «Bounce handling: webhook vs IMAP»
- `docs/runbooks/bounce-troubleshooting.md`

---

## Этап 6.3. Unsubscribe page и compliance

### Контекст
Ссылка unsubscribe в письмах возможно есть. Но страница, suppression list, и 152-ФЗ opt-out — не уверены в работоспособности.

### Цель
Надёжная unsubscribe страница + suppression + audit trail.

### Что делать
1. **Unsubscribe token**:
   - HMAC-signed токен: `sign(recipient_id, campaign_id, expires_at)`.
   - URL: `/unsubscribe?t=<token>`.

2. **Unsubscribe flow**:
   - GET `/unsubscribe?t=<token>` → preview page с чек-боксом «я действительно хочу отписаться», кнопка «Подтвердить».
   - POST → записать в `UnsubscribeEvent` + `SuppressionList`.
   - Thank-you page с объяснением «вы будете удалены из рассылок в течение 24 часов» (по факту — моментально).

3. **One-click unsubscribe** (RFC 8058):
   - POST `/unsubscribe/one-click/` (no CSRF check, token-auth).
   - Используется Gmail/Yahoo, когда юзер жмёт «Unsubscribe» кнопку в интерфейсе Gmail.

4. **Повторная подписка**:
   - Через опционально поле `unsubscribe_reason` (dropdown: «слишком часто», «не актуально», «никогда не подписывался», «другое»).
   - После подтверждения — ссылка «если передумаете, подпишитесь заново через админа».

5. **Segment filtering**:
   - Перед отправкой campaign — фильтр SuppressionList.
   - DRF сериалайзер показывает suppression-статус рядом с recipient.

6. **Audit**:
   - `UnsubscribeEvent`: recipient_id, campaign_id, timestamp, reason, ip, user_agent.
   - Link to `DataProcessingConsent` если контакт отзывает согласие целиком (полное опт-аут).

### Definition of Done
- [ ] Unsubscribe page работает
- [ ] One-click unsubscribe работает
- [ ] SuppressionList пополняется
- [ ] Рассылка не отправляется на suppressed
- [ ] Reason statistics в admin dashboard

### Артефакты
- `backend/mailer/services/unsubscribe.py`
- `backend/mailer/services/tokens.py` (HMAC signing)
- `backend/ui/views/pages/unsubscribe.py`
- `backend/templates/pages/unsubscribe/*.html`
- `backend/api/v1/views/unsubscribe.py` (one-click)
- `tests/mailer/test_unsubscribe.py`
- `docs/features/unsubscribe.md`

### Валидация
```bash
pytest tests/mailer/test_unsubscribe.py
# Manual: отписаться → убедиться что не получаешь следующую рассылку
```

### Откат
Token secret ротировать — старые unsubscribe links перестанут работать (но событие не откатывается).

### Обновить в документации
- `docs/features/unsubscribe.md`

---

## Этап 6.4. Template editor (rich HTML + variables + preview)

### Контекст
Сейчас редактор — textarea с сырым HTML и `{{ lead_name }}` переменными. Нужен «богатый» редактор с проверкой, превью, variables autocomplete. Drag-n-drop — V2.

### Цель
Template editor с WYSIWYG-основой, variables autocomplete, live preview, mobile preview.

### Что делать
1. **Editor**: TinyMCE или CKEditor (CKEditor 5 с licence-free распространением лучше).
   - Basic toolbar: bold, italic, underline, H1-H3, lists, link, image upload (→ S3), color picker, align.
   - HTML source code view (advanced users).

2. **Variables**:
   - Библиотека переменных: `{{contact.first_name}}`, `{{company.name}}`, `{{manager.full_name}}`, `{{today}}`, `{{unsubscribe_url}}` и т.д.
   - Autocomplete при наборе `{{` — popup со списком.
   - Template engine на backend: Jinja2 sandboxed (не Django templates, чтобы избежать security issues).

3. **Live preview**:
   - Split view: editor слева, preview справа.
   - Desktop / Mobile toggle (iframe с соответствующим viewport).
   - Preview с sample data (первый recipient или фейк).

4. **Template gallery**:
   - Предустановленные шаблоны: «Новость», «Акция», «Напоминание», «Персональное письмо».
   - User может сохранять свои templates в `EmailTemplate` модель.

5. **Validation**:
   - Unclosed tags.
   - Unknown variables.
   - Missing `{{unsubscribe_url}}` (обязательно!).
   - Base image URLs (не relative).

6. **Spam score pre-check**:
   - Client-side оценка: слишком много !!!, капс, спам-слов из словаря, соотношение картинок/текста.
   - Warning баннер.

7. **Test send**:
   - Кнопка «Отправить тестовое письмо на мой email».
   - Вставляет sample data из первого recipient.

### Инструменты
- CKEditor 5
- Jinja2 sandboxed
- `mcp__context7__*`

### Definition of Done
- [ ] WYSIWYG editor работает, toolbar standard
- [ ] Variables autocomplete
- [ ] Live preview (desktop + mobile)
- [ ] Template gallery + user templates
- [ ] Validation errors показываются
- [ ] Test send работает
- [ ] Spam score warning (basic)

### Артефакты
- `backend/static/ui/mailer/editor/*.js`
- `backend/mailer/services/template_engine.py` (Jinja2 sandbox)
- `backend/mailer/services/template_validator.py`
- `backend/mailer/models/template.py` (EmailTemplate)
- `backend/ui/views/pages/mailer/campaign_edit.py` (обновлённый)
- `backend/templates/mailer/campaign/edit.html`
- `tests/mailer/test_template_engine.py`
- `docs/features/email-templates.md`

### Валидация
```bash
pytest tests/mailer/test_template_engine.py
playwright test tests/e2e/test_campaign_edit.py
```

### Откат
```bash
git revert
```

### Обновить в документации
- `docs/features/email-templates.md`

---

## Этап 6.5. Sending pool и rate limiting (warming, reputation)

### Контекст
`GlobalMailAccount` — уже есть, `MAILER_SENDING_RATE_LIMIT` есть. Нужно: pool management, warm-up при добавлении нового хоста, reputation monitoring.

### Цель
Надёжная отправка рассылок без попадания в спам через контроль rate и warming.

### Что делать
1. **Pool**:
   - Multiple `GlobalMailAccount` — round-robin распределение campaigns по accounts.
   - Health check: если account начинает получать много bounces → снижение rate или временное отключение.

2. **Warm-up** при добавлении нового account:
   - Day 1: 50 emails
   - Day 2: 100
   - Day 3: 200
   - Day 7: 1000
   - Day 14: 5000
   - Автоматическое плавное увеличение лимита.

3. **Rate limiting**:
   - Per account: max X emails/hour, Y emails/day.
   - Adaptive: если bounce rate > 2% за последние 1000 писем → снизить rate в 2 раза, alert.

4. **Reputation monitoring**:
   - Дашборд: bounces %, complaints %, opens %, clicks %.
   - Postmaster.google.com — ручная проверка.

5. **Retries**:
   - Для transient failures — retry через 1 / 5 / 30 мин.
   - Limit 3 retries, потом → failed state.

6. **Suppression integration** (Wave 6.2): при отправке проверка SuppressionList.

### Definition of Done
- [ ] Pool management работает
- [ ] Warm-up логика работает
- [ ] Rate limits соблюдаются
- [ ] Reputation dashboard
- [ ] Retries работают

### Артефакты
- `backend/mailer/services/pool_manager.py`
- `backend/mailer/services/warmup.py`
- `backend/mailer/services/rate_limiter.py`
- `backend/ui/views/pages/mailer/reputation_dashboard.py`
- `tests/mailer/test_pool.py`
- `docs/features/email-pool.md`

### Валидация
```bash
pytest tests/mailer/test_pool.py
```

### Откат
```bash
git revert
```

### Обновить в документации
- `docs/features/email-pool.md`

---

## Этап 6.6. Advanced tracking: opens, clicks, heatmap (опционально)

### Контекст
Открытия и клики уже отслеживаются частично. Нужно улучшить и добавить click heatmap.

### Цель
Полная аналитика по каждой рассылке: кто открыл, когда, какие ссылки кликал.

### Что делать
1. **Open tracking**:
   - Transparent 1x1 pixel в каждом письме.
   - Record: `opened_at`, `ip`, `user_agent` (для GeoIP в Wave 8).
   - Apple Mail Privacy Protection делает pre-fetch → open не 100% реален. Указать в docs.

2. **Click tracking**:
   - Все `<a href>` переписываются на `https://crm.groupprofi.ru/r/<link_token>` с редиректом.
   - Record `clicked_at`, target_url.

3. **Per-campaign metrics**:
   - Sent, delivered, opened, clicked, bounced, unsubscribed.
   - Open rate, click-through rate.
   - Best/worst performing campaigns.

4. **Per-recipient timeline** в карточке contact:
   - Получил письмо X, открыл через 10 минут, кликнул на ссылку Y.

5. **Heatmap** (опционально):
   - Вывод на макете письма, по какой ссылке сколько кликов.

### Definition of Done
- [ ] Open tracking работает
- [ ] Click tracking + redirect работают
- [ ] Campaign metrics в dashboard
- [ ] Per-contact timeline
- [ ] Heatmap (опционально)

### Артефакты
- `backend/mailer/services/tracking.py`
- `backend/ui/views/pages/mailer/campaign_stats.py`
- `backend/api/v1/views/tracking.py` (open pixel, click redirect)
- `tests/mailer/test_tracking.py`
- `docs/features/email-tracking.md`

### Валидация
```bash
pytest tests/mailer/test_tracking.py
```

### Откат
```bash
git revert
```

### Обновить в документации
- `docs/features/email-tracking.md`

---

## Этап 6.7. Scheduled sends и segmentation

### Контекст
Сейчас отправка immediate. Нужна запланированная отправка + улучшенная сегментация.

### Цель
Campaign с `scheduled_at` + advanced segmentation (filter builder).

### Что делать
1. **Scheduled sending**:
   - Campaign.scheduled_at: datetime.
   - Celery beat проверяет pending campaigns каждую минуту.
   - Timezone-aware (все datetimes в UTC в БД).

2. **Segmentation**:
   - Visual filter builder: «компания из филиала ЕКБ И тег 'VIP' И последний контакт более 30 дней назад».
   - Filter operators: =, !=, <, >, contains, in, not in, is null, date_range.
   - Дерево AND/OR conditions.
   - Сохранение segments как `AudienceSegment` — переиспользуемые.
   - Preview: show first 10 matching + total count.

3. **Suppression integration**:
   - Segment автоматически исключает unsubscribed + bounced_hard.

4. **Send volume preview**:
   - Before confirm — показать «будет отправлено N писем, время старта M».

5. **Cancel / pause**:
   - Если scheduled но ещё не стартанула — можно отменить/редактировать.
   - Если в процессе отправки — pause (остановится после текущей партии).

### Definition of Done
- [ ] Scheduled send работает, точность ±60 секунд
- [ ] Visual filter builder работает
- [ ] Segments сохраняются и переиспользуются
- [ ] Cancel / pause работают
- [ ] Preview перед отправкой

### Артефакты
- Миграция для `AudienceSegment`
- `backend/mailer/services/segmentation.py`
- `backend/mailer/models/segment.py`
- `backend/static/ui/mailer/filter-builder.js`
- `backend/ui/views/pages/mailer/segments.py`
- `tests/mailer/test_scheduling.py`
- `tests/mailer/test_segmentation.py`
- `docs/features/email-segmentation.md`

### Валидация
```bash
pytest tests/mailer/
```

### Откат
```bash
git revert
```

### Обновить в документации
- `docs/features/email-segmentation.md`

---

## Checklist завершения волны 6

- [ ] DKIM / SPF / DMARC настроены, mail-tester ≥ 9/10
- [ ] Bounce handling работает, SuppressionList пополняется
- [ ] Unsubscribe page работает, one-click unsubscribe — тоже
- [ ] Template editor с variables, preview, test send
- [ ] Sending pool с warm-up и rate limiting
- [ ] Open / click tracking с метриками
- [ ] Scheduled sends + segmentation с visual builder

**Можно параллельно с Wave 5 и Wave 7.**
