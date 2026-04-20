# Волна 2. Security, Auth, Policy ENFORCE

**Цель волны:** Закрыть все критичные security-пробелы. Перевести Policy Engine из OBSERVE_ONLY в ENFORCE. Добавить 2FA. Добить 152-ФЗ compliance. Аудит всех типов session/token auth.

**Параллелизация:** ограниченная. Policy ENFORCE — синхронизационный барьер, после него всё идёт с включёнными правами.

**Длительность:** 10–14 рабочих дней.

**Требования:** Wave 1 завершена. Feature flags работают. GlitchTip работает.

**Критичность:** MAX. Это самый рискованный рефакторинг — права реально начинают блокировать. Строго следуем плану с shadow-окном.

---

## Preconditions волны 2 (проверить ДО старта 2.1)

Эти пункты должны быть выполнены перед любой работой над Policy Engine. Если хоть один не выполнен — стоп, сначала закрыть.

- [ ] **W0.1 завершён** — есть `docs/audit/policy-gaps.txt` со списком mutating endpoints без `@policy_required`. Без этого списка шаг 2.1 работает в темноте.
- [ ] **W0.4 завершён** — GlitchTip работает, видно ошибки с user/role/branch тегами.
- [ ] **W10.4 завершён (минимум Prometheus + Grafana)** — есть возможность построить dashboard «denied requests per role per hour». Без dashboard SHADOW период слепой. Если W10.4 не готов — делаем минимальную версию: простой SQL-view поверх `PolicyDecisionLog` + admin-страница.
- [ ] **Kill-switch через env var**, не через `settings.py`:
  - Systemd env file: `/etc/proficrm/env.d/policy.conf` с `POLICY_ENGINE_ENFORCE=true|false`
  - При изменении: `systemctl reload proficrm-web proficrm-celery` — без полного redeploy.
  - В Django код читает через `os.environ.get('POLICY_ENGINE_ENFORCE', 'false')` **на каждом запросе** (не на старте процесса), чтобы reload срабатывал мгновенно.
- [ ] **Матрица ролей×ресурсов как JSON** — `backend/policy/fixtures/role_matrix.json` версионируется в git, из неё же генерятся тесты.
- [ ] **Shadow period минимум 2 недели** на проде с реальным трафиком. Никаких «быстро за день».

Только после всех галок — старт этапа 2.1.

---

## Этап 2.1. Аудит Policy rules × ресурсы

### Контекст
Сейчас Policy Engine работает в `OBSERVE_ONLY`: декоратор срабатывает, правила проверяются, но отказ не возвращается — только логируется. Переход в `ENFORCE` без полной матрицы правил = обязательные инциденты блокировки легитимных юзеров.

### Цель
Составить полную матрицу «роль × ресурс × действие = разрешение». Ревью каждой строки с продакт-стороной (или хотя бы с тобой как ответственным).

### Что делать
1. **Генерация матрицы** через Agent:
   - Для каждой роли (MANAGER, TENDERIST, SALES_HEAD, BRANCH_DIRECTOR, GROUP_MANAGER, ADMIN) × каждый ресурс (Company, Contact, Deal, Task, Note, Campaign, Conversation, Message, MailAccount, CallRequest, PhoneDevice, User, PolicyRule) × каждое действие (list, retrieve, create, update, destroy, export, bulk_action, transfer, approve, reject).
   - Получается ~6 × 13 × 10 = **780 строк**. Реально часть neприменима (нельзя delete User без ADMIN).
   - Записать в `docs/security/policy-matrix.md` в виде Markdown-таблицы.

2. **Ревью матрицы**:
   - Обсудить с тобой/руководителями филиалов кейсы, где роль непонятна.
   - Особенно: visibility — «свои / отдела / все». Иерархия GROUP_MANAGER над подчинёнными.
   - Особо тонкие места: кто может пересылать клиента (transfer), кто — удалять (delete vs request_deletion), кто — рассылать письма.
   - Зафиксировать `PolicyConfig` overrides в БД для краевых случаев.

