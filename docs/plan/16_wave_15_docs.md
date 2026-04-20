# Волна 15. Документация — финальный штрих

**Цель волны:** Собрать всю документацию в единую, доступную структуру. Пользовательскую — чтобы менеджеры работали без звонков в саппорт. Админскую — чтобы ты или второй человек мог поднять систему с нуля. Разработческую — чтобы не забыть, почему что так устроено.

**Принцип:** Документация — не «написать однажды», а «обновлять с кодом». Но сначала нужен первый дамп.

**Параллелизация:** средняя. 4 этапа, 15.1 и 15.4 независимы, 15.2 и 15.3 — основной стрим.

**Длительность:** 5–8 рабочих дней.

**Требования:** Все волны W0–W14 завершены (документируем рабочий продукт, не прототип).

---

## Этап 15.1. Пользовательская документация и help-center

### Контекст
Сейчас менеджеры учатся из звонков тебе и старожилам. Новый менеджер = 2 дня onboarding + постоянные вопросы. Help-center с поиском решает это.

### Цель
Help-center внутри CRM + PDF onboarding-гайд.

### Что делать
1. **Структура help-center**:
   ```
   /help/
   ├── getting-started/
   │   ├── first-login.md
   │   ├── interface-overview.md
   │   └── user-roles.md
   ├── companies/
   │   ├── create-company.md
   │   ├── merge-duplicates.md
   │   ├── transfer-responsible.md
   │   ├── custom-fields.md
   │   └── bulk-actions.md
   ├── deals/
   │   ├── pipeline-stages.md
   │   ├── create-deal.md
   │   └── close-deal.md
   ├── tasks/
   │   ├── create-task.md
   │   ├── task-templates.md
   │   └── recurring-tasks.md
   ├── calls/
   │   ├── phone-pairing.md
   │   ├── make-call.md
   │   └── incoming-calls.md
   ├── chat/
   │   ├── operator-panel.md
   │   ├── auto-distribution.md
   │   └── canned-responses.md
   ├── email/
   │   ├── create-campaign.md
   │   ├── templates.md
   │   ├── segments.md
   │   └── bounce-handling.md
   ├── analytics/
   │   ├── sales-dashboard.md
   │   ├── manager-kpi.md
   │   └── exports.md
   ├── settings/
   │   ├── notifications.md
   │   ├── two-factor.md
   │   └── profile.md
   └── faq/
       ├── common-issues.md
       └── glossary.md
   ```

2. **Формат каждой статьи**:
   - Markdown.
   - Frontmatter: `title`, `role` (кому релевантно), `tags`.
   - Начало: короткое описание «что это и зачем».
   - Шаги со скриншотами (2–5 шагов).
   - Видео-gif если действие нелинейное (3–10 сек).
   - «Частые ошибки» в конце.
   - Ссылки на связанные статьи.

3. **Скриншоты**:
   - Автоматизируй через Playwright: скрипт `scripts/generate-docs-screenshots.py`.
   - Каждая статья объявляет какой URL открыть и какие элементы подсветить.
   - Обновляются по команде (не с каждым PR).
   - Сохранение в `docs/help/screenshots/<article-slug>/<step>.png`.

4. **Search**:
   - PostgreSQL FTS по title + content.
   - Typo-tolerance: pg_trgm similarity.
   - Highlighting результатов.

5. **UI help-center**:
   - Route `/help/` — index с категориями.
   - `/help/<category>/<slug>/` — статья.
   - Sidebar: категории + поиск.
   - Contextual help: `?` иконка на сложных страницах → popover с ссылкой на статью.

6. **Видео-туры** (опционально):
   - Intro (3 мин): «Что такое ProfiCRM» — для новых менеджеров.
   - Роли (5 мин × 6): специфика для MANAGER / TENDERIST / SALES_HEAD / BRANCH_DIRECTOR / GROUP_MANAGER / ADMIN.
   - Запись через Loom или OBS, хостинг на собственном S3 или YouTube unlisted.

