# Волна 7. Телефония + Android-приложение

**Цель волны:** Достроить phonebridge backend, зафиксировать API-контракт, написать Android-приложение (Kotlin + Jetpack Compose) с push-уведомлениями для click-to-call, входящими вызовами с оверлеем, и синхронизацией истории.

**Параллелизация:** высокая. Backend (7.1–7.4) и Android (7.5–7.9) — разные репозитории, разные стеки. Можно вести полностью параллельно после фиксации контракта.

**Длительность:** 15–20 рабочих дней (Android app — сам по себе 2-3 недели).

**Требования:** Wave 2 завершена (JWT + 2FA). Wave 4.2 (Notification Hub) завершена.

**Важно:** Android-приложение пишется в ОТДЕЛЬНОМ git-репозитории (например `groupprofi-android`). В плане фиксируем только контракт + минимальный набор экранов.

---

## Этап 7.1. API-контракт для phonebridge (OpenAPI)

### Контекст
Backend phonebridge недописан. Android-приложения ещё нет. Прежде чем писать одно и другое — фиксируем контракт.

### Цель
Полный OpenAPI 3.0 спецификация `/api/v1/phone/*` endpoint'ов. Оба стороны (backend + Android) пишут по этой спеке.

### Что делать
1. Описать endpoints:
   - `POST /api/v1/phone/devices/register` — регистрация устройства (device_id, fcm_token, os_version, app_version, phone_number).
   - `POST /api/v1/phone/devices/pair` — QR-спаривание: устройство получает pairing_code от CRM, подтверждает.
   - `GET /api/v1/phone/devices/` — list устройств текущего юзера.
   - `DELETE /api/v1/phone/devices/<id>/` — удалить устройство.
   - `POST /api/v1/phone/call-requests/` — создать запрос на звонок (actor - CRM user, target - phone).
   - `GET /api/v1/phone/call-requests/<id>/` — статус запроса.
   - `POST /api/v1/phone/events/` — event от Android: `ringing | answered | ended | missed | rejected`.
   - `GET /api/v1/phone/match/?phone=+7xxx` — матчинг номера → companyId / contactId (для incoming call overlay).
   - `GET /api/v1/phone/history/` — история звонков (для sync в приложении).
   - `POST /api/v1/phone/history/` — batch upload локальной истории с устройства.

2. Authentication: JWT с refresh, device-bound.

3. Errors: `{"error": {"code": "...", "message": "..."}}`.

4. Generate OpenAPI YAML через drf-spectacular + ручная доводка.

5. Согласовать с собой (или с Android-разработчиком) до начала имплементации.

### Инструменты
- `drf-spectacular`
- `mcp__context7__*` — OpenAPI spec patterns

### Definition of Done
- [ ] Полная OpenAPI 3.0 спека в `docs/api/phonebridge.yaml`
- [ ] Все endpoints описаны с request/response schemas
- [ ] Error shapes стандартизованы
- [ ] Примеры (examples) для всех endpoints
- [ ] Валидация через `openapi-spec-validator`

### Артефакты
- `docs/api/phonebridge.yaml`
- `docs/api/phonebridge.md` (human-readable overview)
- `docs/api/phonebridge-changelog.md`

### Валидация
```bash
openapi-spec-validator docs/api/phonebridge.yaml
redocly lint docs/api/phonebridge.yaml  # no errors
```

### Откат
N/A (только документация).

### Обновить в документации
- `docs/api/phonebridge.yaml`

---

## Этап 7.2. Phonebridge backend: device registration + pairing

### Контекст
Есть `PhoneDevice` модель, QR-спаривание "недоделано" (память). Нужно довести до ума.

### Цель
Надёжная регистрация устройства с QR-спариванием в CRM.

### Что делать
1. **Model upgrade**:
   - `PhoneDevice`:
     - user (FK)
     - device_id (unique, generated client-side)
     - fcm_token (nullable — может меняться)
     - phone_number (E.164)
     - os_version, app_version
     - paired_at, last_seen_at, is_active
     - pairing_code (temp, unique, expires)