3. **Полнота проверки**:
   - Прогнать по inventory views (Wave 0) и API endpoint'ам — где нет `@policy_required`, решить: нужно или нет.
   - Если не нужно (публичный endpoint) — зафиксировать явным маркером `@policy_exempt` с комментарием.

4. **Тесты**: для каждой пары (роль, действие) написать integration-тест, который проверяет корректный 200/403. Матрица тестов = матрица правил. Использовать `pytest.parametrize` для генерации из YAML-матрицы.

### Инструменты
- `Agent` для генерации матрицы параллельно
- `mcp__postgres__*` — PolicyRule записи
- `Read`, `Grep`

### Definition of Done
- [ ] Полная матрица в `docs/security/policy-matrix.md`
- [ ] Все `@policy_required` имеют соответствующую PolicyRule в БД
- [ ] Все API-endpoint'ы либо `@policy_required` либо `@policy_exempt`
- [ ] Integration-тесты: минимум 200 автогенерированных тестов на матрицу (роль × действие)
- [ ] `policy-matrix.md` на 100% покрыта правилами в БД

### Артефакты
- `docs/security/policy-matrix.md`
- `docs/security/policy-rules.yaml` (исходник для тестов и для миграций)
- `backend/policy/fixtures/initial_rules.json` (seed для нового окружения)
- `tests/security/test_policy_matrix.py`
- Миграция, создающая базовый набор PolicyRule

### Валидация
```bash
pytest tests/security/test_policy_matrix.py -v
python manage.py check_policy_coverage  # кастомная команда: проверка что все views покрыты
```

### Откат
Tests — безопасны. Матрица в `PolicyConfig` — откатываемая миграция.

### Обновить в документации
- `docs/security/policy-matrix.md` (новый)
- `docs/security/README.md` — oбзор
- `docs/decisions.md`: ADR-010 «Policy matrix as source of truth»

---

## Этап 2.2. Shadow mode: ENFORCE с логированием, но без блокировки

### Контекст
Нельзя сразу включать ENFORCE — риск блокировки легитимных юзеров. Нужен промежуточный режим: правила применяются «как если бы» ENFORCE, но отказ не возвращается, только логируется как `would_deny`.

### Цель
Переключить Policy Engine в `SHADOW_ENFORCE` режим на 2 недели. Собрать логи `would_deny`, разобрать каждый, либо дополнить правила, либо изменить код.

### Что делать
1. Добавить в Policy Engine третий режим: `MODE = OBSERVE | SHADOW_ENFORCE | ENFORCE`.
   - `OBSERVE`: правило проверяется, всё разрешается.
   - `SHADOW_ENFORCE`: правило проверяется, результат логируется в `PolicyDecisionLog` (новая таблица), но HTTP 403 не возвращается.
   - `ENFORCE`: полноценная блокировка.

2. Таблица `PolicyDecisionLog`:
   - `id, timestamp, user_id, role, resource, action, resource_id, decision (allow/deny), reason, request_id, request_path, request_method`
   - Retention: 30 дней (настроить в Celery beat).

3. **Dashboard** для `would_deny`:
   - В Admin / в отдельной странице для SALES_HEAD+: «отказы в последние 24ч».
   - Группировка по (user, resource, action), сортировка по count.

4. **Feature flag** `policy_engine_mode` с значениями.

5. **Alerts**: если `would_deny` count > 100 за час — Sentry warning.

6. **2 недели мониторинга**:
   - Раз в день ревью топ-отказов.
   - Для каждого — решение: (a) расширить правило, (b) изменить код, чтобы не нарушал, (c) это действительно unauthorized — оставляем.

### Инструменты
- `mcp__postgres__*` — для таблицы логов
- `mcp__playwright__*` — smoke тесты чтобы убедиться, что всё работает как в OBSERVE

