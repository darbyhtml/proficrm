# Волна 5. Live-chat полировка

**Цель волны:** Довести сайт-виджет и операторский интерфейс до уровня профессионального решения (Chatwoot/Intercom-like) в рамках уже выбранной архитектуры (Django + Channels).

**Параллелизация:** высокая. Этапы 5.1–5.4 можно вести параллельно. 5.5–5.7 — после 5.4.

**Длительность:** 10–14 рабочих дней.

**Требования:** Wave 2 завершена (policy + security), Wave 4.2 (Notification Hub) завершена. Уже есть: Inbox, Conversation, Message, WebSocket, SSE, typing indicators, GeoIP.

**Важно:** омниканальность (Telegram/WhatsApp/VK) — НЕ в этой волне. Только sайт-виджет и внутренние улучшения.

---

## Этап 5.1. Операторская панель — UX-редизайн

### Контекст
Текущая панель — vanilla JS + Django templates. Функциональна, но выглядит «кустарно» для сравнения с Chatwoot/Intercom.

### Цель
Пересобрать UI операторской панели с консистентным дизайном (на основе Wave 9 design system, который может идти параллельно), сохранив архитектуру (server-rendered HTML + JS islands).

### Что делать
1. **Layout**:
   - Левая колонка: список диалогов (с фильтрами: My / Unassigned / All / By status).
   - Центральная: активный диалог (messages).
   - Правая: детали контакта + company + последние действия.
   - Collapse-able на мобильных.

2. **Список диалогов**:
   - Аватар / инициалы.
   - Имя + snippet последнего сообщения + timestamp.
   - Unread badge.
   - Typing indicator если кто-то печатает.
   - Priority badge (высокий / средний / низкий).
   - Assigned avatar.

3. **Сообщения**:
   - Bubbles (разный цвет для оператор / клиент / system).
   - Avatar, timestamp, status (sent/delivered/read).
   - Inline images / file previews.
   - Quote reply (цитирование).
   - Markdown-rendering (basic: bold, italic, list, links).

4. **Composer**:
   - Textarea с autoresize.
   - Typing-indicator broadcast.
   - Attachments (файл, картинка, drag & drop).
   - Emoji picker.
   - Canned responses (см. 5.5).
   - Send by Enter / Ctrl+Enter toggle в настройках.

5. **Правая панель**:
   - Карточка контакта (имя, email, phone, company).
   - Последние 5 conversations с этим контактом.
   - Связанные сделки.
   - Теги + notes.
   - Кнопки: «Привязать к company / deal / создать task».

6. **Frontend stack**:
   - Tailwind + Alpine.js (легковес, не SPA).
   - WebSocket через существующий channels endpoint.
   - Vue islands где сложные компоненты — ТОЛЬКО если сильно нужно. По default — Alpine.

### Инструменты
- `frontend-design` skill (если есть)
- `mcp__context7__*` — Alpine.js, Tailwind docs
- `mcp__playwright__*` — визуальная регрессия

### Definition of Done
- [ ] Layout responsive (320px — 1920px)
- [ ] Список диалогов с фильтрами и unread badges
- [ ] Сообщения с inline previews, quote reply, markdown
- [ ] Composer с attachments, emoji, canned responses placeholder
- [ ] Правая панель с карточкой контакта
- [ ] Lighthouse Performance score ≥ 85 на operator panel
- [ ] Visual regression: baseline screenshots зафиксированы

### Артефакты
- `backend/templates/messenger/operator/*.html`
- `backend/static/ui/messenger/operator/*.js`
- `backend/static/ui/messenger/operator/*.css`
- `tests/e2e/test_operator_panel.py`
- `tests/visual/operator_panel.spec.ts`
- `docs/features/operator-panel.md`

### Валидация
```bash
playwright test tests/e2e/test_operator_panel.py
playwright test tests/visual/
```

### Откат
```bash
git revert
```

### Обновить в документации
- `docs/features/operator-panel.md`

---

## Этап 5.2. Widget — полировка

### Контекст
Виджет работает, встраивается через `<script>`. Нужно: полировка UI/UX, улучшение загрузки, privacy, accessibility.

### Цель
Widget-скрипт как профессиональный B2C-tool: < 50KB gzipped, работает на любом сайте, не ломает стили хоста, accessible.