2. **Pairing flow**:
   - В CRM: «Подключить устройство» → generate pairing_code (короткий, 6 символов или QR-payload).
   - CRM показывает QR-код с payload: `{"api_url": "...", "pairing_code": "XXXXXX", "expires_at": "..."}`.
   - Android scan QR → регистрирует device через `POST /devices/register` с pairing_code → получает JWT access+refresh.
   - JWT — device-bound: refresh только с device_id.

3. **Multi-device support**:
   - Один юзер может иметь несколько устройств (основное + запасное).
   - CRM показывает list с last_seen, возможность удалить / deactivate.

4. **Heartbeat**:
   - Приложение раз в 5 минут — `POST /devices/<id>/heartbeat`.
   - last_seen_at обновляется.
   - Если last_seen > 24h — device marked as stale (не отправляем push).

5. **Token refresh**:
   - FCM token может ротироваться. Android отправляет обновление при изменении.

6. **Admin UI**:
   - Список всех PhoneDevice.
   - Возможность принудительно разлогинить device (revoke JWT).

### Инструменты
- `mcp__context7__*`
- `qrcode[pil]`

### Definition of Done
- [ ] Pairing flow работает end-to-end (в Postman / curl, Android пока не готов)
- [ ] Multi-device support
- [ ] Heartbeat
- [ ] Token refresh
- [ ] Admin UI
- [ ] E2E тесты (mock Android client)

### Артефакты
- Миграции
- `backend/phonebridge/services/device_service.py`
- `backend/phonebridge/services/pairing_service.py`
- `backend/phonebridge/views/devices.py`
- `backend/ui/views/pages/profile/devices.py`
- `backend/templates/profile/devices/*.html`
- `tests/phonebridge/test_device_pairing.py`
- `docs/features/phone-pairing.md`

### Валидация
```bash
pytest tests/phonebridge/test_device_pairing.py
curl -X POST http://staging/api/v1/phone/devices/register ...
```

### Откат
```bash
git revert
```

### Обновить в документации
- `docs/features/phone-pairing.md`

---

## Этап 7.3. Click-to-call flow

### Контекст
Основной сценарий: менеджер в CRM жмёт «позвонить» на номере контакта → FCM push на устройство → Android делает `Intent.ACTION_CALL` с номером.

### Цель
Надёжный, быстрый click-to-call с обратной связью в CRM (статус звонка).

### Что делать
1. **Call request flow**:
   - CRM: кнопка «позвонить» → POST `/call-requests/` с `phone_number`, `company_id`, `contact_id`.
   - Backend: создать `CallRequest` (status=pending), отправить FCM push с data-payload.
   - Android: получить push → показать notification → при tap — запустить Intent.ACTION_CALL.
   - Android после звонка (через CallLog ContentObserver или события) → POST `/events/` с duration, status.
   - CRM: обновить CallRequest status + длительность, создать запись в CompanyHistoryEvent.

2. **FCM payload**:
   ```json
   {
     "type": "call_request",
     "call_id": "...",
     "phone": "+7...",
     "contact_name": "...",
     "company_name": "...",
     "ttl": 30
   }
   ```
   Data-only message (high priority) — чтобы показать даже в doze mode.

3. **Status transitions**:
   - pending → ringing → answered (duration>0) / missed / rejected / ended
   - Timeout: если в `ttl` секунд нет ответа от устройства → pending → failed.

4. **UI в CRM**:
   - Кнопка «Позвонить» рядом с каждым телефоном.
   - Вибрация / индикатор «запрос отправлен, ожидание...».
   - После события — «говорили Xм Yс» / «пропущено» / «занято».

5. **Fallback**: если device offline > 30 секунд → message «устройство недоступно, позвоните вручную».

6. **Audit**: каждый CallRequest → в CompanyHistoryEvent с детали.

### Definition of Done
- [ ] Call request создаётся
- [ ] FCM push доставляется
- [ ] Events от Android принимаются
- [ ] UI отображает statuses в realtime (SSE/WebSocket)
- [ ] Timeout / offline handling
- [ ] Audit