### Definition of Done
- [ ] Policy Engine поддерживает 3 режима
- [ ] `PolicyDecisionLog` таблица создана и работает
- [ ] Dashboard показывает would_deny
- [ ] Alert при аномальном всплеске настроен
- [ ] 2 недели мониторинга прошли
- [ ] Все критичные would_deny разобраны (либо fix правил, либо fix кода)
- [ ] `would_deny` rate < 5/час за последние 3 дня

### Артефакты
- Миграция для `PolicyDecisionLog`
- `backend/policy/engine.py` (обновлённый с 3 режимами)
- `backend/policy/admin.py` (dashboard)
- `backend/ui/views/pages/admin/policy_decisions.py`
- `docs/runbooks/policy-shadow-rollout.md`

### Валидация
```bash
# Включить SHADOW_ENFORCE
python manage.py set_policy_mode shadow_enforce
# Через неделю смотрим
python manage.py shell -c "from policy.models import PolicyDecisionLog; print(PolicyDecisionLog.objects.filter(decision='deny').count())"
```

### Откат
```bash
python manage.py set_policy_mode observe
```

### Обновить в документации
- `docs/runbooks/policy-shadow-rollout.md`
- `docs/decisions.md`: ADR-011

---

## Этап 2.3. Переход в ENFORCE

### Контекст
После 2-недельного shadow-окна и зачистки всех would_deny — можно переключать.

### Цель
Policy Engine в `ENFORCE`. Любой unauthorized — HTTP 403 с корректной ошибкой.

### Что делать
1. Прогнать финальный проход по `PolicyDecisionLog` — нет неразобранных deny.
2. Создать feature flag `policy_engine_enforce_percentage` — процент юзеров, у которых ENFORCE активен. Процент берётся через `user.pk % 100 < threshold` — детерминированно, один и тот же юзер всегда в одной группе.
3. Rollout в 5 этапов: 10% → 25% → 50% → 75% → 100%. Каждый этап — 2 дня наблюдений.
4. На каждом этапе — мониторинг GlitchTip, `PolicyDecisionLog`, тикеты от юзеров, **Grafana dashboard «denied requests per role per hour»**.
5. **Kill-switch через env var** (НЕ через settings.py):
   - `/etc/proficrm/env.d/policy.conf`:
     ```bash
     POLICY_ENGINE_ENFORCE=true   # или false для отката
     ```
   - В коде Django:
     ```python
     # backend/policy/engine.py
     import os
     def is_enforce_enabled() -> bool:
         return os.environ.get('POLICY_ENGINE_ENFORCE', 'false').lower() == 'true'
     ```
   - Systemd unit `proficrm-web.service` имеет `EnvironmentFile=/etc/proficrm/env.d/policy.conf`.
   - Exec:
     ```bash
     # Включить ENFORCE
     sudo sed -i 's/ENFORCE=.*/ENFORCE=true/' /etc/proficrm/env.d/policy.conf
     sudo systemctl reload proficrm-web proficrm-celery proficrm-daphne
     # Выключить (EMERGENCY)
     sudo sed -i 's/ENFORCE=.*/ENFORCE=false/' /etc/proficrm/env.d/policy.conf
     sudo systemctl reload proficrm-web proficrm-celery proficrm-daphne
     ```
   - **Критично:** функция `is_enforce_enabled()` вызывается на каждом запросе, не кэшируется. Иначе reload бесполезен.
   - Runbook `docs/runbooks/policy-kill-switch.md` — одна команда, время выполнения < 10 сек от решения до эффекта.

### Инструменты
- `mcp__postgres__*`
- `mcp__playwright__*` — прогон всех ролей, проверка 403 в ожидаемых местах

### Definition of Done
- [ ] ENFORCE активен для 100% юзеров
- [ ] За 3 дня после 100% — 0 инцидентов блокировки легитимных операций
- [ ] Kill-switch протестирован (включить → всё работает; выключить — вернулось в OBSERVE)
- [ ] UI отображает 403 errors корректно (кастомная страница с объяснением «связаться с админом»)