### Что делать
1. **Bundle**:
   - Build pipeline: esbuild / vite. Output: single `widget.min.js` < 50KB gzipped.
   - Versioning: `/widget/v1/widget.min.js?v=<hash>`.
   - Cache headers: 1 year.

2. **Shadow DOM**:
   - Весь widget UI — внутри Shadow DOM. Гарантия: хост-сайт не сломает стили.

3. **Стили**:
   - Configurable theme: primary color, avatar, position (bottom-left / bottom-right), size.
   - Сайт-админ может задать в CRM, скрипт подтягивает config.

4. **Flow**:
   - Greeting bubble (настраиваемый текст).
   - Pre-chat form (опциональная): имя, email, phone, тема. Config per Inbox.
   - Conversation view.
   - Post-conversation survey (см. 5.4).

5. **Accessibility**:
   - ARIA labels везде.
   - Keyboard navigation.
   - Screen reader compatible.
   - Color contrast WCAG AA.

6. **Privacy**:
   - Opt-in checkbox обязательный (Wave 2.7).
   - Ссылка на /privacy/.
   - Cookie notice: widget использует localStorage для session token, explicitly disclosed.

7. **Offline / error state**:
   - Сообщение «Пытаемся восстановить соединение».
   - Retry с exp backoff.
   - Fallback на HTTP polling если WebSocket недоступен.

8. **Preview в CRM**:
   - Страница `/inboxes/<id>/widget-preview/` — live preview виджета с настройками.
   - Embed code generator.

### Инструменты
- Node.js build tools
- `mcp__context7__*`

### Definition of Done
- [ ] Widget bundle < 50KB gzipped
- [ ] Shadow DOM изолирован
- [ ] Configurable theme
- [ ] Pre-chat form + greeting
- [ ] Accessibility WCAG AA (axe-core clean)
- [ ] Privacy opt-in обязателен
- [ ] Offline fallback
- [ ] Preview страница работает

### Артефакты
- `widget/src/*.js` — исходники
- `widget/src/*.css`
- `widget/build-config.js`
- `backend/static/widget/v1/widget.min.js` — built
- `backend/ui/views/pages/inbox/widget_preview.py`
- `backend/templates/pages/inbox/widget_preview.html`
- `tests/widget/*.spec.ts`
- `docs/features/widget.md`
- `docs/runbooks/widget-embed.md`

### Валидация
```bash
cd widget && npm run build && ls -la dist/widget.min.js  # < 50KB
playwright test tests/widget/
axe-core testing автоматизирован
```

### Откат
Откатить версию `?v=` на предыдущий хеш.

### Обновить в документации
- `docs/features/widget.md`
- `docs/runbooks/widget-embed.md`

---

## Этап 5.3. Distribution: round-robin и по навыкам

### Контекст
Сейчас операторы «разбирают» диалоги сами. Нужна автоматическая раздача: round-robin с балансировкой нагрузки + опционально «по навыкам».

### Цель
Новый диалог → автоматически назначается подходящему оператору с учётом онлайн-статуса, текущей нагрузки, skill match.

### Что делать
1. **Operator state** модель (или расширить существующую OperatorPresence):
   - `status: online | away | dnd | offline`
   - `last_activity_at`
   - `active_conversations_count`
   - `max_concurrent_conversations` (config per user)
   - `skills: list[Skill]` (M2M)

2. **Skill** модель:
   - Тег навыков: `{"ru", "en", "tendering", "billing", "technical"}`.
   - Юзер может иметь несколько.
   - Inbox может иметь required skills.

3. **Distribution service**:
   ```python
   def assign_conversation(conversation) -> User | None:
       candidates = get_online_operators_for_inbox(conversation.inbox)
       if conversation.required_skills:
           candidates = filter_by_skills(candidates, conversation.required_skills)
       if not candidates:
           return None  # fallback: unassigned pool
       # round-robin: оператор с наименьшим active_conversations_count
       return min(candidates, key=lambda u: (u.active_conversations_count, u.last_assigned_at))
   ```

4. **Auto-reassignment** при timeout:
   - Если оператор не ответил за X минут (configurable) и клиент всё ещё в диалоге — переназначение.
   - Уведомление бывшему оператору + новому.

5. **Escalation**:
   - Если диалог > Y минут без ответа — эскалация на SALES_HEAD с alerting.

6. **Config UI**:
   - Admin-страница: настройка правил.
   - Skill management.
   - Inbox → required skills.

