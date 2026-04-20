# Волна 13. Performance & Optimization

**Цель волны:** После всех предыдущих волн — финальная оптимизация. N+1 hunt, query optimization через pg_stat_statements, caching audit, frontend bundle size, image optimization.

**Параллелизация:** средняя.

**Длительность:** 7–10 рабочих дней.

**Требования:** Wave 0.6 (baseline метрики) есть. Wave 10 (pg_stat_statements установлен) есть.

---

## Этап 13.1. N+1 hunt

### Контекст
В CRM с 66 моделями и множеством FK — N+1 встречается часто. После рефакторинга Wave 1 часть могла стать хуже или лучше.

### Цель
Выявить все N+1 в hot paths и исправить через select_related / prefetch_related.

### Что делать
1. **Tooling**:
   - django-silk на staging (middleware с toggle).
   - django-debug-toolbar в dev.
   - nplusone-django (detection via middleware).
   - pytest-django's assertNumQueries.

2. **Hot paths hunt**:
   - Company list → load with pages (silk profiles).
   - Company detail → все tabs.
   - Deal list / detail.
   - Chat operator panel open (loads conversations + messages).
   - Analytics dashboards.
   - Email campaign send preview.

3. **Fix pattern**:
   - `select_related('fk_field')` для FK и OneToOne.
   - `prefetch_related('m2m_field', 'reverse_fk')` для M2M и reverse FK.
   - `Prefetch('messages', queryset=Message.objects.select_related('sender'))` для вложенных.
   - `annotate` + `aggregate` вместо loops в views.

4. **QueryCountMiddleware**:
   - На staging отметить все response'ы с > 30 queries как warning.
   - Alert в Sentry.

5. **Tests**:
   - Для каждого fixed place — `assertNumQueries(X)` test.

### Инструменты
- `mcp__postgres__*` — EXPLAIN ANALYZE
- `django-silk`, `nplusone-django`

### Definition of Done
- [ ] N+1 обнаружены и исправлены в 10+ hot paths
- [ ] `assertNumQueries` tests добавлены
- [ ] QueryCountMiddleware alerts настроен

### Артефакты
- Множество изменений в views/services/querysets
- `tests/performance/test_query_counts.py`
- `backend/core/middleware/query_count.py`
- `docs/audit/n-plus-1-fixes.md`

### Валидация
```bash
pytest tests/performance/test_query_counts.py
# Staging: открыть Silk, проверить query count per page
```

### Откат
```bash
git revert
```

### Обновить в документации
- `docs/audit/n-plus-1-fixes.md`

---

## Этап 13.2. Indexes + query optimization

### Контекст
pg_stat_statements показывает топ медленных запросов (из Wave 0.6 baseline). Нужно добавить индексы где надо.

### Цель
Топ-20 медленных запросов — оптимизированы. Нет queries > 500ms в hot paths.

### Что делать
1. **Collect top slow**:
   - `SELECT query, calls, mean_exec_time FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 50;`
   - Отфильтровать admin-только queries, migrations.
   - Оставить топ-20 product-related.

2. **Per-query analysis**:
   - EXPLAIN ANALYZE.
   - Поиск missing index (`Seq Scan on large_table` — кандидат).
   - Compound index vs single column — обсудить.
   - Partial index для условий (e.g., `WHERE is_active = true`).

3. **Common patterns**:
   - `companies_company(branch_id, responsible_id, created_at)` — compound для typical filter.
   - `messenger_message(conversation_id, created_at)` — уже должен быть.
   - `mailer_campaignrecipient(campaign_id, status)` — для group by status.
   - GIN на JSONB (custom_fields) — для filter.
   - Trigram index на name / phone для FTS.

4. **Migrations**:
   - `CREATE INDEX CONCURRENTLY` — чтобы не блокировать write'ы.
   - RunSQL миграция с обратной операцией.

5. **Verification**:
   - После индекса — re-run query, confirm Index Scan.
   - Load test improvement.

6. **Denormalization** (selective):
   - `Company.last_activity_at` denormalized (вместо MAX запроса) — triggered on events.
   - `CompanyDeal.company_cached_name` — для list view без join.
   - Только когда profiling показывает реальный выигрыш.

### Инструменты
- `mcp__postgres__*`

### Definition of Done
- [ ] Топ-20 медленных запросов оптимизированы
- [ ] Новые индексы добавлены через CONCURRENTLY migrations
- [ ] Denormalization (если решили) — с триггерами/signals
- [ ] p95 на hot paths < 500ms

### Артефакты
- Миграции с индексами
- `docs/audit/postgres-optimizations.md`

### Валидация
```bash
# After indexes:
SELECT query, mean_exec_time FROM pg_stat_statements WHERE ... ORDER BY mean_exec_time DESC;
# Все topы должны быть быстрее
```

### Откат
```bash
# Drop indexes migration
```

### Обновить в документации
- `docs/audit/postgres-optimizations.md`

---

## Этап 13.3. Redis caching audit

### Контекст
Каче есть (django cache), но используется неконсистентно.

### Цель
Определить, что кешировать, с каким TTL, и invalidation strategy.

### Что делать
1. **Inventory**:
   - Какие данные читаются чаще, чем пишутся?
   - Кандидаты: PolicyRule list (читается на каждом request), user roles, feature flags, inbox list для operator.

