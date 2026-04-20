# Волна 8. Аналитика

**Цель волны:** Превратить CRM из «тут лежат данные» в «тут видно, что происходит». Дашборды для менеджмента, метрики каждого модуля, экспорты.

**Параллелизация:** высокая. Каждый этап — независимый dashboard.

**Длительность:** 10–12 рабочих дней.

**Требования:** Все предыдущие волны (W3–W7) — основные entity и события наполняют данные.

---

## Этап 8.1. Sales / Pipeline analytics dashboard

### Контекст
Сейчас есть `/analytics/` страница с базовой информацией. Нужен полноценный дашборд для SALES_HEAD.

### Цель
Дашборд, из которого SALES_HEAD понимает: воронка, conversion rates, выручка, прогноз.

### Что делать
1. **Виджеты**:
   - **Sales funnel** — визуал воронки по стадиям: N лидов → N deals → N qualified → N won. Conversion % между стадиями.
   - **Revenue** — сумма won deals за период (сегодня/неделя/месяц/квартал/год). Сравнение с предыдущим периодом.
   - **Forecast** — сумма pipeline × probability (из PipelineStage.probability, Wave 3.1). Прогноз закрытия в этом периоде.
   - **Win / loss rate** — % won / (won + lost). По причинам lost (из CompanyDeal.lost_reason).
   - **Average deal size** — среднее по won deals.
   - **Average deal cycle** — от создания до закрытия (days).
   - **Top customers** — top 10 компаний по total deal value.

2. **Фильтры** (глобальные для дашборда):
   - Период (preset + custom date range).
   - Branch (для BRANCH_DIRECTOR+).
   - Responsible user.
   - Pipeline (если multi-pipeline из Wave 3.1).

3. **Сравнения**:
   - Period-over-period (% change).
   - Per-manager comparison.

4. **Drill-down**:
   - Click на виджет → list view с применёнными фильтрами.

5. **Performance**:
   - Агрегаты в БД (materialized views или кэш Redis 5 минут).
   - Фоновая пересборка каждые 5 минут (Celery beat).

### Инструменты
- `mcp__postgres__*` — оптимизация запросов, materialized views
- `mcp__context7__*` — Chart.js / Recharts / ApexCharts

### Definition of Done
- [ ] Dashboard работает для всех фильтров
- [ ] 7 виджетов реализованы
- [ ] Drill-down работает
- [ ] Latency < 500ms (с кэшем)
- [ ] Permissions: MANAGER видит только свои данные, SALES_HEAD — филиал

### Артефакты
- `backend/analytics/services/sales_analytics.py`
- `backend/analytics/aggregators.py`
- `backend/ui/views/pages/analytics/sales.py`
- `backend/templates/analytics/sales.html`
- `backend/static/ui/analytics/sales.js`
- `backend/api/v1/views/analytics/sales.py`
- `tests/analytics/test_sales.py`
- `docs/features/analytics-sales.md`

### Валидация
```bash
pytest tests/analytics/test_sales.py
# Manual: проверить на staging с реальными данными
```

### Откат
```bash
git revert
```

### Обновить в документации
- `docs/features/analytics-sales.md`

---

## Этап 8.2. Manager KPI dashboard

### Контекст
Нужен персональный KPI-дашборд для менеджера: «как я работаю?». И обзорный для руководителя: «как работают мои подчинённые?».

### Цель
Personal dashboard для MANAGER и team dashboard для SALES_HEAD / GROUP_MANAGER.

### Что делать
1. **Personal KPI** (`/analytics/me/`):
   - Количество звонков за период.
   - Количество отправленных писем.
   - Количество новых клиентов (создано / передано).
   - Количество выполненных задач (on time / overdue).
   - Количество won deals + сумма.
   - Conversion rate.
   - Activity heatmap (по часам дня / дням недели).
   - Ranking по филиалу (анонимизированный: «вы в топ-3»).