### Артефакты
- `backend/policy/engine.py` (финальная версия)
- `backend/templates/errors/403.html` (с указанием, к кому обратиться)
- `docs/runbooks/policy-enforce-rollout.md`
- `docs/runbooks/policy-kill-switch.md`

### Валидация
```bash
pytest tests/security/test_policy_matrix.py  # все зелёные в ENFORCE
curl -X GET http://staging.url/admin/ -H "Cookie: ..."  # MANAGER → 403
```

### Откат
```bash
export POLICY_ENGINE_KILL_SWITCH=1
# перезапустить web/celery
```

### Обновить в документации
- `docs/runbooks/policy-enforce-rollout.md` (how-to для будущих подобных миграций)

---

## Этап 2.4. 2FA TOTP (django-otp)

### Контекст
Сейчас auth = Django session + MagicLink. Для ADMIN и BRANCH_DIRECTOR — недостаточно. Нужен TOTP.

### Цель
TOTP 2FA обязателен для ADMIN и BRANCH_DIRECTOR, опционален для остальных. Recovery codes при включении. Email OTP как fallback.

### Что делать
1. **Установить** `django-otp`, `qrcode[pil]`.

2. **Модели**:
   - `TOTPDevice` (из django-otp) — для TOTP.
   - `RecoveryCode` — 10 одноразовых кодов при включении.
   - `EmailOTPBackup` — если юзер потерял доступ к TOTP-приложению.

3. **UI**:
   - Страница `/profile/security/` — включить/выключить 2FA, показать QR-код, recovery codes.
   - Login flow: после password → если 2FA включен → страница ввода TOTP.
   - «Remember this device 30 days» — опционально, cookie `2fa_trust_token`.

4. **Enforcement для админов — мягкая миграция 2 недели**:
   - **Неделя 1-2:** Баннер на главной для ADMIN и BRANCH_DIRECTOR: «Настройте 2FA — с [дата] это будет обязательным». Middleware пропускает без 2FA, но показывает баннер + отправляет email-напоминание каждые 3 дня.
   - **С дня 15 (mandatory):** Middleware — если `user.role in [ADMIN, BRANCH_DIRECTOR]` и 2FA не включен → redirect на `/profile/security/setup-2fa/`. Юзер **не может** попасть в другие страницы пока не настроит TOTP.
   - Feature flag `TWO_FACTOR_MANDATORY_FOR_ADMINS` — переключает soft ↔ mandatory без редеплоя.
   - CLI для отчёта кто ещё не включил: `python manage.py audit_2fa --role=ADMIN,BRANCH_DIRECTOR --not-enabled`.
   - Все юзеры из списка получают персональный email за 3 дня до перехода в mandatory.
   - **Recovery codes обязательно показываются при enable** — юзер не сможет закрыть окно пока не подтвердит «я сохранил коды».

5. **Management commands**:
   - `reset_2fa <user_id>` — для восстановления доступа.
   - **Критично:** команда доступна ТОЛЬКО для `is_superuser=True` ИЛИ через прямой SSH на сервер. Не через web.
   - Каждый вызов `reset_2fa` пишется в `SecurityAuditLog` с IP, timestamp, кто сбрасывал.
   - `audit_2fa` — отчёт, у кого включено.

6. **Recovery flow**: если TOTP недоступен:
   - Вариант A (пользователь сам): ввести один из 10 recovery codes → сбросить TOTP → настроить заново.
   - Вариант B (код потерян): запрос email OTP → ввести OTP из письма → сбросить TOTP → настроить заново.
   - Вариант C (email тоже недоступен — редко): обратиться к ADMIN, он делает `reset_2fa <user>` через SSH.
   - Всё логируется в `SecurityAuditLog`.

7. **Rate limiting**: 5 попыток TOTP за 10 минут → IP-бан на 30 минут.

### Инструменты
- `mcp__context7__*` — django-otp docs

