# Smoke Checklist: Call Analytics (ЭТАП 6)

**Время выполнения:** ~10 минут  
**Цель:** Быстрая проверка критических сценариев перед релизом

---

## Backend тесты (2-3 минуты)

### 1. API совместимость
```bash
cd backend
python manage.py test phonebridge.tests.UpdateCallInfoViewTest.test_legacy_payload_acceptance
python manage.py test phonebridge.tests.UpdateCallInfoViewTest.test_extended_payload_acceptance
python manage.py test phonebridge.tests.UpdateCallInfoViewTest.test_unknown_status_persists
```

**Ожидаемый результат:** Все тесты проходят (3 теста, ~1 сек)

### 2. Статистика и распределения
```bash
python manage.py test phonebridge.tests_stats.CallStatsViewTest.test_connect_rate_percent_calculation
python manage.py test phonebridge.tests_stats.CallStatsViewTest.test_avg_duration_only_connected
python manage.py test phonebridge.tests_stats.CallStatsViewTest.test_distributions_by_direction
```

**Ожидаемый результат:** Все тесты проходят (3 теста, ~1 сек)

### 3. Template safety
```bash
python manage.py test ui.tests.test_calls_stats_view.CallsStatsViewTemplateSafetyTest.test_view_context_keys_present
python manage.py test ui.tests.test_calls_stats_view.CallsStatsViewTemplateSafetyTest.test_view_with_calls_without_new_fields
```

**Ожидаемый результат:** Все тесты проходят (2 теста, ~1 сек)

### 4. Graceful обработка неизвестных enum
```bash
python manage.py test phonebridge.tests.UpdateCallInfoViewTest.test_invalid_direction_graceful_handling
python manage.py test phonebridge.tests.UpdateCallInfoViewTest.test_invalid_resolve_method_graceful_handling
python manage.py test phonebridge.tests.UpdateCallInfoViewTest.test_invalid_action_source_graceful_handling
```

**Ожидаемый результат:** Все тесты проходят (3 теста, ~1 сек)

---

## Android unit-тесты (1-2 минуты)

### 5. CallEventPayload
```bash
cd android/CRMProfiDialer
./gradlew test --tests "ru.groupprofi.crmprofi.dialer.domain.CallEventPayloadTest"
```

**Ожидаемый результат:** Все тесты проходят (4 теста)

### 6. Enum mapping
```bash
./gradlew test --tests "ru.groupprofi.crmprofi.dialer.domain.CallDirectionTest"
./gradlew test --tests "ru.groupprofi.crmprofi.dialer.domain.ResolveMethodActionSourceTest"
```

**Ожидаемый результат:** Все тесты проходят (6 тестов)

### 7. PhoneNumberNormalizer
```bash
./gradlew test --tests "ru.groupprofi.crmprofi.dialer.domain.PhoneNumberNormalizerTest"
```

**Ожидаемый результат:** Все тесты проходят (8 тестов)

### 8. CallStatsUseCase
```bash
./gradlew test --tests "ru.groupprofi.crmprofi.dialer.domain.CallStatsUseCaseTest"
```

**Ожидаемый результат:** Все тесты проходят (7 тестов)

---

## Сборка staging APK (2-3 минуты)

### 9. Сборка staging debug
```bash
cd android/CRMProfiDialer
./gradlew assembleStagingDebug
```

**Ожидаемый результат:** APK собран успешно (`app/build/outputs/apk/staging/debug/app-staging-debug.apk`)

**Примечание:** Для сборки с минификацией используйте `assembleStagingMinified` (staging flavor + minified buildType)

---

## API проверки (curl) (2-3 минуты)

### 10. Legacy payload
```bash
curl -X POST http://localhost:8000/api/phone/calls/update/ \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "call_request_id": "<uuid>",
    "call_status": "connected",
    "call_started_at": "2024-01-15T14:30:00Z",
    "call_duration_seconds": 180
  }'
```

**Ожидаемый результат:** `200 OK`, `{"ok": true}`

### 11. Extended payload
```bash
curl -X POST http://localhost:8000/api/phone/calls/update/ \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "call_request_id": "<uuid>",
    "call_status": "connected",
    "call_started_at": "2024-01-15T14:30:00Z",
    "call_duration_seconds": 180,
    "direction": "outgoing",
    "resolve_method": "observer",
    "attempts_count": 1,
    "action_source": "crm_ui"
  }'
```

**Ожидаемый результат:** `200 OK`, `{"ok": true}`

### 12. Unknown статус
```bash
curl -X POST http://localhost:8000/api/phone/calls/update/ \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "call_request_id": "<uuid>",
    "call_status": "unknown",
    "call_started_at": "2024-01-15T14:30:00Z"
  }'
```

**Ожидаемый результат:** `200 OK`, `{"ok": true}`

---

## UI проверки (браузер) (2-3 минуты)

### 13. Статистика звонков
**URL:** `http://localhost:8000/settings/calls/stats/?period=day`

**Проверки:**
- ✅ Страница загружается без ошибок
- ✅ Видно "Дозвоняемость: X%"
- ✅ Видно "Не определено: X" (если есть unknown)
- ✅ Таблица отображается корректно
- ✅ Блоки распределений показываются (если есть данные)

### 14. История звонков менеджера
**URL:** `http://localhost:8000/settings/calls/stats/<manager_id>/?period=day`

**Проверки:**
- ✅ Страница загружается без ошибок
- ✅ Звонки с новыми полями отображаются (direction, "До HH:mm")
- ✅ Звонки без новых полей отображаются (не показывается "None")
- ✅ Фильтр "Не удалось определить" работает

### 15. Аналитика пользователя
**URL:** `http://localhost:8000/analytics/users/<user_id>/?period=day`

**Проверки:**
- ✅ Страница загружается без ошибок
- ✅ Новые поля отображаются компактно (если есть)
- ✅ Для админов видно "Источник: ..." (если есть)

---

## Итоговый чек-лист

- [ ] Backend тесты: все проходят (11 тестов)
- [ ] Android тесты: все проходят (25 тестов)
- [ ] Staging APK: собран успешно
- [ ] Legacy payload: 200 OK
- [ ] Extended payload: 200 OK
- [ ] Unknown статус: 200 OK
- [ ] UI статистика: загружается без ошибок
- [ ] UI история: загружается без ошибок
- [ ] UI аналитика: загружается без ошибок

**Время:** ~10 минут  
**Статус:** ✅ Готово к релизу / ❌ Требует исправлений
