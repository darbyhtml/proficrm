# Отчёт: Alerting Plan и Исправления

Краткий отчёт о внесённых исправлениях и созданном плане алёртов.

## 1. Что исправлено

### Fix A: Исправлен SQL запрос в playbook ✅

**Проблема:** В `MAILER_ONCALL_PLAYBOOK.md` в секции `daily_limit` использовался ORM-подобный синтаксис `campaign__created_by_id`, который не работает в чистом SQL.

**Исправление:**
- Заменён на корректный SQL с JOIN:
  ```sql
  SELECT COUNT(*) as sent_today
  FROM mailer_sendlog sl
  JOIN mailer_campaign c ON c.id = sl.campaign_id
  WHERE sl.provider = 'smtp_global'
    AND sl.status = 'sent'
    AND c.created_by_id = <user_id>
    AND sl.created_at >= CURRENT_DATE;
  ```

**Файл:** `MAILER_ONCALL_PLAYBOOK.md`

**Проверка:** Выполнена проверка на наличие других ORM-подобных конструкций (`campaign__`, `__created_by`) — найдено только одно место, исправлено.

---

### Fix B: Явное напоминание о HASH_SALT в production ✅

**Проблема:** Дефолтный `MAILER_LOG_HASH_SALT` может быть забыт при деплое в production, что создаёт риск безопасности.

**Исправления:**

1. **В `backend/crm/settings.py`:**
   - Добавлен комментарий с предупреждением о необходимости смены salt в production
   - Добавлен пример генерации безопасного salt: `openssl rand -hex 32`

2. **В `MAILER_ENTERPRISE_FINAL_REPORT.md`:**
   - Добавлен раздел "⚠️ ОБЯЗАТЕЛЬНЫЙ CHECKLIST ДЛЯ PRODUCTION DEPLOYMENT"
   - Включены пункты:
     - Установить уникальный `MAILER_LOG_HASH_SALT`
     - Убедиться, что `MAILER_LOG_FULL_EMAILS=False`
     - Проверить `MAILER_LOG_PII_LEVEL`

**Файлы:**
- `backend/crm/settings.py`
- `MAILER_ENTERPRISE_FINAL_REPORT.md`

---

## 2. Новый документ

### MAILER_ALERTING_PLAN.md ✅

Создан операционный план настройки алёртов на основе `MAILER_OBSERVABILITY_PACK.md`.

**Содержание:**
- 3 критических алёрта (ALERT-001, ALERT-002, ALERT-003)
- 4 предупреждающих алёрта (ALERT-004, ALERT-005, ALERT-006, ALERT-007)
- Для каждого алёрта:
  - Имя, severity, trigger condition, порог, окно
  - Источник данных (логи/SQL/Redis)
  - Action / ссылка на runbook в playbook
- Раздел "Как внедрять" с примерами для:
  - Grafana Loki (LogQL)
  - ELK Stack (KQL)
  - Sentry (если используется)
  - Минимум (cron SQL checks)
- Приоритеты внедрения (3 фазы)

**Файл:** `MAILER_ALERTING_PLAN.md`

---

## 3. Как проверить

### Проверка Fix A:
1. Открыть `MAILER_ONCALL_PLAYBOOK.md`
2. Найти раздел "daily_limit"
3. Убедиться, что SQL запрос использует JOIN, а не `campaign__created_by_id`
4. Выполнить SQL запрос в PostgreSQL — должен выполниться без ошибок

### Проверка Fix B:
1. Открыть `backend/crm/settings.py`
2. Найти `MAILER_LOG_HASH_SALT` — должен быть комментарий с предупреждением
3. Открыть `MAILER_ENTERPRISE_FINAL_REPORT.md`
4. Найти раздел "ОБЯЗАТЕЛЬНЫЙ CHECKLIST ДЛЯ PRODUCTION DEPLOYMENT" — должен содержать пункт о HASH_SALT

### Проверка Alerting Plan:
1. Открыть `MAILER_ALERTING_PLAN.md`
2. Убедиться, что все алёрты из `MAILER_OBSERVABILITY_PACK.md` включены
3. Проверить, что для каждого алёрта есть источник данных и action

---

## 4. Краткое резюме

Исправлены два замечания: SQL запрос в playbook (заменён ORM-синтаксис на корректный JOIN) и добавлено явное напоминание о необходимости смены HASH_SALT в production (комментарии в settings и checklist в отчёте). Создан операционный план алёртов (`MAILER_ALERTING_PLAN.md`) с 7 алёртами (3 критических, 4 предупреждающих), примерами внедрения для разных систем мониторинга (Loki, ELK, Sentry, cron) и приоритетами внедрения. Все изменения минимальны и не требуют рефакторинга архитектуры. Система готова к настройке алёртов в production.