7. **PDF-гайд для новичка**:
   - 20–30 страниц.
   - Step-by-step: первый день, первая неделя, первый месяц.
   - Генерация из markdown через Pandoc (скрипт).
   - Обновляется автоматически при изменении help-center.

### Инструменты
- `mcp__playwright__*` — скриншоты.
- `mcp__postgres__*` — FTS index.
- **Skill**: `/mnt/skills/public/docx/SKILL.md` или `/mnt/skills/public/pdf/SKILL.md` для PDF-гайда.
- **Subagent**: `docs-writer` (параллельно пишет по 5 статей в каждой категории).

### Definition of Done
- [ ] 40+ статей в help-center.
- [ ] Все роли имеют минимум 5 статей.
- [ ] Скриншоты автогенерируются.
- [ ] Search FTS работает с typo-tolerance.
- [ ] Contextual help `?` на 20+ страницах.
- [ ] PDF onboarding guide 20+ страниц.
- [ ] 3+ видео-тура (опционально).
- [ ] Help-center доступен по `/help/` — все роли.
- [ ] A/B показал: новые менеджеры спрашивают саппорт на 50%+ меньше после 1 недели.

### Артефакты
- `docs/help/**/*.md`
- `docs/help/screenshots/**/*.png`
- `backend/help_center/` — Django app: модели + views + search.
- `scripts/generate-docs-screenshots.py`
- `scripts/build-pdf-guide.sh`
- `docs/onboarding-guide.pdf`

### Валидация
```bash
# Все статьи парсятся
find docs/help -name '*.md' | xargs -I {} python -c "import frontmatter; frontmatter.load('{}')"

# FTS index
docker compose exec db psql -c "SELECT * FROM help_article_fts WHERE article_fts @@ 'company' LIMIT 5;"

# Help-center UI
curl https://crm.groupprofi.ru/help/ | grep '40 articles'
```

### Откат
Help-center — additive feature, откат отключает `/help/` роут, статьи остаются в репо.

### Обновить в документации
- `docs/architecture/help-center.md` — как работает: Markdown → Django view.
- CLAUDE.md: «При добавлении фичи — обязательно статья в `docs/help/`».

---

## Этап 15.2. Админская документация и runbooks

### Контекст
Ты — single-point-of-failure. Если ты в отпуске / болеешь / уходишь — никто не знает как развернуть, бэкапить, восстановить. 40+ runbooks уже есть (из W10), нужно привести к единообразию и дополнить.

### Цель
Admin guide: всё, что нужно человеку чтобы поднять и поддерживать ProfiCRM.

### Что делать
1. **Структура admin docs**:
   ```
   docs/admin/
   ├── index.md                          # Оглавление
   ├── overview/
   │   ├── architecture.md               # Схема системы (что с чем говорит)
   │   ├── stack.md                      # Django / Postgres / Redis / Celery — версии и роли
   │   └── directory-structure.md        # Что где лежит
   ├── deployment/
   │   ├── initial-setup.md              # С нуля: от чистого Ubuntu до работающего prod
   │   ├── env-variables.md              # Все env-vars с описанием
   │   ├── secrets-management.md         # Как хранить Fernet, DKIM, SMTP
   │   ├── dns-setup.md                  # A/MX/SPF/DKIM/DMARC
   │   └── tls-certificates.md           # Certbot + renewal
   ├── operations/
   │   ├── daily-checks.md               # Что проверять утром (Grafana, Sentry)
   │   ├── deploy.md                     # Как выкатить релиз
   │   ├── rollback.md                   # Как откатить
   │   ├── database-migrations.md        # Как накатывать миграции безопасно
   │   └── feature-flags.md              # Как включать/выключать фичи
   ├── backup-recovery/
   │   ├── backup-strategy.md            # WAL-G + daily + retention
   │   ├── restore-procedure.md          # PITR step-by-step
   │   ├── disaster-recovery.md          # Полный DR плейбук
   │   └── backup-verification.md        # Как проверить что бэкапы рабочие
   ├── monitoring/
   │   ├── grafana-dashboards.md         # Что смотреть на каких дашбордах
   │   ├── alerts.md                     # Что значит каждый алерт + как реагировать
   │   ├── sentry.md                     # Как триажить ошибки
   │   └── log-analysis.md               # Loki queries
   ├── troubleshooting/
   │   ├── high-cpu.md
   │   ├── high-memory.md
   │   ├── slow-queries.md
   │   ├── websocket-drops.md
   │   ├── email-not-sending.md
   │   ├── fcm-push-failing.md
   │   └── celery-stuck.md
   ├── security/
   │   ├── incident-response.md          # Что делать при компрометации
   │   ├── user-access-audit.md          # Как провести аудит
   │   ├── 2fa-reset.md                  # Как сбросить 2FA пользователю
   │   └── 152-fz-compliance.md          # Процесс работы с запросами субъектов ПД
   └── runbooks/
       ├── 00_index.md                   # Список runbooks
       ├── 01_initial_deploy.md
       ├── 02_regular_deploy.md
       ├── ... (40+ runbooks из W10)
       └── 40_yearly_security_audit.md
   ```