7. **Manual override**:
   - Оператор может «взять» диалог вручную.
   - Может «передать» коллеге.

### Definition of Done
- [ ] Автораспределение работает
- [ ] Skills matching работает
- [ ] Reassignment по timeout работает
- [ ] Escalation работает
- [ ] UI для конфигурации
- [ ] Manual override работает

### Артефакты
- Миграции для Skill, расширения Operator state
- `backend/messenger/services/distribution.py`
- `backend/messenger/services/escalation.py`
- `backend/ui/views/pages/admin/skills.py`
- `backend/celery/tasks/reassign_stale.py`
- `tests/messenger/test_distribution.py`
- `docs/features/distribution.md`

### Валидация
```bash
pytest tests/messenger/test_distribution.py
```

### Откат
```bash
git revert
# Отключить auto-assignment через feature flag
```

### Обновить в документации
- `docs/features/distribution.md`

---

## Этап 5.4. Conversation rating + post-chat survey

### Контекст
Нет механизма оценки работы оператора. Менеджмент хочет видеть CSAT (customer satisfaction).

### Цель
В конце диалога (auto или manual close) — клиент получает короткий опрос.

### Что делать
1. **Модели**:
   - `ConversationRating`: 1-5 звёзд + optional текст.
   - `ConversationSurvey`: опросник с несколькими вопросами (конфигурабельно).
   - `SurveyResponse`: ответы.

2. **Auto-close**:
   - Диалог без активности > X часов → статус `resolved`.
   - Через Y минут после `resolved` — отправка опроса в widget.

3. **Widget UX**:
   - Non-blocking banner: «Как вам помогли? [⭐⭐⭐⭐⭐]».
   - Если низкая оценка (1-2) — follow-up: «Что можно улучшить?».

4. **Email survey** (опционально):
   - Для диалогов где клиент указал email, но покинул страницу — email через час после close.

5. **Admin dashboard**:
   - Средний рейтинг по оператору / inbox / branch.
   - Топ жалоб (из open-text responses).
   - Интеграция в Wave 8 (аналитика).

6. **Permissions**:
   - MANAGER видит свои ratings.
   - SALES_HEAD видит по branch.
   - ADMIN — все.

### Definition of Done
- [ ] Rating widget виден после close
- [ ] Rating сохраняется
- [ ] Email survey работает (опционально)
- [ ] Admin dashboard
- [ ] Permissions

### Артефакты
- Миграции
- `backend/messenger/models/rating.py`
- `backend/messenger/services/survey.py`
- `backend/static/widget/survey.js`
- `backend/ui/views/pages/ratings.py`
- `tests/messenger/test_rating.py`
- `docs/features/ratings.md`

### Валидация
```bash
pytest tests/messenger/test_rating.py
```

### Откат
```bash
git revert
```

### Обновить в документации
- `docs/features/ratings.md`

---

## Этап 5.5. Canned responses + quick replies

### Контекст
Операторы часто пишут одни и те же ответы. Ускорить через шаблоны.

### Цель
Библиотека шаблонов ответов, доступ по keyboard shortcut.

### Что делать
1. **Модели**:
   - `CannedResponse`:
     - `shortcut` (slash-command: `/greeting`, `/price`)
     - `title`
     - `body` (with variables: `{contact.name}`, `{operator.name}`, `{company.name}`)
     - `scope: personal | team | inbox | global`
     - `owner` (если personal)
     - `inbox` (если inbox-scoped)
     - `usage_count` для sorting

2. **UI в composer**:
   - Набираешь `/` → autocomplete popup с fuzzy search.
   - Tab — выбрать.
   - Enter — вставить с подставленными переменными.

3. **Admin UI**:
   - CRUD canned responses.
   - Группировка, фильтры, теги.
   - Per-role: MANAGER может создавать только personal, SALES_HEAD — team, ADMIN — global.

4. **Variables**:
   - Библиотека переменных с авто-подстановкой по контексту диалога.
   - Jinja-подобный синтаксис: `{% if contact.first_name %}...{% endif %}`.

5. **Analytics**:
   - Top-used shortcuts.
   - Per-operator usage.

### Definition of Done
- [ ] CannedResponse CRUD работает
- [ ] Composer integration с `/` autocomplete
- [ ] Variables подставляются
- [ ] Scope permissions работают
- [ ] Usage analytics

