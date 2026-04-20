# Волна 9. UX/UI унификация

**Цель волны:** Привести визуал к единому стилю. Формализовать дизайн-систему. Убрать «лоскутное одеяло» из v2+v3 токенов, смешанных стилей (Notion + amoCRM popup), непоследовательной типографики.

**Параллелизация:** высокая. Можно вести параллельно с W5/W6/W7.

**Длительность:** 10–14 рабочих дней.

**Требования:** Wave 1 завершена (refactor). Tailwind 3.4 конфигурация в порядке.

**Важно:** НЕ переписываем на SPA. Тzhaem Tailwind + Django templates + Alpine.js.

---

## Этап 9.1. Design system finalization

### Контекст
`--v2-*` и `--v3-*` tokens дублируются. `docs/ui/ICONS.md` говорит Heroicons Outline, но используется неконсистентно. Стиль смешанный (Notion minimal + amoCRM popup menu).

### Цель
Зафиксировать design system: токены, типографика, spacing, colors, shadows, radii. Один стиль, один источник правды.

### Что делать
1. **Design tokens** (`backend/static/ui/design/tokens.css`):
   - Colors: primary, secondary, accent, success, warning, danger + neutral grayscale.
   - Text colors по семантике: primary, secondary, muted, disabled, inverse.
   - Backgrounds по семантике: surface, elevated, sunken.
   - Spacing scale: 4 / 8 / 12 / 16 / 24 / 32 / 48 / 64.
   - Radius: sm (4), md (6), lg (8), xl (12), full.
   - Shadow: sm, md, lg, xl.
   - Font sizes: xs / sm / base / lg / xl / 2xl / 3xl.
   - Line heights.
   - Font weights.
   - Transition durations.

2. **Tailwind config**:
   - Обновить `tailwind.config.js` — все theme.extend подтягивает из design tokens.
   - Removing `--v2-*` unified под `--v3-*` (или переименовать в `--brand-*`).

3. **Component vocabulary**:
   - Buttons: primary / secondary / ghost / danger / link — 3 размера (sm / md / lg).
   - Inputs: text / textarea / select / checkbox / radio / date / number — единые стили.
   - Cards: default / elevated / outlined.
   - Modals / Dialogs: 3 размера.
   - Tables: default / compact / dense.
   - Badges / Tags.
   - Alerts / Toasts.

4. **Figma** (опционально):
   - Если заведёшь Figma файл с design system — отлично. Если нет — документация достаточна.

5. **Storybook-like gallery**:
   - Страница `/design-system/` (ADMIN only) — все компоненты с кодом.
   - Используется для ревью новых экранов.

### Инструменты
- `frontend-design` skill (обязательно прочитать)
- `mcp__context7__*`

### Definition of Done
- [ ] Design tokens зафиксированы в одном файле
- [ ] Tailwind config подтягивает tokens
- [ ] `--v2-*` полностью убраны (всё `--v3-*`)
- [ ] Component gallery работает
- [ ] Документация полная

### Артефакты
- `backend/static/ui/design/tokens.css`
- `tailwind.config.js` (обновлённый)
- `backend/ui/views/pages/admin/design_system.py`
- `backend/templates/admin/design_system.html`
- `docs/ui/DESIGN_SYSTEM.md`
- `docs/ui/COMPONENTS.md`

### Валидация
```bash
grep -r "v2-" backend/static/  # 0 hits
grep -r "v2-" backend/templates/  # 0 hits (кроме docs)
```

### Откат
```bash
git revert
```

### Обновить в документации
- `docs/ui/DESIGN_SYSTEM.md`
- `docs/decisions.md`: ADR-018

---

## Этап 9.2. Icons унификация

### Контекст
`docs/ui/ICONS.md` говорит Heroicons Outline. Проверить соблюдение.

### Цель
100% иконок — Heroicons Outline (или Solid для активных состояний, но системно).

