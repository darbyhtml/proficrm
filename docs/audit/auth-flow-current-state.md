# Auth Flow Current State — 2026-04-22

Read-only diagnostic для планирования W2.6 (удаление password-пути для non-admin). Zero code changes.

---

## TL;DR

1. **Magic link работает, primary path** — `MagicLinkToken` model, TTL 24ч, single-use, SHA-256 хеш, URL `/auth/magic/<token>/`. Admin генерирует через UI `/settings/admin/users/<id>/magic-link/generate/`.
2. **Password path частично ограничен** — `SecureLoginView` (`/login/`) уже отклоняет non-admin password login с ошибкой. Но есть **параллельный JWT endpoint `/api/token/` без role-restriction** — это bypass, который надо закрыть в W2.6.
3. **Session 14 дней** (`SESSION_COOKIE_AGE=1209600`) + rolling window (`SESSION_SAVE_EVERY_REQUEST=True`) → активные user'ы не выходят. Match c user's approximation "1-2 недели".
4. **Impersonation есть** — `view_as_enabled` session flag + `view_as_user_id/role/branch_id`. Helper `get_view_as_user(request)` в `accounts/permissions.py`. Toggle через POST на `/settings/admin/users/` с `toggle_view_as=on`.
5. **17 non-admin users с usable password** на staging. Пароли не reachable через основную login-форму (view отклоняет), но JWT endpoint может их принять.

---

## Magic link (expected primary)

### Model