### Артефакты
- Миграции
- `backend/messenger/models/canned_response.py`
- `backend/messenger/services/canned_service.py`
- `backend/static/ui/messenger/canned-autocomplete.js`
- `backend/ui/views/pages/admin/canned_responses.py`
- `tests/messenger/test_canned.py`
- `docs/features/canned-responses.md`

### Валидация
```bash
pytest tests/messenger/test_canned.py
```

### Откат
```bash
git revert
```

### Обновить в документации
- `docs/features/canned-responses.md`

---

## Этап 5.6. Internal chat (между операторами)

### Контекст
Операторам иногда нужно обсудить клиента между собой без видимости клиента. Сейчас — нет.

### Цель
Внутренние заметки в диалоге + отдельный team chat.

### Что делать
1. **Internal notes в диалоге**:
   - Message.is_internal: bool.
   - Визуально: жёлтый bubble, «только для оператора».
   - @mention коллеги → уведомление.

2. **Team chat**:
   - Отдельный Inbox типа `internal_team`.
   - Каналы: general, random, branch-specific.
   - Direct messages между операторами.

3. **Совместимость с Widget**:
   - Internal messages НИКОГДА не уходят в widget.
   - Защита на уровне БД + serializer.

### Definition of Done
- [ ] Internal notes в диалоге
- [ ] Team chat работает (каналы + DM)
- [ ] Internal messages не видны клиенту (тесты)

### Артефакты
- Миграции
- `backend/messenger/models/internal.py`
- `backend/messenger/services/internal_chat.py`
- `backend/ui/views/pages/team_chat.py`
- `tests/messenger/test_internal.py`
- `docs/features/internal-chat.md`

### Валидация
```bash
pytest tests/messenger/test_internal.py
# Critical: test_internal_message_never_sent_to_widget
```

### Откат
```bash
git revert
```

### Обновить в документации
- `docs/features/internal-chat.md`

---

## Этап 5.7. File/voice messages + reactions

### Контекст
Расширить типы сообщений: файлы (любые), войсы, эмодзи-реакции на сообщения.

### Цель
Довести message types до паритета с современными чатами.

### Что делать
1. **Файлы**:
   - Уже частично есть (image attachments). Расширить на любые.
   - Preview: PDF (иконка + название), архивы (zip/rar), офисные файлы.
   - Защита: ClamAV сканирование (если можно), MIME type validation, размер лимит (25MB default).
   - Хранение: S3 (Wave 10 зависимость).

2. **Voice messages**:
   - Widget: Record button (MediaRecorder API), формат opus/webm.
   - Operator: загрузка audio file.
   - UI: аудиоплеер с waveform visualization.
   - Transcription: опционально V2 (через Whisper API).

3. **Reactions**:
   - Emoji reactions on messages (👍 ❤️ 😂 😮 😢 🙏).
   - UI: hover over message → emoji picker.
   - Модель `MessageReaction`.
   - Realtime update через WebSocket.

4. **Read receipts**:
   - Message.read_at (уже может быть).
   - UI: «прочитано» с галочками как в мессенджерах.

### Definition of Done
- [ ] Файлы до 25MB работают, preview рендерится
- [ ] Voice messages: record + playback
- [ ] Reactions работают, realtime updates
- [ ] Read receipts видны

### Артефакты
- Миграции
- `backend/messenger/services/attachments.py`
- `backend/static/widget/voice-recorder.js`
- `backend/static/ui/messenger/reactions.js`
- `backend/static/ui/messenger/waveform.js`
- `tests/messenger/test_attachments.py`
- `tests/messenger/test_voice.py`
- `tests/messenger/test_reactions.py`

### Валидация
```bash
pytest tests/messenger/
```

### Откат
```bash
git revert
```

### Обновить в документации
- `docs/features/messaging-types.md`

---

## Checklist завершения волны 5

- [ ] Operator panel — редизайн, responsive, accessible
- [ ] Widget — < 50KB, shadow DOM, customizable
- [ ] Auto-distribution по round-robin + skills
- [ ] Rating + survey работают
- [ ] Canned responses с autocomplete
- [ ] Internal chat + team chat
- [ ] File / voice / reactions
- [ ] Playwright E2E: полный chat flow (widget → operator → close → rating)

**Можно параллельно с Wave 6 и Wave 7.**