2. **Формат каждого runbook**:
   - Заголовок: короткий и поисковый («Восстановление Postgres из WAL-G», не «Процедура восстановления БД»).
   - Prerequisites: что должно быть установлено / какие доступы нужны.
   - Time estimate: сколько это займёт.
   - Severity: какие последствия если не сделать / сделать неверно.
   - Step-by-step: пронумерованные команды с ожидаемым выводом.
   - Verification: как убедиться что получилось.
   - Rollback: как откатить если что.
   - Related: ссылки на связанные runbook.

3. **Incident response plan**:
   - Severity matrix (SEV1/SEV2/SEV3/SEV4).
   - Roles: IC (incident commander), Comms, Ops.
   - Communication templates (статус-письмо пользователям).
   - Post-mortem template.

4. **Onboarding checklist для нового админа** (если появится):
   - SSH ключи, доступы (Netangels, Sentry, Grafana, Domain registrar).
   - Пароли из vault (Bitwarden / 1Password).
   - Знакомство со стеком: 1 неделя shadow + 1 неделя вместе + 1 неделя solo with review.

5. **Architecture diagrams**:
   - High-level (контекст): ProfiCRM + внешние системы (SMTP, FCM, Android, сайт GroupProfi).
   - Component: Django, Celery, Postgres, Redis, nginx, LiveKit — связи.
   - Data flow: как письмо уходит, как звонок идёт, как сообщение из чата приходит.
   - Deployment: физическая топология (сервер Netangels + DNS + CloudFlare если есть).
   - Инструмент: Mermaid или draw.io, сохраняем и исходник и PNG.

### Инструменты
- **Skill**: `/mnt/skills/public/docx/SKILL.md` — если нужен PDF export.
- **Subagent**: `admin-docs-writer` (пишет параллельно по 5 runbooks).
- Grafana export dashboards → JSON → ссылки в docs.

### Definition of Done
- [ ] 40+ runbooks переписаны в едином формате.
- [ ] Overview section: architecture + stack + directory.
- [ ] Deployment: с нуля на чистом Ubuntu — возможно по инструкции.
- [ ] Operations: 5 ежедневных процедур расписаны.
- [ ] Backup-recovery: DR плейбук + проверено учением.
- [ ] Monitoring: все алерты имеют runbook.
- [ ] Troubleshooting: 10+ типовых проблем.
- [ ] Security: incident response plan.
- [ ] 5+ architecture diagrams (Mermaid).
- [ ] Проверка: «попроси друга (не-админа) по docs развернуть копию на тестовой VM» — удалось за день.

### Артефакты
- `docs/admin/**/*.md`
- `docs/admin/diagrams/*.mmd` + `.png`
- `docs/admin/onboarding-checklist.md`

### Валидация
```bash
# Все markdown парсятся
find docs/admin -name '*.md' | xargs -I {} markdown-link-check {}

# Mermaid рендерится
find docs/admin/diagrams -name '*.mmd' | xargs -I {} mmdc -i {} -o {}.png

# DR learning
# Тест: восстановить staging из бэкапа на отдельный сервер по docs — должно получиться за <4 часа
```