2. **Caching layers**:
   - Request-scoped: `request.cache` dict (для сущностей, читаемых в рамках request'a).
   - Django cache (Redis): с TTL 5-60 минут.
   - Never-cache: пользовательские данные, финансовые (deals amounts), messages.

3. **Invalidation**:
   - Signal-based: on save/delete — clear cache.
   - Version-based: `cache_key = f"user_scope:{user.id}:v{user.cache_version}"`.

4. **Per-module**:
   - **Policy**: cache PolicyRule list per role, 5 min. Invalidate on PolicyRule save.
   - **User roles**: cache user.role / branch on request.user wrapper.
   - **FeatureFlags**: cache for 1 min per user.
   - **Analytics**: cache dashboard results for 5 min.

5. **Anti-patterns**:
   - Nev do cache on user-specific queries without key including user_id.
   - Never cache сalar returns — нужны TTL + invalidation.

6. **Monitoring**:
   - Cache hit ratio в Grafana.
   - Alert если hit ratio < 80% (возможно, cache не работает).

### Инструменты
- `mcp__context7__*` — Django cache docs

### Definition of Done
- [ ] Caching strategy документирована
- [ ] 5+ cache layers добавлены с invalidation
- [ ] Monitoring в Grafana
- [ ] Тесты: cache hit + invalidation сценарии

### Артефакты
- `backend/core/cache/*.py`
- `docs/architecture/caching.md`
- `tests/performance/test_cache.py`

### Валидация
```bash
pytest tests/performance/test_cache.py
# Grafana: cache hit ratio graph
```

### Откат
```bash
git revert
```

### Обновить в документации
- `docs/architecture/caching.md`

---

## Этап 13.4. Frontend bundle optimization

### Контекст
`backend/static/ui/` — много JS-файлов, дубликаты, минимальной оптимизации.

### Цель
Frontend bundle < 500KB total (gzip). Latency FCP < 2.5s.

### Что делать
1. **Audit**:
   - Размеры всех JS/CSS.
   - Duplicated libs (DOMPurify, date-fns).
   - Unused code.

2. **Build pipeline**:
   - esbuild / Vite для сборки.
   - Tree shaking.
   - Minification + gzip/brotli.
   - Code splitting per-route (если route-specific JS).

3. **Lazy loading**:
   - Heavy widgets (charts, rich editor) — loaded on-demand.
   - `<script defer>` / `type="module"`.

4. **Images**:
   - WebP где возможно.
   - `<picture>` + srcset for responsive.
   - Lazy loading `loading="lazy"`.

5. **Fonts**:
   - Subset только used characters (русский + latin).
   - `font-display: swap`.
   - Preload critical fonts.

6. **CSS**:
   - Tailwind purge working (производство — only used classes).
   - Critical CSS inline (head).
   - Remaining CSS — async.

7. **Third-party**:
   - Yandex Metrika, Sentry — async.
   - Consent-gated (не грузить до opt-in).

### Инструменты
- `mcp__playwright__*` — Lighthouse CI

### Definition of Done
- [ ] Total JS < 500KB gzipped on key pages
- [ ] Lighthouse Performance ≥ 90
- [ ] Critical CSS inline
- [ ] Lazy loading для не-critical

### Артефакты
- `vite.config.js` / `esbuild.config.js`
- `backend/templates/base.html` (updated)
- `docs/ui/bundle-optimization.md`

### Валидация
```bash
lhci autorun
# Check bundle size
```

### Откат
```bash
git revert
```

### Обновить в документации
- `docs/ui/bundle-optimization.md`

---

## Этап 13.5. Celery optimization

### Контекст
Celery worker'ы сейчас default config. При масштабировании — могут стать bottleneck.

### Цель
Celery tuned для нагрузки.

### Что делать
1. **Queue separation**:
   - `default` — fast tasks (< 10s)
   - `heavy` — long tasks (exports, imports, campaigns)
   - `priority_high` — realtime (notifications, FCM)
   - `low_priority` — background (retention cleanup, analytics snapshot)

2. **Workers separation**:
   - Separate worker processes per queue.
   - Concurrency tuned (CPU count × 2 for I/O-bound).
   - Prefetch multiplier = 1 for long tasks (default 4 не подходит).

3. **Task tuning**:
   - `acks_late=True` — task acked только after completion (restart safety).
   - `reject_on_worker_lost=True`.
   - `time_limit` / `soft_time_limit` per task type.
   - `retry_backoff=True` with jitter.

4. **Beat scheduler**:
   - DatabaseScheduler (уже скорее всего) — scheduled tasks в БД.
   - No race conditions if multiple beats.

5. **Monitoring**:
   - `celery-exporter` to Prometheus.
   - Dashboard: tasks rate, durations, failures, queue depth per queue.
   - Alert: queue depth > 1000 for > 5 min.

6. **Idempotency**:
   - All tasks — idempotent (safe to retry).
   - Use unique task_id where needed (e.g., campaign send — lock by campaign_id).

### Инструменты
- `mcp__context7__*` — Celery docs

### Definition of Done
- [ ] 4 queues разделены
- [ ] Workers configured per queue
- [ ] Task tuning applied
- [ ] Monitoring + alerts
- [ ] Idempotency проверена на критичных tasks

### Артефакты
- `backend/crm/celery.py` (updated)
- `deploy/docker-compose/celery-*.yml` (updated с multiple services)
- `docs/ops/celery-tuning.md`

### Валидация
```bash
celery -A crm inspect active_queues
# Grafana: queue metrics
```

### Откат
```bash
git revert
```

### Обновить в документации
- `docs/ops/celery-tuning.md`

---

## Checklist завершения волны 13

- [ ] N+1 в hot paths исправлен
- [ ] Медленные запросы оптимизированы индексами
- [ ] Redis caching применён где надо
- [ ] Frontend bundle < 500KB, Lighthouse ≥ 90
- [ ] Celery tuned с разделением очередей

**Готовимся к Wave 14 (финальный QA).**