2. **Team dashboard** (`/analytics/team/`):
   - Таблица с подчинёнными + их KPI.
   - Сортировка по любому столбцу.
   - Leaderboard с бейджами (top performer, most improved).
   - Export в XLSX.

3. **Goals**:
   - `SalesGoal` модель: user_id, period (month/quarter/year), target_amount, target_deals_count.
   - Прогресс-бар на dashboard: «выполнил 65% месячного плана».

4. **Alerts**:
   - Если manager не сделал ни одного действия > 2 дня → SALES_HEAD уведомление (opt-in).

### Definition of Done
- [ ] Personal KPI работает
- [ ] Team dashboard работает
- [ ] Goals с progress
- [ ] Alerts
- [ ] Permissions соблюдаются

### Артефакты
- Миграции для `SalesGoal`, `SalesKPISnapshot`
- `backend/analytics/services/manager_kpi.py`
- `backend/ui/views/pages/analytics/manager.py`, `team.py`
- `backend/templates/analytics/manager.html`, `team.html`
- `backend/celery/tasks/snapshot_kpi.py` (nightly)
- `tests/analytics/test_manager_kpi.py`
- `docs/features/analytics-manager.md`

### Валидация
```bash
pytest tests/analytics/test_manager_kpi.py
```

### Откат
```bash
git revert
```

### Обновить в документации
- `docs/features/analytics-manager.md`

---

## Этап 8.3. Campaign (email) analytics

### Контекст
В Wave 6 добавили tracking. Теперь — сводная аналитика рассылок.

### Цель
Dashboard «эффективность рассылок» для SALES_HEAD / ADMIN.

### Что делать
1. **Per-campaign detail page**:
   - Sent / delivered / opened / clicked / bounced / unsubscribed — числа + %.
   - Timeline: graph «когда открывали» (first 48h).
   - Top clicked links.
   - Top email clients (Gmail / Yandex / Outlook — по user_agent).
   - GeoIP карта (где открывали).

2. **Campaign list** с quick metrics:
   - Сортировка по open rate, click rate.
   - Фильтр по автору, периоду.

3. **A/B comparison**:
   - Если 2 campaigns с одинаковой audience (можно так настроить) — сравнение side-by-side.

4. **Sender reputation**:
   - Per-GlobalMailAccount — bounce %, complaint %, график за 30 дней.

5. **Cohort**:
   - Контакты, получившие X писем за месяц vs их deal conversion.

### Definition of Done
- [ ] Per-campaign detail работает
- [ ] Campaign list с metrics
- [ ] Sender reputation dashboard
- [ ] Cohort analysis (basic)

### Артефакты
- `backend/analytics/services/campaign_analytics.py`
- `backend/ui/views/pages/mailer/campaign_stats.py`
- `backend/templates/mailer/campaign_stats.html`
- `tests/analytics/test_campaign_analytics.py`
- `docs/features/analytics-campaigns.md`

### Валидация
```bash
pytest tests/analytics/
```

### Откат
```bash
git revert
```

### Обновить в документации
- `docs/features/analytics-campaigns.md`

---

## Этап 8.4. Live-chat analytics

### Контекст
Нужны метрики: FRT (first response time), AHT (average handling time), CSAT.

### Цель
Chat performance dashboard.

### Что делать
1. **Per-inbox metrics**:
   - Total conversations.
   - Active / resolved / unassigned.
   - Avg FRT (first response time): от создания диалога до первого ответа оператора.
   - Avg AHT (average handling time): от первого до последнего сообщения оператора.
   - Avg response time (между сообщениями).
   - CSAT (из Wave 5.4): средняя оценка + distribution.

2. **Per-operator metrics**:
   - Conversations handled.
   - Avg FRT / AHT.
   - Rating avg.
   - Online time.

3. **Timeline**:
   - Conversations per hour / day.
   - Peak hours heatmap.

4. **SLA tracking**:
   - Если Inbox имеет SLA (e.g. «ответить в 15 минут») — % соблюдения.
   - Breaches list.

5. **Tags analysis**:
   - Если операторы тегируют диалоги — top tags, trend.