### Откат
Документация не ломается — только пополняется.

### Обновить в документации
- CLAUDE.md: «Любая infra-изменение → обновление runbook или создание нового».

---

## Этап 15.3. Разработческая документация

### Контекст
CLAUDE.md + docs/{architecture,decisions,problems-solved,current-sprint,roadmap}.md — есть. Нужно довести до уровня «новый разработчик за 1 день понимает систему, за 1 неделю пишет первый PR».

### Цель
Complete developer documentation.

### Что делать
1. **Структура**:
   ```
   docs/dev/
   ├── getting-started.md               # С нуля до первого PR
   ├── architecture/
   │   ├── overview.md
   │   ├── apps-structure.md            # Django apps и их ответственность
   │   ├── services-layer.md            # Паттерн service классов
   │   ├── views-layer.md               # pages/ vs api/v1/
   │   ├── policy-engine.md             # Как работает авторизация
   │   ├── data-scope.md                # Видимость данных
   │   ├── audit-logging.md             # Как логируются изменения
   │   ├── notifications.md             # Notification Hub
   │   ├── livechat.md                  # Chatwoot-style архитектура
   │   ├── email-campaigns.md
   │   ├── phonebridge.md               # Android click-to-call
   │   ├── feature-flags.md             # django-waffle
   │   └── performance-tuning.md
   ├── decisions/                       # ADR (Architecture Decision Records)
   │   ├── 001-django-over-fastapi.md
   │   ├── 002-postgres-not-clickhouse.md
   │   ├── 003-templates-not-spa.md
   │   ├── 004-kotlin-not-flutter.md
   │   ├── 005-single-tenant.md
   │   ├── 006-policy-engine-design.md
   │   ├── 007-notification-hub.md
   │   ├── 008-data-scope-unified.md
   │   ├── 009-s3-storage.md
   │   ├── 010-wal-g-backups.md
   │   └── ...
   ├── patterns/
   │   ├── service-class.md             # Как писать CompanyService
   │   ├── policy-required.md           # Как применять @policy_required
   │   ├── audit-decorator.md           # Как писать @audit
   │   ├── scope-queryset.md            # Как использовать Scope
   │   ├── celery-task.md               # Pattern for tasks
   │   ├── websocket-consumer.md        # channels consumer
   │   ├── drf-viewset.md               # API viewset structure
   │   └── test-factory.md              # factory_boy usage
   ├── guides/
   │   ├── add-new-model.md
   │   ├── add-new-permission.md
   │   ├── add-notification-type.md
   │   ├── add-email-trigger.md
   │   ├── add-widget-feature.md
   │   └── refactor-view.md
   ├── contribution/
   │   ├── git-workflow.md
   │   ├── commit-messages.md
   │   ├── code-review.md
   │   ├── testing-strategy.md
   │   └── ci-cd.md
   ├── troubleshooting/
   │   ├── migrations-conflict.md
   │   ├── celery-not-running.md
   │   ├── websocket-not-connecting.md
   │   └── tests-flaky.md
   └── glossary.md                      # Термины: Lead vs Company, Branch, Scope, Policy
   ```

2. **ADR формат**:
   ```markdown
   # ADR-001: Django over FastAPI
   
   - **Status**: Accepted
   - **Date**: 2026-02-15
   - **Deciders**: Dmitry
   
   ## Context
   [Почему возник вопрос]
   
   ## Options considered
   - Option A: Django (выбран)
   - Option B: FastAPI + Next.js
   - Option C: Mixed (Django + FastAPI gateway)
   
   ## Decision
   [Что выбрано]
   
   ## Consequences
   - Positive: ORM, admin, forms — 60% CRUD бесплатно.
   - Negative: tight coupling templates и бизнес-логика.
   
   ## Revisit
   [Когда пересмотреть: если SaaS mode, если команда > 3]
   ```

3. **Code examples**:
   - Каждый pattern → реальный снипет из кода.
   - «Так правильно» / «Так не надо» с объяснением.