### Definition of Done
- [ ] TOTP работает для любого юзера, кто включил
- [ ] Soft-фаза (2 недели): баннер + email-напоминания для ADMIN и BRANCH_DIRECTOR
- [ ] Mandatory-фаза (с дня 15): feature flag `TWO_FACTOR_MANDATORY_FOR_ADMINS=true`, блок-redirect работает
- [ ] Recovery codes генерируются, показываются с подтверждением «я сохранил»
- [ ] Email OTP fallback работает
- [ ] `reset_2fa` доступен только is_superuser + через SSH; пишет в `SecurityAuditLog`
- [ ] Rate limiting на TOTP попытки
- [ ] E2E тест: включить 2FA, выйти, войти, ввести TOTP, попасть в CRM
- [ ] E2E тест: recovery code flow
- [ ] E2E тест: email OTP fallback
- [ ] `audit_2fa --not-enabled` на день 14 возвращает 0 для ADMIN/BRANCH_DIRECTOR

### Артефакты
- Миграции для `TOTPDevice`, `RecoveryCode`, `EmailOTPBackup`, `SecurityAuditLog`
- `backend/accounts/services/two_factor_service.py`
- `backend/ui/views/pages/profile/security.py`
- `backend/templates/profile/security/*.html`
- `backend/accounts/middleware/two_factor_enforce.py`
- `backend/management/commands/reset_2fa.py`
- `backend/management/commands/audit_2fa.py`
- `tests/e2e/test_2fa_flow.py`
- `tests/e2e/test_2fa_recovery.py`
- `docs/runbooks/2fa-setup.md`
- `docs/runbooks/2fa-recovery.md`
- `docs/runbooks/2fa-migration-timeline.md` — хронология soft→mandatory

### Валидация
```bash
pytest tests/accounts/test_two_factor.py
playwright test tests/e2e/test_2fa_flow.py
```

### Откат
```bash
python manage.py disable_2fa --all  # кастомная команда на экстренный случай
```

### Обновить в документации
- `docs/runbooks/2fa-setup.md`
- `docs/decisions.md`: ADR-012 «TOTP 2FA для высоких ролей»

---

## Этап 2.5. CSRF/XSS/SSRF аудит и исправление

### Контекст
Проект с Django — базовые защиты есть. Но если есть raw HTML в pages, обращения к URL по пользовательскому вводу (аватарки, загрузки), SSRF возможен.

### Цель
Пройти по OWASP Top 10 и закрыть все дырки.

### Что делать
1. **Bandit scan**: `bandit -r backend/ -lll`. Разобрать каждую находку.

2. **CSP**: настроить `Content-Security-Policy` через `django-csp`. Строгая политика: `default-src 'self'`, `script-src 'self' 'nonce-...'`, `img-src 'self' data: https://storage.yandexcloud.net`, и т.д. Для inline-скриптов — nonce.

3. **XSS**:
   - Все `|safe` в шаблонах — ревью. Нужно только для sanitized HTML.
   - Все `innerHTML` в JS — ревью. Использовать DOMPurify (у тебя уже есть).
   - User-input в rich-text (notes, campaigns) — санитизация через bleach на backend.

4. **CSRF**:
   - DRF: SessionAuthentication требует CSRF для unsafe methods.
   - JWT-endpoint'ы — без CSRF, но с `SameSite=Lax` cookie если используется session.
   - WebSocket: origin-check обязателен.

5. **SSRF**:
   - Все места, где URL приходит от пользователя (webhook_url в campaigns, avatar_url в contacts) — whitelist схем (https only), blacklist internal IPs (10.*, 172.16.*, 192.168.*, 127.*, 169.254.*, metadata.google.internal), таймауты, ограничение редиректов.
   - Используй `httpx` с `transport=httpx.HTTPTransport(verify_ssl=True)`.

6. **Open redirect**: все `?next=` и подобные — только relative или whitelist доменов.

7. **Mass assignment**: все DRF-serializer'ы проверить `fields = [...]` (не `__all__`). Особенно для update endpoint'ов.

8. **Secrets in logs**: grep by `password|token|secret|api_key` в logging statements. Заменить на `[REDACTED]`.

