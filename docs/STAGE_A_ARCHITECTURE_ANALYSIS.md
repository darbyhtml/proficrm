# ЭТАП A: Анализ архитектуры проекта

**Дата:** 2024-01-XX  
**Статус:** ✅ Завершён

---

## 1. Текущая архитектура передачи компаний

### 1.1. Source of Truth

**Модель Company** (`backend/companies/models.py`):
- Поле `responsible` (ForeignKey на User) — **это source of truth для владения компанией**
- Поле `branch` (ForeignKey на Branch) — филиал компании
- При сохранении: если `branch` не задан, берётся из `responsible.branch`

### 1.2. Модель User и роли

**Модель User** (`backend/accounts/models.py`):
```python
class Role(models.TextChoices):
    MANAGER = "manager", "Менеджер"
    BRANCH_DIRECTOR = "branch_director", "Директор филиала"
    SALES_HEAD = "sales_head", "Руководитель отдела продаж"
    GROUP_MANAGER = "group_manager", "Управляющий группой компаний"
    ADMIN = "admin", "Администратор"
```

- Поле `branch` (ForeignKey на Branch) — филиал пользователя
- Поле `data_scope` — область доступа (GLOBAL, BRANCH, SELF)

### 1.3. Текущая логика прав доступа

**Файл:** `backend/companies/permissions.py`

**Функция `can_edit_company(user, company)`:**
- ✅ Админ/суперпользователь/GROUP_MANAGER: всегда `True`
- ✅ Менеджер: только если `company.responsible_id == user.id`
- ✅ РОП/Директор филиала: если `company.branch_id == user.branch_id` или `company.responsible.branch_id == user.branch_id`

**Функция `editable_company_qs(user)`:**
- Возвращает QuerySet компаний, которые пользователь может редактировать
- Используется для фильтрации списков

### 1.4. Endpoints передачи компаний

#### 1.4.1. Одиночная передача

**URL:** `POST /companies/<uuid:company_id>/transfer/`  
**View:** `company_transfer()` в `backend/ui/views.py:3007`

**Текущая логика:**
1. Проверка прав через `_can_edit_company(user, company)`
2. Валидация нового ответственного: только MANAGER, BRANCH_DIRECTOR, SALES_HEAD
3. Обновление: `company.responsible = new_resp`, `company.branch = new_resp.branch`
4. Логирование и уведомление

**Проблемы:**
- ❌ Нет проверки, что менеджер может передавать ТОЛЬКО свои компании
- ❌ Нет проверки, что РОП может передавать только компании своего филиала
- ❌ В списке получателей могут быть GROUP_MANAGER и ADMIN (не должно быть)

#### 1.4.2. Массовая передача

**URL:** `POST /companies/bulk-transfer/`  
**View:** `company_bulk_transfer()` в `backend/ui/views.py:1160`

**Текущая логика:**
1. Использует `_editable_company_qs(user)` для фильтрации
2. Режимы: `selected` (по выбранным ID) или `filtered` (по текущему фильтру)
3. Ограничение для директора филиала: только внутри своего филиала
4. Валидация нового ответственного: только MANAGER, BRANCH_DIRECTOR, SALES_HEAD

**Проблемы:**
- ❌ Нет проверки для менеджера: может ли он передавать выбранные компании
- ❌ Нет проверки для РОП: может ли он передавать компании своего филиала
- ❌ Нет группировки получателей по филиалам в UI
- ❌ Нет проверки, что все выбранные компании разрешены для передачи

### 1.5. UI для передачи

#### 1.5.1. Карточка компании

**Файл:** `backend/templates/ui/company_detail.html:602-614`

**Текущая реализация:**
- Форма с select для выбора нового ответственного
- Список `transfer_targets` передаётся в контекст
- Нет группировки по филиалам
- Нет исключения GROUP_MANAGER и ADMIN

#### 1.5.2. Список компаний

**Файл:** `backend/templates/ui/company_list.html:102-134`