4. **Glossary**:
   - **Company** — юр. лицо, клиент. Не путать с Lead.
   - **Lead** — сейчас не отдельная модель (sic!), см. ADR-???.
   - **Deal** — сделка, всегда к Company, без pipeline'ов.
   - **Branch** — филиал GroupProfi (ЕКБ/Тюмень/Краснодар).
   - **Scope** — область видимости данных per role.
   - **Policy** — правило доступа в PolicyRule.
   - **Responsible** — FK User, чей клиент.
   - ...

5. **`getting-started.md`**:
   - Prerequisites (Docker, git, Python 3.13).
   - Clone + env + docker compose up.
   - Первый запуск тестов.
   - Первое изменение и PR.
   - За 1 день должен пройти полный path.

6. **CLAUDE.md update**:
   - Полностью переписать с учётом всех новых паттернов.
   - Ссылки на все ADR.
   - «How to use Claude Code in this repo» — скиллы, MCP, subagents.

### Инструменты
- **Skill**: `/mnt/skills/public/skill-creator/SKILL.md` — если нужен свой skill для проекта.
- **Subagent**: `dev-docs-writer`.

### Definition of Done
- [ ] 15+ ADR документированы.
- [ ] 8+ architecture docs.
- [ ] 10+ patterns с примерами.
- [ ] 6+ how-to guides.
- [ ] Glossary 30+ терминов.
- [ ] `getting-started.md` — новый разработчик на тестовой VM разворачивает за ≤4 часа.
- [ ] CLAUDE.md обновлён с полным обзором + ссылками.
- [ ] Все linked markdown files — no broken links.
- [ ] Каждый Django app имеет `README.md` в корне.

### Артефакты
- `docs/dev/**/*.md`
- `CLAUDE.md` (обновлённый)
- `backend/<app>/README.md` (для каждого app)

### Валидация
```bash
# No broken links
find docs/dev -name '*.md' | xargs -I {} markdown-link-check {}

# Onboarding test
# Дай доки другу-Python-разработчику — пусть поднимет локально по docs/dev/getting-started.md
```

### Откат
Документация не ломается.

### Обновить в документации
- CLAUDE.md: финализировать как «источник истины» для Claude Code.

---

## Этап 15.4. Обучающие материалы и внутренние видео

### Контекст
Документация — reference. Для первого знакомства нужны видео. Для команды менеджеров важно увидеть, не прочитать.

### Цель
Onboarding video course для менеджеров + admin и dev videos для тебя/будущих коллег.

### Что делать
1. **Manager onboarding course** (15–20 видео по 2–5 мин):
   - 01. Первый вход и интерфейс (2 мин).
   - 02. Создание компании (3 мин).
   - 03. Поиск и фильтры (2 мин).
   - 04. Карточка компании обзор (4 мин).
   - 05. Создание сделки (3 мин).
   - 06. Задачи — создание и планирование (4 мин).
   - 07. Звонки через телефон (3 мин).
   - 08. Рассылки — создание первой (5 мин).
   - 09. Шаблоны писем (3 мин).
   - 10. Live-chat — панель оператора (5 мин).
   - 11. Уведомления и настройки (2 мин).
   - 12. Моя аналитика (3 мин).
   - 13. 2FA настройка (2 мин).
   - 14. Горячие клавиши (2 мин).
   - 15. Частые ошибки новичка (5 мин).

2. **Role-specific videos** (по 1 для каждой):
   - TENDERIST (5 мин): специфика тендеров.
   - SALES_HEAD (5 мин): отчёты по команде.
   - BRANCH_DIRECTOR (7 мин): управление филиалом.
   - GROUP_MANAGER (7 мин): управление группой.

3. **Admin videos** (внутренние):
   - Deploy на staging (5 мин).
   - Deploy на prod (7 мин).
   - Восстановление из бэкапа (10 мин).
   - Разбор инцидента (10 мин).

4. **Dev videos** (для будущего второго разработчика):
   - Локальная разработка (10 мин).
   - Паттерн service class (15 мин).
   - Policy engine deep-dive (20 мин).
   - Feature flags — как добавить (10 мин).