### Артефакты
- `backend/phonebridge/services/call_service.py`
- `backend/phonebridge/services/fcm_sender.py`
- `backend/phonebridge/views/calls.py`
- `backend/static/ui/call-button.js`
- `tests/phonebridge/test_call_flow.py`
- `docs/features/click-to-call.md`

### Валидация
```bash
pytest tests/phonebridge/test_call_flow.py
# Integration test with FCM dry-run
```

### Откат
```bash
git revert
```

### Обновить в документации
- `docs/features/click-to-call.md`

---

## Этап 7.4. Incoming call matching + overlay

### Контекст
Второй сценарий: входящий звонок на менеджера → Android показывает overlay «Звонит [Company Name / Contact]» с кнопкой «открыть карточку».

### Цель
Быстрый matching номера → сущность CRM в момент входящего звонка.

### Что делать
1. **Endpoint `GET /api/v1/phone/match/?phone=+7xxx`**:
   - Normalize phone → E.164.
   - Поиск в `ContactPhone`, `CompanyPhone` (с учётом data scope user'a).
   - Возврат: `{"found": true, "company": {...}, "contact": {...}, "last_call": {...}, "notes_count": X, "deal_count": Y}`.
   - Если не найдено — `{"found": false, "suggest": "create_contact"}`.

2. **Cache**:
   - Redis cache 10 минут на pair (user_id, phone).

3. **Latency**: < 200ms (matcher должен быть быстрым, FTS по индексу).

4. **Android side** (будет в 7.8): получает incoming call, читает номер, запрашивает match, показывает overlay.

### Definition of Done
- [ ] Endpoint работает с latency < 200ms
- [ ] Нормализация номеров корректна (учитывает +7 / 8 / без префикса)
- [ ] Data scope соблюдается
- [ ] Cache работает

### Артефакты
- `backend/phonebridge/services/matcher.py`
- `backend/phonebridge/views/match.py`
- `tests/phonebridge/test_matcher.py`
- `docs/features/incoming-call-match.md`

### Валидация
```bash
pytest tests/phonebridge/test_matcher.py
ab -n 1000 -c 10 'http://localhost:8001/api/v1/phone/match/?phone=%2B79991234567'
```

### Откат
```bash
git revert
```

### Обновить в документации
- `docs/features/incoming-call-match.md`

---

## Этап 7.5. Android app skeleton (Kotlin + Compose)

### Контекст
Android-проект создаётся с нуля. Kotlin + Jetpack Compose + Clean Architecture + Hilt + Retrofit + Room.

### Цель
Скелет приложения с навигацией, auth flow, базовой структурой.

### Что делать
1. **Project setup**:
   - Android Studio → New Project → Compose Empty Activity.
   - Kotlin 1.9+, Gradle 8+.
   - Min SDK 24 (Android 7.0), Target SDK 34.
   - Package `ru.groupprofi.crm`.
   - Git init, отдельный репозиторий на GitHub.

2. **Dependencies**:
   - Jetpack Compose + Material 3.
   - Hilt (DI).
   - Retrofit + OkHttp + Gson/Moshi.
   - Room (local DB).
   - DataStore (preferences).
   - Coroutines + Flow.
   - Coil (image loading).
   - Timber (logging).
   - Firebase Messaging (FCM).

3. **Architecture**:
   - Clean: `data/ (api, db, repository)` / `domain/ (usecase, model)` / `presentation/ (screen, viewmodel, ui)`.
   - Один feature-module на каждую большую фичу: `auth`, `call`, `contacts`, `settings`.

4. **Navigation**:
   - Compose Navigation.
   - 4 main screens: Pairing (QR scan), Call History, Settings, Profile.

5. **Theme**:
   - Material 3 с color scheme (в стиль CRM web).
   - Light / Dark (опционально).
   - Typography (системные шрифты).

6. **Build variants**:
   - `dev` (pointed to staging), `prod` (pointed to crm.groupprofi.ru).
   - Build flavors separated.

7. **Signing**:
   - Debug keystore автоматический.
   - Release keystore — отдельно, закоммичен в зашифрованном виде (ansible-vault или sops).

8. **CI** (GitHub Actions):
   - `./gradlew lint detekt test`
   - APK assembly on tag push.

### Инструменты
- Android Studio (вручную)
- `mcp__context7__*` — Kotlin, Jetpack Compose docs
- `mcp__github__*` — repo setup

### Definition of Done
- [ ] Проект собирается (`./gradlew assembleDebug`)
- [ ] Запускается в эмуляторе, показывает стартовый экран «Сканируйте QR»
- [ ] CI зелёный
- [ ] Minimal unit test passing
- [ ] Detekt без warnings

### Артефакты (в отдельном репо)
- `app/build.gradle.kts`, `build.gradle.kts`
- `app/src/main/java/ru/groupprofi/crm/MainActivity.kt`
- `app/src/main/java/ru/groupprofi/crm/di/AppModule.kt`
- `.github/workflows/android-ci.yml`
- `README.md`

### Валидация
```bash
./gradlew clean assembleDebug
./gradlew test
./gradlew detekt
```

### Откат
Revert в Android репо.

### Обновить в документации
- `docs/mobile/android-architecture.md` (в основном CRM репо)

---

## Этап 7.6. Android: QR pairing + auth

### Контекст
После скелета — первый реальный flow: сканирование QR из CRM и получение JWT.

### Цель
Пользователь сканирует QR → приложение авторизовано, токены в DataStore.

### Что делать
1. **QR scanner**: CameraX + ML Kit Barcode Scanner.

2. **Pairing screen**:
   - Camera preview с overlay области сканирования.
   - После успешного скана — вызов `POST /devices/register` с pairing_code.
   - Response: JWT access + refresh + device_id.
   - Сохранить в DataStore (encrypted).

3. **Token refresh interceptor**:
   - Retrofit Interceptor: при 401 → refresh, retry.

4. **Logout**:
   - Settings screen → logout → вызов `DELETE /devices/<id>` → clear DataStore.

5. **Re-pair**:
   - При смене устройства / переустановке — re-pairing.

### Инструменты
- CameraX, ML Kit Barcode
- `mcp__context7__*`

### Definition of Done
- [ ] QR scan работает, находит pairing_code
- [ ] Registration успешен, JWT сохранён
- [ ] Token refresh работает
- [ ] Logout работает

### Артефакты
- `app/src/main/java/ru/groupprofi/crm/feature/auth/*`
- Tests

### Валидация
Manual в эмуляторе + на устройстве.

### Откат
N/A.

### Обновить в документации
- `docs/mobile/android-auth.md`

---

## Этап 7.7. Android: FCM + click-to-call receiver

### Контекст
Приложение слушает FCM, получает call_request, запускает dialer.

### Цель
Надёжный receive + dial.

### Что делать
1. **Firebase setup**:
   - `google-services.json` (staging + prod flavors).
   - `FirebaseMessagingService` — обработка data-payload.

2. **Incoming call request handler**:
   - Parse payload → открыть `CallRequestActivity` (fullscreen intent).
   - Показать карточку: «Позвонить [Имя]? [Отмена] [Позвонить]».
   - При Позвонить — `Intent.ACTION_CALL` с разрешением `CALL_PHONE`.

3. **Post-call tracking**:
   - `BroadcastReceiver` для `ACTION_NEW_OUTGOING_CALL` + `PHONE_STATE`.
   - Определить end-time, duration.
   - Отправить event `POST /events/` (с retry в background worker).

4. **Foreground notification**:
   - Persistent notification «GroupProfi CRM работает» (Android требует для background service).

5. **Permissions**:
   - `CALL_PHONE`, `READ_PHONE_STATE`, `READ_CALL_LOG`, `POST_NOTIFICATIONS` (Android 13+).
   - Runtime prompt с rationale.

6. **Battery optimization**:
   - Запрос исключения из Doze mode (с обоснованием для юзера).

### Definition of Done
- [ ] FCM push принимается
- [ ] Fullscreen call intent работает даже при locked screen
- [ ] Dialing запускается
- [ ] Event отправляется после звонка
- [ ] Permissions запрашиваются корректно

### Артефакты
- `feature/call/CallRequestActivity.kt`
- `feature/call/FcmService.kt`
- `feature/call/CallStateReceiver.kt`

### Валидация
Manual:
1. В CRM создать call request.
2. На устройстве — push должен прийти < 5 сек.
3. Показать fullscreen → позвонить → статус обновился в CRM.

### Откат
N/A.

### Обновить в документации
- `docs/mobile/android-fcm.md`

---

## Этап 7.8. Android: Incoming call overlay

### Контекст
Входящий звонок → приложение смотрит номер → запрашивает match → показывает overlay.

### Цель
Overlay с информацией о звонящем.

### Что делать
1. **CallScreeningService** (API 24+) — перехватывает входящий звонок до показа стандартного UI.

2. **Альтернатива** (для < 24 и как fallback): BroadcastReceiver на `ACTION_PHONE_STATE_CHANGED`.

3. **Logic**:
   - Получить phone_number.
   - Вызов `GET /phone/match/?phone=...` (через WorkManager с timeout 3 сек).
   - Если found → показать Overlay Window (системное разрешение `SYSTEM_ALERT_WINDOW`) с Company / Contact info + кнопкой «открыть в CRM».
   - Кнопка → deep link в web-CRM (открывает браузер на правильной странице).

4. **Privacy**:
   - Номера не логировать без разрешения.
   - Overlay появляется только если matched (чтобы не раскрывать структуру БД).

5. **Permissions**:
   - `SYSTEM_ALERT_WINDOW` (через Settings intent).
   - `READ_CALL_LOG`.

### Definition of Done
- [ ] Overlay показывается при входящем от known contact
- [ ] Нет overlay для unknown
- [ ] Deep link открывает карточку в браузере
- [ ] Latency < 2s от звонка до overlay

### Артефакты
- `feature/call/IncomingCallScreeningService.kt`
- `feature/call/OverlayView.kt`

### Валидация
Manual на реальном устройстве.

### Откат
N/A.

### Обновить в документации
- `docs/mobile/android-overlay.md`

---

## Этап 7.9. Android: Call history + sync

### Контекст
Приложение должно периодически синхронизировать локальную call history с CRM.

### Цель
Bi-directional sync call history.

### Что делать
1. **Local DB** (Room):
   - `LocalCall` entity: phone, timestamp, duration, direction, synced.

2. **ContentObserver** на CallLog:
   - При новом звонке — добавить в Room, mark `synced=false`.

3. **WorkManager** (`PeriodicWorkRequest` every 1h + `OneTimeWorkRequest` при connectivity):
   - Upload pending calls → `POST /history/` (batch).
   - Mark synced.

4. **UI**:
   - Screen «История звонков» — list из Room с pull-to-refresh.
   - Тап на запись → deep link в CRM.

5. **Conflict resolution**:
   - CRM — источник правды для статусов (CallRequest.status).
   - Local — только локальные события.

### Definition of Done
- [ ] Call history sync работает
- [ ] Offline / online handling
- [ ] UI list работает

### Артефакты
- `feature/history/*`

### Валидация
Manual: сделать звонки offline → connect → проверить sync.

### Откат
N/A.

### Обновить в документации
- `docs/mobile/android-history.md`

---

## Checklist завершения волны 7

- [ ] OpenAPI контракт зафиксирован
- [ ] Backend phonebridge допилен (pairing, devices, calls, match, events)
- [ ] Android app: QR pairing работает
- [ ] Click-to-call flow end-to-end работает
- [ ] Incoming call overlay работает
- [ ] Call history sync работает
- [ ] APK опубликован в тестовую группу (5-10 менеджеров в одном филиале)

**Android-приложение для внутреннего распространения.** Тестовая группа в Тюмени — 2 недели → сбор фидбэка → корректировки → остальные филиалы.