### Инструменты
- `Bash`: bandit, semgrep
- `mcp__context7__*`

### Definition of Done
- [ ] Bandit `-lll` — 0 high/critical
- [ ] django-csp подключён, CSP заголовки присутствуют во всех ответах
- [ ] CSP не ломает ни один экран (проверка Playwright с Chromium DevTools)
- [ ] Все SSRF-прона места защищены
- [ ] Нет `fields = "__all__"` в serializer'ах (кроме явно выбранных read-only)
- [ ] В логах нет секретов (grep-проверка)

### Артефакты
- `backend/core/security/ssrf_protection.py`
- `backend/core/security/csp_policy.py`
- `backend/core/security/url_validators.py`
- `tests/security/test_ssrf.py`
- `tests/security/test_csp.py`
- `tests/security/test_xss.py`
- `docs/security/owasp-checklist.md`

### Валидация
```bash
bandit -r backend/ -lll
pytest tests/security/
# Browser DevTools: CSP violations console должен быть пустой
```

### Откат
Revert отдельных изменений. CSP в report-only режиме — можно откатить без блокировки.

### Обновить в документации
- `docs/security/owasp-checklist.md`
- `docs/decisions.md`: ADR-013 «Strict CSP policy»

---

## Этап 2.6. Rate limiting и abuse prevention

### Контекст
Сейчас нет централизованного rate limiting. Риски: brute-force auth, spam рассылок, DDoS чат-виджета.

### Цель
Ввести multi-layer rate limiting: nginx → middleware → per-user per-endpoint.

### Что делать
1. **Nginx rate limiting** (глобальный): `limit_req_zone` на IP, 100 req/min для `/` и 10 req/min для `/auth/`. В `deploy/nginx/proficrm.conf`.

2. **Django-level** (`django-ratelimit` или `django-defender`):
   - Auth endpoints: 5 попыток per IP per 15 min.
   - Magic link request: 3 per email per hour.
   - API endpoints: 60 req/min per user (для MANAGER), 300 для ADMIN.
   - Widget chat создание диалога: 10 per IP per 5 min.
   - Campaign создание рассылки: 5 per user per day (защита от опечаток + sanity).

3. **DRF throttling**: `AnonRateThrottle`, `UserRateThrottle`, `ScopedRateThrottle` для специфичных.

4. **Defense in depth**: при срабатывании любого лимита — записать в `RateLimitEvent`, при threshold 100 events/ip/24h — автоматический IP ban (через iptables/fail2ban или Django middleware).

5. **Monitoring**: Grafana dashboard «Abuse & Rate limits» — графики по endpoint'ам.

### Инструменты
- `mcp__context7__*`

### Definition of Done
- [ ] Nginx rate limiting активен
- [ ] django-ratelimit + DRF throttling настроены для auth, widget, campaigns, API
- [ ] Rate limit events логируются
- [ ] Автоматический IP-ban при abuse threshold работает
- [ ] Тесты: brute-force попытка получает 429 после 5 попыток

### Артефакты
- `deploy/nginx/proficrm.conf` (обновлённый)
- `backend/core/middleware/rate_limit.py`
- `backend/core/throttles.py`
- Миграции для `RateLimitEvent`
- `tests/security/test_rate_limits.py`
- `docs/security/rate-limits.md`

### Валидация
```bash
# Brute-force test
for i in {1..10}; do curl -X POST http://staging/auth/login/ -d "email=x@y&password=wrong"; done
# Последние 5 — 429
```

### Откат
Revert. Nginx — `nginx -s reload` после правки.

### Обновить в документации
- `docs/security/rate-limits.md`

---

## Этап 2.7. 152-ФЗ compliance: opt-in, retention, data export

### Контекст
Вы обрабатываете ПДн (ФИО, email, телефон клиентов). 152-ФЗ требует: явное согласие на обработку (opt-in), возможность отозвать, предоставить данные по запросу, удалить по запросу.