5. **Production process**:
   - Запись через OBS Studio (бесплатно).
   - Микрофон: любой петличный или headset от 3000₽.
   - Монтаж: DaVinci Resolve Free / CapCut.
   - Subtitles автогенерация через Whisper.
   - Хостинг:
     - Менеджерские — внутри CRM в `/help/videos/` (S3 backend).
     - Admin/dev — приватный YouTube unlisted или own S3.

6. **Script template**:
   - Intro (10 сек): «Привет, в этом видео научу ...»
   - Content (hook → problem → solution → example).
   - Outro (10 сек): «В следующем видео — ...»

7. **Live training sessions** (опционально):
   - Monthly Q&A для менеджеров.
   - Quarterly feature-announcement.
   - Записи становятся частью help-center.

### Инструменты
- OBS Studio / CapCut.
- Whisper (via openai-whisper или local faster-whisper) для субтитров.
- **Skill**: — (ручная работа).

### Definition of Done
- [ ] 15+ manager onboarding videos с субтитрами (RU).
- [ ] 4 role-specific videos.
- [ ] 4 admin videos.
- [ ] 4 dev videos (опционально — можно отложить).
- [ ] Videos вбиты в help-center с привязкой к статьям.
- [ ] Новый менеджер смотрит 01–05 за первый час + первая работа в CRM.
- [ ] Каждое видео <5 мин (кроме deep-dives).
- [ ] Feedback форма под каждым видео.

### Артефакты
- `docs/help/videos/*.mp4` (в S3 bucket).
- `docs/help/videos/scripts/*.md` (сценарии).
- `docs/help/videos/subtitles/*.vtt`.

### Валидация
- Соберите фокус-группу из 3 новых менеджеров (new hire в Тюмени/ЕКБ/Краснодаре).
- Пусть пройдут course без помощи.
- Замерьте: сколько дошли до конца первой задачи самостоятельно. Цель: 90%+.

### Откат
Видео — additive, убираются из UI help-center одним config flag.

### Обновить в документации
- `docs/help/index.md` — ссылки на video course.
- CLAUDE.md: «При значимой UX-фиче — обновить соответствующее видео (или отметить как устаревшее)».

---

## Итог волны 15

После этой волны:
- **Менеджеры** самостоятельны — help-center + videos покрывают 90%+ вопросов.
- **Админ** заменим — runbooks позволяют второму человеку поднять и поддержать.
- **Разработчики** быстро заходят — getting-started + ADR + patterns дают контекст за неделю.
- **Документация живая** — встроена в процесс (обновление с кодом).

### DoD волны
- [ ] 15.1 Help-center + PDF guide
- [ ] 15.2 Admin docs + 40+ runbooks
- [ ] 15.3 Dev docs + 15+ ADR + обновлённый CLAUDE.md
- [ ] 15.4 Video course — минимум manager-пакет (4 необязательны)
- [ ] `docs/README.md` — навигация по всей документации
- [ ] В CI: `markdown-link-check` на `docs/**/*.md`

---

## Общий итог проекта

После завершения всех 16 волн (W0–W15):

1. **Аудит + baseline** (W0).
2. **Чистая архитектура** (W1).
3. **Безопасность ENFORCE** (W2).
4. **Стабильный core CRM** (W3).
5. **Надёжные задачи и уведомления** (W4).
6. **Полированный live-chat** (W5).
7. **Harden email-campaigns** (W6).
8. **Готовая телефония + Android-app** (W7).
9. **Полная аналитика + экспорты** (W8).
10. **Унифицированный UX/UI** (W9).
11. **Production-grade infrastructure** (W10).
12. **API v1 с OpenAPI + keys** (W11).
13. **Интеграции (UTM / IMAP)** (W12).
14. **Performance tuning** (W13).
15. **Тотальный QA** (W14).
16. **Документация** (W15).

**Оценка:** 4–6 месяцев solo-работы при 6–8 часах в день и 2–3 параллельных Claude Code сессиях (разные worktree).

**Результат:** продуктовое состояние для продажи как SaaS (после отделения multi-tenant в V2) или надёжная внутренняя система для 50 менеджеров GroupProfi с запасом роста до 500+.