### Что делать
1. **Audit**: поиск всех `<svg>` и `<i class="fas/fab">` (Font Awesome) в templates.
2. **Replace**: все non-Heroicons → соответствующие Heroicons.
3. **Icon library**:
   - `{% icon "chevron-down" size=20 %}` template tag.
   - Inline SVG (не внешние requests).
   - Optional classes.
4. **Accessibility**: все decorative иконки — `aria-hidden="true"`; смысловые — с `<span class="sr-only">`.

### Definition of Done
- [ ] Все иконки — Heroicons
- [ ] Template tag `{% icon %}` используется
- [ ] Accessibility соблюдена

### Артефакты
- `backend/core/templatetags/icons.py`
- `backend/templates/partials/icon.html`
- Все templates — обновлённые

### Валидация
```bash
grep -r "fa-" backend/templates/  # 0 hits
grep -r 'class="fa' backend/templates/  # 0 hits
```

### Откат
```bash
git revert
```

### Обновить в документации
- `docs/ui/ICONS.md` (актуализация)

---

## Этап 9.3. Mobile responsive audit + fixes

### Контекст
Field-менеджеры в выездах смотрят CRM с телефона. Нужна responsive адаптация от 360px.

### Цель
Все ключевые страницы работают от 360px до 1920px.

### Что делать
1. **Audit**: Playwright тест на 5 viewports: 360, 768, 1024, 1440, 1920.
   - Принудительно иметь скриншоты + overflow/scroll detection.
   - Список проблемных мест — в отчёт.

2. **Breakpoints**:
   - Использовать Tailwind breakpoints (`sm`, `md`, `lg`, `xl`).
   - Mobile-first подход в новом коде.

3. **Key screens**:
   - Dashboard: collapse sidebar, 1-column виджеты.
   - Company list: table → cards на mobile.
   - Company detail: tabs → accordion на mobile.
   - Deal form: vertical stack.
   - Chat operator: single column на mobile, swipe между списком/диалогом.
   - Analytics: scrollable charts, сжатые таблицы.
   - Login / Register: центрированная форма.

4. **Navigation**:
   - Sidebar → hamburger menu на mobile.
   - Bottom tabs на мобильных? (опционально).

5. **Touch targets**:
   - Минимум 44x44px для интерактивных элементов.

6. **Forms**:
   - `input type="tel"`, `type="email"` для правильной клавиатуры.
   - `autocomplete` атрибуты.
   - Labels всегда видимы (не placeholder-only).

### Инструменты
- `mcp__playwright__*` — visual regression на 5 viewports

### Definition of Done
- [ ] Все key screens responsive от 360px до 1920px
- [ ] Нет horizontal scroll (кроме таблиц с явным overflow-x-auto)
- [ ] Touch targets ≥ 44px
- [ ] Hamburger menu работает на mobile
- [ ] Visual regression baseline зафиксирован

### Артефакты
- `tests/visual/responsive/*.spec.ts`
- `backend/templates/*.html` — обновлённые (много файлов)
- `docs/ui/RESPONSIVE.md`

### Валидация
```bash
playwright test tests/visual/responsive/
```

### Откат
```bash
git revert
```

### Обновить в документации
- `docs/ui/RESPONSIVE.md`

---

## Этап 9.4. Empty / loading / error states

### Контекст
Сейчас многие страницы показывают сырой «no results» или ничего при ошибке.

### Цель
Каждое возможное состояние UI — явно визуализировано с понятным действием.

### Что делать
1. **Empty states**:
   - «У вас нет компаний. Создайте первую» + иконка + кнопка.
   - «Нет результатов по вашему фильтру» + совет «измените фильтры» + «сбросить».
   - «Нет задач на сегодня. Отдохните 🎉» (лёгкий тон).

2. **Loading states**:
   - Skeleton screens для list views (не spinner).
   - Spinner для inline actions (button loading).
   - Optimistic UI где можно (отметка задачи выполненной — моментально).