- **Class**: `accounts.models.MagicLinkToken` ([backend/accounts/models.py:118](../../backend/accounts/models.py#L118))
- **Table**: `accounts_magiclinktoken`
- **Fields**:
  - `user` FK → `User` (CASCADE)
  - `token_hash` CharField(64, unique, indexed) — SHA-256 plain token
  - `created_at` DateTime (auto, indexed)
  - `expires_at` DateTime (indexed)
  - `used_at` DateTime (nullable, indexed)
  - `created_by` FK → `User` (SET_NULL, related_name="created_magic_links")
  - `ip_address` GenericIPAddressField (nullable, set on use)
  - `user_agent` CharField(255)
- **Indexes**: `(user, expires_at, used_at)` composite

### Token generation

- **Method**: `MagicLinkToken.generate_token()` (models.py:177)
  - `secrets.token_urlsafe(48)` → 64 chars base64url (~288 bits entropy)
  - Hash: SHA-256
- **Creator**: `MagicLinkToken.create_for_user(user, created_by, ttl_minutes=1440)` (models.py:188)
  - Default TTL: **24 часа** (1440 минут).

### Expiry

- **24 часа** по умолчанию. Задаётся через `ttl_minutes` arg в `create_for_user`.
- Enforced в `is_valid()`: `used_at is None AND timezone.now() < expires_at`.

### Single-use

- **Да**, enforced в `is_valid()` + `mark_as_used()` обновляет `used_at` atomically (update_fields).
- После успешного входа в `SecureLoginView.post` (views.py:128) и `magic_link_login` view вызывается `magic_link.mark_as_used(ip, ua)`.

### Generation endpoint

- **URL**: `/settings/admin/users/<int:user_id>/magic-link/generate/` ([backend/ui/urls.py:339-343](../../backend/ui/urls.py#L339))
- **View**: `ui.views.settings_core.settings_user_magic_link_generate` ([line 956](../../backend/ui/views/settings_core.py#L956))
- **Permission**: Inline `require_admin(request.user)` (line 961) — returns 302 redirect + flash error для non-admin.
- **Rate limit**: 1 generation per 10 seconds per target user_id (cache key `magic_link_generate_rate:<user_id>`, line 972-978).
- **Audit**: `log_event(actor=admin, verb=CREATE, entity_type="magic_link", ...)` (line 1002-1019).
- **Auto-generation** (bonus): `UserCreateForm.save()` в `ui/forms.py:1072` — при создании non-admin пользователя автоматически генерирует magic link.
- **Migration path**: `accounts/management/commands/migrate_users_to_access_keys.py` — CLI команда массового перевода users на magic link.

### Login endpoint

Two entry points:

1. **`/auth/magic/<token>/`** (GET) — direct link clicked from email/message/chat.
   - View: `accounts.views.magic_link_login` (views.py:234)
   - Rate limit: 5 tries/min per IP
   - Path: hash → lookup → validate → `login(request, user)` → `mark_as_used` → redirect to `LOGIN_REDIRECT_URL`.

2. **`/login/` + POST `login_type=access_key` с `access_key=<token>`** — через login form.
   - View: `accounts.views.SecureLoginView` (views.py:37)
   - Rate limit: 5 tries/min per IP
   - Same hash+validate+login+mark_as_used flow.

### Security

- `@policy_required` **НЕ использован** — inline check `require_admin()` + rate limit + audit log.
- `require_admin()` = `is_superuser OR role == ADMIN` (permissions.py:13-24).
- Rate limit ok, audit log ok, но surface не codified для W2.1.x policy migration.

---

## Password login (partially restricted)

### SecureLoginView `/login/`

- **Location**: `backend/accounts/views.py:37-231`
- **Flow** для `login_type=password`:
  1. Rate limit 5/min per IP (line 160).
  2. Account lockout check (line 168).
  3. `authenticate(username, password)` (line 178) — стандартный Django `ModelBackend`.
  4. **Role check**: `if user.role != User.Role.ADMIN: return error` (line 187-193) — message: "Вход по логину и паролю доступен только для администраторов".
  5. `is_active` check (line 196).
  6. `login(request, user)` + audit `password_login_success:{user.id}`.

- **Verdict**: non-admin password **уже заблокирован на view уровне**, но `authenticate()` выполняется до role check — credential validity проверяется на каждом non-admin попытке.

### JWT `/api/token/` (UNRESTRICTED — w2.6 target)

- **Location**: `backend/accounts/jwt_views.py:30-97`
- **View**: `SecureTokenObtainPairView(TokenObtainPairView)`
- **Flow**:
  1. Rate limit 5/min per IP.
  2. Lockout check.
  3. `super().post(...)` — стандартный `TokenObtainPairView` → `authenticate()` с default backend → если успех, возвращает `{access, refresh, is_admin}`.
  4. **НЕТ role check**. Non-admin с правильным паролем получит JWT access/refresh tokens.
- **Callers**: `/api/token/`, `/api/token/refresh/`, `/api/v1/token/` — все три используют `SecureTokenObtainPairView`.
- **Risk**: это parallel password path без role-filter. Если non-admin знает свой пароль → может получить JWT и использовать его как auth для API endpoints (включая messenger, mobile app, etc.).

### Auth backends

- **`AUTHENTICATION_BACKENDS`**: НЕ переопределён → default Django `ModelBackend`.
- **`AUTH_USER_MODEL`**: `accounts.User`.
- **`MAGIC_LINK_ONLY`** env (default `"0"` → False): если `"1"`, `SecureLoginView.post` полностью disable'ит password branch (views.py:61-66). Но `/api/token/` ЭТИМ НЕ ПРОВЕРЯЕТСЯ — JWT ignoring `MAGIC_LINK_ONLY`.

---

## User account state (staging 2026-04-22)

### Role breakdown

| Role | Total | Active | Superuser | Usable pw | Unusable pw (`!`) |
|------|-------|--------|-----------|-----------|-------------------|
| admin | 3 | 3 | 2 | 3 | 0 |
| manager | 29 | 28 | 0 | 10 | 19 |
| branch_director | 3 | 3 | 0 | 3 | 0 |
| sales_head | 3 | 3 | 0 | 2 | 1 |
| group_manager | 2 | 2 | 0 | 2 | 0 |
| tenderist | 0 | — | — | — | — |
| **Total non-admin with usable password** | — | — | — | **17** | — |

### Superusers

- `sdm` — role=admin, superuser=True, password usable, last login 2026-04-22 09:03 UTC.
- `perf_check` — role=admin, superuser=True, password usable, last login 2026-04-20 10:01 UTC.
- 3rd admin = regular (non-superuser), role=admin, password usable.

### Login activity (last 7 days via ActivityEvent)

- `password_login_success`: **7** (all admins per SecureLoginView role-filter).
- `access_key_login_success`: **1**.
- `login_failed`: **4**.

**Interpretation**: admin пользуются паролем (удобно, знают). Non-admin пользователи логинятся через magic link (когда admin сгенерировал). Low traffic → possibly non-admin тоже используют долгоживущие 14-дневные сессии и редко re-login.

### MagicLinkToken statistics

| Metric | Count |
|--------|-------|
| Total tokens (all time) | 140 |
| Active (unused, non-expired) | 1 |
| Used | 108 |
| Expired (never used) | 31 |
| Created last 7 days | 3 |
| Used last 7 days | 1 |

**Interpretation**: 108/(108+31) = 78% use rate на expired/used subset — tokens обычно используются в течение TTL. Low usage (3 created + 1 used в 7 дней) подтверждает, что долгие сессии (14d rolling) = редкие re-logins.

---

## Session

### Settings (crm/settings.py:417-424)

- `SESSION_COOKIE_AGE = 1209600` sec = **14 дней** (2 weeks).
- `SESSION_SAVE_EVERY_REQUEST = True` — **rolling window** (каждый request продлевает expiry).
- `SESSION_COOKIE_HTTPONLY = True`
- `SESSION_COOKIE_SAMESITE = "Lax"`
- Override via `DJANGO_SESSION_COOKIE_AGE` env.

### Remember-me

- Нет явного checkbox в login form. Все сессии по умолчанию 14d rolling.
- Можно менять per-user через `request.session.set_expiry(N)` но не практикуется.

### JWT token lifetimes (crm/settings.py:592-614)

- `ACCESS_TOKEN_LIFETIME = 1 hour`
- `REFRESH_TOKEN_LIFETIME = 7 days`
- `ROTATE_REFRESH_TOKENS = True` + `BLACKLIST_AFTER_ROTATION = True` — стандартная rotation security.
- `ALGORITHM = HS256`, `SIGNING_KEY = SECRET_KEY`.

---

## Impersonation ("Режим просмотра")

### Confirmed feature

- **Toggle URL**: POST `/settings/admin/users/` с `toggle_view_as` param.
  - View: `ui.views.settings_core.settings_users` (line 580-600).
  - Permission: inline `require_admin(request.user)` (line 580).
  - POST с `view_as_enabled=on` → `request.session["view_as_enabled"] = True`.
  - POST с `view_as_enabled=off` → clears `view_as_enabled`, `view_as_user_id`, `view_as_role`, `view_as_branch_id`.

- **Session flags set**:
  - `view_as_enabled` (bool)
  - `view_as_user_id` (int) — конкретный user для impersonation
  - `view_as_role` (str) — можно смотреть "как role=MANAGER" без конкретного user
  - `view_as_branch_id` (int) — filter by branch

- **Helper**: `accounts.permissions.get_view_as_user(request)` (permissions.py:27)
  - Возвращает target User если enabled + valid user_id, иначе None.
  - Guards: only admin может использовать (permissions.py:38 проверяет `is_superuser OR role==ADMIN`).

- **Helper #2**: `get_effective_user(request)` (permissions.py:58) — возвращает impersonated user ИЛИ real user. Используется в queryset filters.

- **Template context**: `ui/context_processors.py:10-115` инжектит `view_as_*` во все templates (badge показывает "Admin: смотрите как X").

### Impact of W2.6 на impersonation

- Impersonation НЕ создаёт новую login-сессию — просто флаг в существующей admin-сессии.
- Admin должен быть admin (authenticated через password или magic link).
- При W2.6 (удаление password для non-admin) impersonation **не сломается** — admin по-прежнему может login (password работает для admin) + impersonate любого user.
- Important: impersonated user сам не логинится — никаких password/magic_link проверок не выполняется. Это pure session-flag feature.

---

## Findings for W2.6 plan

### Scope of change

**Главный change**: закрыть `/api/token/` JWT endpoint для non-admin.

**Candidate edit locations**:
1. **`SecureTokenObtainPairView.post`** ([jwt_views.py:33](../../backend/accounts/jwt_views.py#L33)) — добавить role check после `super().post()` success, аналогично `SecureLoginView.post:187-193`. Если `user.role != ADMIN and not is_superuser` → вернуть 403 + не возвращать токены.

**Secondary changes (optional)**:
2. **`MAGIC_LINK_ONLY=1`** env в staging/prod — full kill switch password для admin тоже (но это более radical, user не просил).
3. **Inactive non-admin password cleanup** — после W2.6 закрывает bypass, но 17 usable password hashes всё ещё в БД. Опционально: `User.objects.filter(role__in=non_admin).update(password='!' + token_urlsafe(40))` — render passwords unusable (standard Django convention). Сохраняет аудит "когда-то был пароль" без возможности использования.

### Risk

- **Direct lockout risk**: NONE. Non-admin и так не могут использовать `/login/` password form. Они используют magic link.
- **JWT bypass impact**: если non-admin сейчас где-то fetches JWT через `/api/token/` (например, mobile app), то после W2.6 это сломается. **Надо проверить**:
  - Есть ли client, который вызывает `/api/token/` с non-admin credentials?
  - Candidates: mobile app phonebridge (не похоже — QR token exchange), AmoCRM integration (serverside admin auth), external integrations.
  - Grep `/api/token/` в frontend/templates и external clients.

### Migration

**Существующие passwords** (17 non-admin usable):
- Option A (conservative): оставить as-is. View-level filter блокирует all paths после W2.6. Passwords unused.
- Option B (secure): render unusable через `user.set_unusable_password() + user.save()`. Чистый state. Admin потом может восстановить через reset, но этот flow не существует (нет UI).
- Option C (paranoid): удалить `password` field data полностью (null). Требует migration но unsafe (ломает `user.has_usable_password()` checks в коде).

**Recommended: Option A** — minimal change, если найден unexpected caller JWT для non-admin, admin сам разберётся.

### Estimated effort

- **Core change (JWT role filter)**: 10 LOC + 3 tests (role check happy path, role check rejection, JWT refresh for previously-admin user) = **~1 час**.
- **+ Client audit**: 30 мин (grep external clients).
- **+ Staging verification**: 15 мин (smoke + manual login tests).
- **Total**: **~2 часа**.

### Open questions for user

1. **`MAGIC_LINK_ONLY=1`**: enable на staging/prod? Это убьёт password login даже для admin — они вынуждены будут magic link использовать.
2. **Password cleanup** (17 non-admin с usable hash): apply Option B (`set_unusable_password`)?
3. **External clients**: есть ли integrations, которые fetch JWT с non-admin credentials? Mobile app? Zapier? Если да — они сломаются после W2.6.

---

## Mobile QR auth (Android app) — discovered for W2.6 scope

### Flow overview

1. Manager (or admin) **логинится в web** через session cookie (для non-admin это = magic link).
2. Открывает `/mobile-app/` страницу (`ui.views.mobile.mobile_app_page`, line 25).
3. Нажимает "получить QR", что POST'ит `/api/phone/qr/create/` → `QrTokenCreateView` (phonebridge/api.py:751).
4. `MobileAppQrToken` создаётся: `token = secrets.token_urlsafe(64)`, SHA-256 hash saved, TTL **5 минут**, single-use.
5. `/mobile-app/qr-image/?token=<plain>` → `mobile_app_qr_image` view генерирует PNG QR.
6. Android app сканирует QR, получает plain token.
7. App POST'ит `/api/phone/qr/exchange/` с `{token: <plain>}` → `QrTokenExchangeView` (api.py:822).
8. View: hash → lookup → validate → policy check → `qr_token.mark_as_used()` → **`RefreshToken.for_user(qr_token.user)`** → возвращает `{access, refresh, username, is_admin}`.
9. App хранит JWT, refresh на `/api/token/refresh/` при истечении access (1h TTL).

### Auth mechanism

- **`/api/phone/qr/create/`**: session auth (`IsAuthenticated`) + `enforce(resource="phone:qr:create")` policy check.
- **`/api/phone/qr/exchange/`**: **публичный endpoint** (`permission_classes = []`) — any caller может exchange токен если знает его. Token 5-min single-use защищает от abuse. `enforce(user=qr_token.user, resource="phone:qr:exchange")` — policy может блокировать по role.
- **JWT generation**: через `RefreshToken.for_user(user)` direct — **НЕ через `/api/token/` password flow**. SimpleJWT's built-in token factory.

### Role filtering

- **Exists**: `enforce(user=qr_token.user, resource="phone:qr:exchange")` на обмене (line 864-869). Default — allow. Admin может создать `PolicyRule.DENY` для конкретных ролей чтобы блокировать мобилу.
- **`phone:qr:create` resource registered**: `policy/resources.py` has `ui:mobile_app:qr` + analogous `phone:qr:*` resources.

### Token expiry / single-use

- **TTL**: 5 минут (hardcoded в `MobileAppQrToken.save()` line 366).
- **Single-use**: ✅ enforced в `is_valid()` (line 372) + `mark_as_used()` (line 380).
- Короткий TTL + single-use + hash-based lookup = безопасно против replay.

### Assessment for W2.6

| Check | Result |
|-------|--------|
| Does mobile auth accept non-admin users? | ✅ Yes (manager может exchange QR и получить JWT) |
| Is role filtering needed here? | ⚠️ Exists via `enforce()` policy, но default allow → по факту пока нет |
| Does mobile auth use `/api/token/` password? | ❌ **НЕТ** — использует `/api/phone/qr/exchange/` → `RefreshToken.for_user()` direct |
| Will W2.6 JWT fix affect mobile app? | ❌ **НЕТ** — W2.6 touches `/api/token/` only, mobile app на `/api/phone/qr/exchange/` |
| Will W2.6 cleanup (unusable passwords) affect mobile app? | ❌ **НЕТ** — mobile auth не проверяет password |
| Refresh flow после W2.6? | ✅ Работает — `/api/token/refresh/` не использует password; rotates existing refresh token |

**Conclusion for W2.6**: Mobile QR auth полностью orthogonal к `/api/token/` password path. Не требует scope expansion. Future work (когда Android app finalized для managers): consider stricter policy rule на `phone:qr:exchange` для фиксации kto именно может exchange.

---

## Session artifacts

- Docs only: `docs/audit/auth-flow-current-state.md` (this file).
- Zero code changes.
- Zero prod touches.
- Staging stats snapshot 2026-04-22 ~09:10 UTC.
- Mobile QR section added 2026-04-22 ~09:25 UTC (pre-W2.6 scope lock).
