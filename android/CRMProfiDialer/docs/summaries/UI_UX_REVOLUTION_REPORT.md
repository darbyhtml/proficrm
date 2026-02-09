# UI/UX Revolution — отчёт (CRM Profi Dialer)

**Философия:** Calm Tech, Zero clutter, One-glance UX. Продукт 2026 года, работающий как калькулятор на слабых устройствах.

---

## 1. Список изменённых UI-файлов

| Файл | Изменения |
|------|-----------|
| `res/anim/fragment_open_enter.xml` | Длительность 180ms, crossfade + translateY 6dp |
| `res/anim/fragment_open_exit.xml` | 180ms, alpha + translateY -6dp |
| `res/anim/fragment_close_enter.xml` | 180ms, alpha + translateY -6→0 |
| `res/anim/fragment_close_exit.xml` | 180ms, alpha + translateY 6dp |
| `res/layout/activity_main_with_nav.xml` | itemActiveIndicatorColor, itemIconSize, padding (уже были) |
| `res/layout/fragment_settings.xml` | Унификация: Widget.CRM.Card, dimen/spacing_base, TextAppearance.CRM.*, строки в strings |
| `res/layout/fragment_history.xml` | Поиск: elevation 1dp, ic_search (vector), search_hint, history_empty, padding dimen, contentDescription |
| `res/layout/fragment_dialer.xml` | Размер поля ввода dialer_input_text_size (20sp), contentDescription |
| `res/values/dimens.xml` | bottom_nav_icon_size (было), dialer_input_text_size (20sp) |
| `res/values/strings.xml` | settings_* (Авторизация, Работа в фоне, OEM, Диагностика, О приложении) |
| `res/drawable/ic_search.xml` | **Новый** — векторная иконка поиска (Material outline) |
| `ui/home/HomeFragment.kt` | contentDescription для statusIcon (TalkBack) |
| `ui/dialer/DialerFragment.kt` | Автоформат номера +7 (XXX) XXX-XX-XX, флаг isFormatting от рекурсии |
| `ui/history/HistoryFragment.kt` | ListAdapter + DiffUtil.ItemCallback вместо RecyclerView.Adapter + notifyDataSetChanged |
| `res/layout/fragment_home.xml` | Skeleton для блока «Сегодня»: FrameLayout + View (surface_variant, 52dp), скрывается при первой загрузке |
| `ui/home/HomeFragment.kt` | Ссылка на todayStatsSkeleton, скрытие при первом вызове updateTodayStats |

**Без изменений (уже соответствовали спецификации):**  
`fragment_home.xml`, `item_call_history.xml`, `bottom_navigation.xml`, логика MainActivity (подтверждение выхода, анимации фрагментов), haptic в Dialer, bottom sheet по тапу в истории.

---

## 2. Что где изменилось (по экранам)

- **Навигация:** Переходы между вкладками — crossfade + лёгкий сдвиг по вертикали (6dp), длительность 180ms. Активная вкладка — индикатор цвета surface_variant. Иконки 24dp, отступы 6dp, чтобы не наплывали на подписи.
- **Главная:** Статус, последняя команда, кнопка «Исправить» уже были. Добавлен динамический contentDescription у статус-иконки для TalkBack. Добавлен лёгкий skeleton для блока «Сегодня» (один слой surface_variant, без shimmer): показывается до первой загрузки статистики из callHistoryStore. Добавлен лёгкий skeleton для блока «Сегодня» (один слой surface_variant, без shimmer): показывается до первой загрузки статистики из callHistoryStore.
- **Телефон:** Крупное поле ввода (20sp). Автоформат номера в виде +7 (XXX) XXX-XX-XX при вводе. Подпись «Звонок будет зафиксирован в истории», кнопка «Позвонить», haptic — без изменений.
- **История:** Строка поиска — elevation 1dp, векторная иконка поиска, подпись из strings. Пустое состояние — строка history_empty. Список — ListAdapter + DiffUtil (минимальные перерисовки, меньше allocation). Тап по элементу → bottom sheet с деталями (как и раньше).
- **Настройки:** Все карточки переведены на Widget.CRM.Card и dimen/spacing_base. Заголовки и тексты — TextAppearance.CRM.*. Строки вынесены в strings (settings_auth_title, settings_background_title, settings_oem_help, settings_diagnostics_*, settings_about_title и т.д.). Выход через Bottom Nav — с подтверждением (уже было в MainActivity).

---

## 3. UX rationale

- **Одна анимация на взаимодействие, ≤180ms:** Снижает нагрузку и ощущение «тормозов» на слабых устройствах, при этом смена экрана остаётся читаемой.
- **ListAdapter + DiffUtil в истории:** Меньше лишних bind’ов и allocation, плавный скролл при обновлении списка.
- **Автоформат номера:** Меньше ошибок при наборе, быстрее визуальная проверка номера без изменения логики (звонок по нормализованному номеру).
- **Единые spacing/typography в настройках:** Визуально один стиль с остальным приложением, проще поддержка и смена масштаба шрифта.
- **Векторная иконка поиска + 1dp elevation:** Меньше визуального шума, соответствие правилу «flat + subtle depth».

---

## 4. Performance checklist (почему не тормозит)

- **Анимации:** Только ViewPropertyAnimator/XML anim (alpha + translate). Нет Lottie, blur, spring.
- **Списки:** RecyclerView + ListAdapter + DiffUtil, один тип ViewHolder, без тяжёлых вложенных layout’ов в item_call_history.
- **Drawable:** Только векторные иконки (ic_search, ic_nav_*). Нет runtime blur, нет bitmap scaling в UI thread.
- **Темы/цвета:** Через styles и dimen, без дублирования inline.
- **Глубина layout:** В экранах — LinearLayout/ConstraintLayout, глубина вложенности ≤3. Карточки — MaterialCardView с одним внутренним LinearLayout.

---

## 5. Ничего не сломано

- Бизнес-логика, сервисы, long-poll, диагностика не трогались.
- Навигация (Bottom Navigation, вкладки) сохранена.
- LOCAL_ONLY / FULL, ViewModel и data-слой без изменений.
- Подтверждение выхода, обработка Fix action, onboarding — как раньше.

---

## 6. Accessibility

- Touch targets ≥ 48dp (button_height, touch_target_min, minHeight у полей и кнопок).
- contentDescription: поле ввода телефона, поиск в истории, статус на главной (динамически по состоянию).
- Контраст: on_surface на surface, on_surface_variant для вторичного текста (соответствие WCAG AA заложено в цветах).
- Шрифты: размеры в sp (text_headline_size, text_title_size и т.д.) — масштаб шрифта системы не ломает layout.

---

*Итог: интерфейс приведён к единому стилю, анимации и списки оптимизированы под слабые устройства, добавлены автоформат номера и доступность. Изменён только UI-слой.*