**Текущая реализация:**
- Форма массовой передачи с select для выбора ответственного
- Список `transfer_targets` передаётся в контекст
- Нет проверки, что все выбранные компании разрешены
- Нет disabled состояния кнопки при неразрешённых компаниях
- Нет группировки по филиалам

**Где формируется `transfer_targets`:**
- Нужно найти в `company_list()` и `company_detail()` views

---

## 2. Текущая система аутентификации

### 2.1. Endpoints

**URL:** `POST /login/`  
**View:** `SecureLoginView` в `backend/accounts/views.py:28`

**Текущая реализация:**
- Наследуется от `django.contrib.auth.views.LoginView`
- Защита от брутфорса через rate limiting (IP и username)
- Использует стандартную Django сессионную аутентификацию
- Логирование успешных/неудачных попыток входа

**Шаблон:** `backend/templates/registration/login.html` (нужно проверить)

### 2.2. Модели и хранение

- Используется стандартная модель `User` (расширенная AbstractUser)
- Пароли хранятся через Django password hashers (PBKDF2)
- Сессии через Django sessions (database-backed)

### 2.3. Что нужно для magic link

**Требуется создать:**
1. Модель `MagicLinkToken` для одноразовых токенов
2. Endpoint генерации токена (только для админа)
3. Endpoint входа по токену `GET /auth/magic/<token>/`
4. UI в настройках пользователей для генерации ссылки
5. Отключение password login (опционально, через настройку)
6. Аннулирование паролей (set_unusable_password)

---

## 3. Android приложение (предварительный анализ)

**Требуется детальный анализ:**
- Polling механизм для получения команд на звонок
- Нормализация телефонов (`PhoneNumberNormalizer`)
- CallLog observer и retry схема
- Matching звонков по номеру и времени

**Файлы для изучения:**
- `android/CRMProfiDialer/app/src/main/java/ru/groupprofi/crmprofi/dialer/`
- Особенно: `CallListenerService`, `CallLogObserverManager`, `PhoneNumberNormalizer`

---

## 4. Критичные проблемы (предварительный список)

### 4.1. Безопасность

**Требуется проверить:**
- [ ] Утечки персональных данных в логах
- [ ] Permission checks на всех критичных endpoints
- [ ] SQL injection риски (ORM должен защищать, но проверить)
- [ ] CSRF защита (Django должен быть включён)

### 4.2. Производительность

**Требуется проверить:**
- [ ] N+1 queries в списках компаний
- [ ] Индексы БД на критичных полях
- [ ] Polling нагрузка от Android устройств

### 4.3. Зависимости

**Требуется проверить:**
- [ ] Устаревшие версии библиотек с уязвимостями
- [ ] Странные версии (например, нестандартные форки)

---

## 5. План работы

### ЭТАП B: Задача 1 — Передача компаний
1. Исправить логику прав в `company_transfer()` и `company_bulk_transfer()`
2. Добавить валидацию на backend
3. Обновить UI: группировка по филиалам, исключение GROUP_MANAGER/ADMIN
4. Добавить проверку в массовой передаче (disabled кнопка)
5. Написать тесты

### ЭТАП C: Задача 2 — Magic link auth
1. Создать модель `MagicLinkToken`
2. Создать endpoints (генерация, вход)
3. Обновить UI в настройках пользователей
4. Отключить password login (опционально)
5. Написать тесты

### ЭТАП D: Задачи 3 и 4
1. Анализ Android приложения
2. Рекомендации и минимальные правки
3. Поиск и исправление критичных проблем

---

## 6. Следующие шаги

**Начинаю с ЭТАПА B — Задача 1: Передача компаний**

**Файлы для изменения:**
- `backend/companies/permissions.py` — добавить функции проверки прав на передачу
- `backend/ui/views.py` — исправить `company_transfer()` и `company_bulk_transfer()`
- `backend/templates/ui/company_list.html` — обновить UI массовой передачи
- `backend/templates/ui/company_detail.html` — обновить UI одиночной передачи
- `backend/companies/tests.py` — добавить тесты (или создать новый файл)

**Документация:**
- `docs/COMPANY_TRANSFER_RULES.md` — правила передачи, source-of-truth, endpoints, UX