### Цель
Привести CRM в соответствие 152-ФЗ в части функциональности (регистрация у РКН — твоя юридическая сторона).

### Что делать
1. **Opt-in в widget**: в форме чата — чек-бокс «Согласен на обработку персональных данных» (не прегалочен). Сохранять timestamp + IP + user-agent в `DataProcessingConsent` модели, связанной с Contact.

2. **Opt-in в рассылках**: любая рассылка идёт только тем, у кого `DataProcessingConsent` + `MarketingOptIn`. Лиды, пришедшие через виджет до внедрения — принудительная валидация до ручного подтверждения либо отсечение.

3. **Unsubscribe**:
   - В каждом рассылочном письме — ссылка unsubscribe (уже есть — проверить работоспособность).
   - Unsubscribe-страница: фиксирует отзыв согласия, сохраняет в `UnsubscribeEvent`.
   - Сегментации рассылки — исключают unsubscribed и suppressed emails.

4. **Data export**: по запросу (endpoint или management command) — экспорт всех данных по контакту/компании в JSON + CSV. ADMIN-only.

5. **Data deletion**: по запросу — полное удаление (а не soft). Через существующий `CompanyDeletionRequest` + новый `ContactDeletionRequest` с approval flow.

6. **Retention policy**:
   - Неактивные лиды (нет активности > 3 года) — автоматическое удаление (Celery beat).
   - Логи (ActivityEvent 180d, ErrorLog 90d — уже есть).
   - Chat messages > 2 года — архивирование в S3 с шифрованием, удаление из основной БД.

7. **Политика конфиденциальности**: страница `/privacy/` со ссылкой из виджета, footer. Текст — из `docs/legal/privacy-policy.md`, рендерится через Markdown.

8. **Audit log**: любой просмотр списков контактов / экспорт — логируется в `DataAccessLog`. Raсстояние для compliance аудита.

### Инструменты
- `mcp__context7__*`
- Помощь юриста — скорее вне Claude Code

### Definition of Done
- [ ] Widget форма требует явный opt-in чек-бокс
- [ ] `DataProcessingConsent` записывается с IP/UA/timestamp
- [ ] Unsubscribe работает и сегменты рассылок его учитывают
- [ ] Data export endpoint работает (ADMIN + ручная подтверждающая команда)
- [ ] Data deletion (hard delete) работает через approval
- [ ] Retention policies запускаются по Celery beat
- [ ] Страница /privacy/ опубликована
- [ ] `DataAccessLog` фиксирует просмотры/экспорты

### Артефакты
- Миграции для `DataProcessingConsent`, `UnsubscribeEvent`, `DataAccessLog`, `ContactDeletionRequest`
- `backend/compliance/services/consent_service.py`
- `backend/compliance/services/data_export_service.py`
- `backend/compliance/management/commands/retention_cleanup.py`
- `backend/ui/views/pages/privacy.py`
- `backend/templates/pages/privacy.html`
- `docs/legal/privacy-policy.md`
- `docs/compliance/152-fz-checklist.md`
- `docs/runbooks/data-export-request.md`
- `docs/runbooks/data-deletion-request.md`

### Валидация
```bash
pytest tests/compliance/
# Manual: widget opt-in flow работает, unsubscribe работает
```

### Откат
Revert.

### Обновить в документации
- `docs/compliance/152-fz-checklist.md`
- `docs/decisions.md`: ADR-014

---

## Checklist завершения волны 2

- [ ] Policy Engine в ENFORCE, 100% юзеров
- [ ] 2FA TOTP обязателен для ADMIN и BRANCH_DIRECTOR
- [ ] CSP / XSS / SSRF проверки пройдены
- [ ] Rate limiting на всех уровнях
- [ ] 152-ФЗ compliance: opt-in, unsubscribe, data export/delete
- [ ] Audit log покрывает ≥ 95% mutating операций
- [ ] Bandit + pip-audit + gitleaks — зелёные

**Только после этого** — переход к Wave 3 (Core CRM).