### Definition of Done
- [ ] Inbox metrics работают
- [ ] Operator metrics работают
- [ ] SLA tracking
- [ ] Timeline graphs

### Артефакты
- `backend/analytics/services/chat_analytics.py`
- `backend/ui/views/pages/analytics/chat.py`
- `tests/analytics/test_chat_analytics.py`
- `docs/features/analytics-chat.md`

### Валидация
```bash
pytest tests/analytics/
```

### Откат
```bash
git revert
```

### Обновить в документации
- `docs/features/analytics-chat.md`

---

## Этап 8.5. Call analytics

### Контекст
CallRequest + Android events дают данные. Нужны метрики.

### Цель
Call performance dashboard.

### Что делать
1. **Per-period metrics**:
   - Total calls (incoming + outgoing).
   - Answered / missed / rejected / ended.
   - Avg duration.
   - Total talk time per manager.

2. **Per-contact / per-company**:
   - Call history с datetime, duration, direction, manager.
   - В карточке компании — раздел «Звонки».

3. **Activity heatmap**:
   - Peak hours calling.
   - Ratio incoming / outgoing.

4. **Correlation with deals**:
   - Deals, где было > N звонков vs где <.
   - Conversion rate по call activity.

### Definition of Done
- [ ] Dashboard работает
- [ ] Per-contact integration в карточку
- [ ] Correlation analysis

### Артефакты
- `backend/analytics/services/call_analytics.py`
- `backend/ui/views/pages/analytics/calls.py`
- `tests/analytics/test_call_analytics.py`
- `docs/features/analytics-calls.md`

### Валидация
```bash
pytest tests/analytics/
```

### Откат
```bash
git revert
```

### Обновить в документации
- `docs/features/analytics-calls.md`

---

## Этап 8.6. Export в Excel / PDF + scheduled reports

### Контекст
Менеджмент хочет «отчёт за месяц в Excel на почту».

### Цель
Export любого dashboard в XLSX/PDF + scheduled emails.

### Что делать
1. **Export endpoints**:
   - `/analytics/<dashboard>/export?format=xlsx` — asynchronous через Celery.
   - Формат: openpyxl с форматированием (заголовки, борды, цвета).
   - PDF: через WeasyPrint (HTML → PDF) из того же template.

2. **Scheduled reports**:
   - UserReportSchedule модель: user, dashboard_type, frequency (daily/weekly/monthly), format, recipients (emails).
   - Celery beat генерирует, отправляет email с attachment.

3. **Templates для отчётов**:
   - Красивое оформление с logo GroupProfi.
   - Header с периодом, branch, автором.
   - Footer с датой генерации.

4. **Permissions**:
   - Юзер может schedule report только для данных, к которым имеет доступ.
   - Recipients могут быть external emails (ADMIN confirmation для external).

### Definition of Done
- [ ] Export XLSX / PDF работает для всех dashboards
- [ ] Scheduled reports работают
- [ ] Template красиво рендерит

### Артефакты
- Миграции для `UserReportSchedule`
- `backend/analytics/services/export_service.py`
- `backend/analytics/services/pdf_generator.py`
- `backend/celery/tasks/scheduled_reports.py`
- `backend/ui/views/pages/analytics/reports.py`
- `tests/analytics/test_export.py`
- `docs/features/scheduled-reports.md`

### Валидация
```bash
pytest tests/analytics/test_export.py
# Manual: запланировать отчёт, получить email
```

### Откат
```bash
git revert
```

### Обновить в документации
- `docs/features/scheduled-reports.md`

---

## Checklist завершения волны 8

- [ ] Sales / Pipeline dashboard
- [ ] Manager KPI (personal + team) dashboard
- [ ] Campaign analytics
- [ ] Chat analytics (FRT / AHT / CSAT)
- [ ] Call analytics
- [ ] Export + scheduled reports

**Синк с продуктовой стороной** (SALES_HEAD всех трёх филиалов) — обязательно. Аналитика бесполезна, если менеджмент её не смотрит и не понимает.