3. **Error states**:
   - 404: «Страница не найдена» + кнопка «на главную».
   - 403: «Нет доступа. Обратитесь к <user.branch.admin_contact>».
   - 500: «Что-то пошло не так. Попробуйте позже. Ошибка <sentry_trace_id>».
   - Offline: banner «Нет интернета, данные могут быть устаревшими».
   - Network error в fetch: toast «Не удалось загрузить. Повторить?».

4. **Toasts**:
   - Единая toast-система (Alpine.js store).
   - Success (green), error (red), warning (yellow), info (blue).
   - Autodismiss 5 сек, hover pause.
   - Max 3 visible одновременно.

### Definition of Done
- [ ] Empty states на всех list views
- [ ] Skeleton screens везде, где > 300ms loading
- [ ] Error pages стилизованы
- [ ] Toast система работает
- [ ] Тесты на все states

### Артефакты
- `backend/templates/partials/empty_state.html`
- `backend/templates/partials/skeleton/*.html`
- `backend/static/ui/toasts.js`
- `backend/templates/errors/*.html` (обновлённые)
- `docs/ui/STATES.md`

### Валидация
```bash
# Visual регрессия для каждого state
playwright test tests/visual/states/
```

### Откат
```bash
git revert
```

### Обновить в документации
- `docs/ui/STATES.md`

---

## Этап 9.5. Dark mode (опционально)

### Контекст
Не must-have для CRM (менеджеры работают днём). Но добавляет престиж.

### Цель
Dark mode toggle с корректным отображением всех экранов.

### Что делать
1. **Tailwind**:
   - `darkMode: 'class'` в config.
   - `<html class="dark">` toggle.

2. **Tokens**:
   - Dark variants для всех colors в `tokens.css`:
     ```css
     .dark {
       --color-bg: #18181b;
       --color-text: #f4f4f5;
       ...
     }
     ```

3. **Toggle**:
   - В user preferences + header switch.
   - Предпочтение сохраняется в DB + cookie.
   - `prefers-color-scheme: dark` — initial detection.

4. **Testing**:
   - Все screens в обеих темах.
   - Contrast WCAG AA.

### Definition of Done
- [ ] Dark mode toggle работает
- [ ] Все key screens корректны в dark
- [ ] Contrast passing WCAG AA
- [ ] User preference сохраняется

### Артефакты
- `backend/static/ui/design/tokens-dark.css`
- `backend/templates/partials/theme_toggle.html`
- `backend/ui/views/pages/profile/theme.py`

### Валидация
```bash
playwright test tests/visual/dark-mode/
```

### Откат
```bash
git revert
```

### Обновить в документации
- `docs/ui/DARK_MODE.md`

---

## Этап 9.6. Micro-interactions + animations

### Контекст
Плавность интерфейса делает CRM приятнее в использовании. Но нельзя переборщить.

### Цель
Продуманные micro-interactions в ключевых местах.

### Что делать
1. **Transitions**:
   - Hover на buttons (150ms).
   - Modal open/close (200ms).
   - Accordion expand (150ms).
   - Toast slide-in (200ms).

2. **Loading**:
   - Button loading: text fade + spinner.
   - Skeleton shimmer.

3. **Success feedback**:
   - Checkmark animation after save (200ms).
   - Deal stage change — smooth drag + drop.

4. **Smart defaults**:
   - Focus ring на focused input.
   - Enter submits form.
   - Esc closes modal.

5. **Respect prefers-reduced-motion**:
   - Все animations отключаются если `@media (prefers-reduced-motion: reduce)`.

### Definition of Done
- [ ] Transitions на кнопках и модалах
- [ ] Loading feedback в buttons
- [ ] Keyboard shortcuts работают
- [ ] Reduced motion соблюдается

### Артефакты
- `backend/static/ui/design/animations.css`
- `backend/static/ui/keyboard.js`

### Валидация
Manual review.

### Откат
```bash
git revert
```

### Обновить в документации
- `docs/ui/INTERACTIONS.md`

---

## Этап 9.7. Accessibility (WCAG 2.1 AA) — full pass

### Контекст
152-ФЗ формально не требует, но хорошая практика + показатель качества.

### Цель
WCAG 2.1 AA на ключевых экранах.

### Что делать
1. **Automated**:
   - axe-core во всех Playwright тестах.
   - `playwright-axe` plugin.
   - Zero violations на critical severity.

2. **Manual**:
   - Keyboard-only navigation (Tab, Shift+Tab, Enter, Esc).
   - Screen reader test (NVDA на Windows, VoiceOver на Mac).
   - Zoom 200% без потери функциональности.

3. **Fix categories**:
   - Missing alt на изображениях.
   - Missing labels на forms.
   - Low contrast.
   - Focus trap missing в modals.
   - Missing aria-live для dynamic content (toasts).
   - Missing landmarks (main, nav, header, footer).

4. **Documentation**:
   - VPAT (Voluntary Product Accessibility Template) — опционально.

### Инструменты
- `mcp__playwright__*` — axe-core integration

### Definition of Done
- [ ] axe-core zero critical violations
- [ ] Keyboard navigation works для всех key screens
- [ ] Screen reader compatible
- [ ] WCAG 2.1 AA passing на 10+ main screens

### Артефакты
- `tests/a11y/*.spec.ts`
- `docs/ui/ACCESSIBILITY.md`

### Валидация
```bash
playwright test tests/a11y/
```

### Откат
```bash
git revert
```

### Обновить в документации
- `docs/ui/ACCESSIBILITY.md`

---

## Этап 9.8. i18n / l10n подготовка

### Контекст
Сейчас только русский, строки захардкожены. На будущее — вынести в .po.

### Цель
Все user-facing strings в gettext. Русский остаётся дефолтом.

### Что делать
1. **Django i18n**:
   - `LANGUAGE_CODE = 'ru'`, `USE_I18N = True`.
   - `LOCALE_PATHS`.

2. **Strings extraction**:
   - Templates: `{% trans "..." %}` / `{% blocktrans %}`.
   - Python: `from django.utils.translation import gettext_lazy as _`.

3. **Messages**:
   - `python manage.py makemessages -l ru -l en`.
   - Перевод en — для задела на будущее (Kazakhstan ?).

4. **JS i18n**:
   - `django.catalog` endpoint для JS i18n.
   - `gettext()` в JS.

5. **Formatting**:
   - Datetime: `{% naturaltime %}` с локалью.
   - Numbers: `{{ value|floatformat:2 }}` с thousand separator.

6. **Pluralization**:
   - `{% blocktrans count n=n %}{{ n }} item{% plural %}{{ n }} items{% endblocktrans %}`.

### Definition of Done
- [ ] Все user-facing strings в gettext
- [ ] `locale/ru/LC_MESSAGES/django.po` заполнен
- [ ] `en` переведён на 50%+ (задел)
- [ ] JS i18n работает
- [ ] Нет hardcoded strings в templates

### Артефакты
- `locale/ru/`, `locale/en/`
- `backend/static/ui/i18n.js`
- `docs/i18n/README.md`

### Валидация
```bash
python manage.py makemessages -l ru
python manage.py makemessages -l en
# Check .po files completeness
```

### Откат
```bash
git revert
```

### Обновить в документации
- `docs/i18n/README.md`

---

## Checklist завершения волны 9

- [ ] Design system зафиксирована
- [ ] Icons унифицированы (Heroicons)
- [ ] Mobile responsive (360px–1920px)
- [ ] Empty / loading / error states
- [ ] Dark mode (если решили)
- [ ] Micro-interactions
- [ ] WCAG 2.1 AA passing
- [ ] i18n подготовлена

**Visual regression baseline** зафиксирован и используется в Wave 14 (QA).
