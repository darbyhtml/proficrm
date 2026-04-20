# Инвентаризация моделей CRM ПРОФИ
_Снапшот: 2026-04-20. Wave 0.1._

> Исчерпывающая карта всех Django-моделей проекта. Источники: `backend/*/models.py`, `backend/accounts/models_region.py`.
> Тесты определены как упоминания имени модели в файлах `tests*.py`/`test_*.py` (grep по идентификатору).
> Сервисы — функции/классы в `backend/{app}/services/` или `backend/{app}/services.py`.

---

## Сводка

| Показатель | Значение |
|------------|----------|
| Django-приложений с моделями | **10** (`accounts`, `audit`, `companies`, `mailer`, `messenger`, `notifications`, `phonebridge`, `policy`, `tasksapp`, `ui`) |
| Приложений без моделей | **1** (`amocrm` — только клиент/импортер, данные живут в `companies`/`ui.AmoApiConfig`) |
| Всего моделей | **65** |
| Моделей, использующих `UUIDField` как PK | 11 |
| Моделей-singleton (через `load()`) | 5 (`PolicyConfig`, `UiGlobalConfig`, `AmoApiConfig`, `GlobalMailAccount`, `SmtpBzQuota`) |
| God-узлы (FK-центры) | `User` (используется 40+ моделями через `settings.AUTH_USER_MODEL`), `Company` (11 входящих FK), `Contact`, `Conversation`, `Branch` |

### Распределение моделей по приложениям

| App | Моделей | Ключевые |
|-----|---------|----------|
| `companies` | 15 | Company, Contact, CompanyPhone, CompanyEmail, ContactPhone, ContactEmail, CompanyNote, CompanyDeal, CompanyHistoryEvent, CompanySearchIndex |
| `messenger` | 17 | Inbox, Conversation, Message, Contact (messenger), ContactInbox, RoutingRule, Campaign (messenger), AutomationRule |
| `mailer` | 10 | Campaign, CampaignRecipient, SendLog, MailAccount, GlobalMailAccount, SmtpBzQuota, CampaignQueue |
| `phonebridge` | 6 | PhoneDevice, CallRequest, PhoneTelemetry, MobileAppBuild, MobileAppQrToken |
| `accounts` | 4 | User, Branch, BranchRegion, MagicLinkToken, UserAbsence |
| `tasksapp` | 4 | Task, TaskType, TaskComment, TaskEvent |
| `notifications` | 4 | Notification, CompanyContractReminder, CrmAnnouncement, CrmAnnouncementRead |
| `ui` | 3 | UiGlobalConfig, UiUserPreference, AmoApiConfig |
| `audit` | 2 | ActivityEvent, ErrorLog |
| `policy` | 2 | PolicyConfig, PolicyRule |

---

## app: accounts

### Branch
**Описание:** Подразделение (филиал) компании ПРОФИ. Используется для сегрегации данных между отделами (ЕКБ/Тюмень/Краснодар). К нему привязываются пользователи, компании, inbox'ы, диалоги мессенджера.
**Verbose name:** нет (!)
**Поля:**
| Имя | Тип | Null | Unique | Default |
|-----|-----|------|--------|---------|
| code | SlugField(50) | - | да | - |
| name | CharField(120) | - | да | - |
| is_active | BooleanField | - | - | True (db_index) |

**FK:** нет
**M2M:** нет
**Meta:** нет (!)
**Бизнес-методы:** `delete()` переопределён — блокирует удаление если есть активные юзеры или компании.
**Сервисы:** нет специального services.py.
**Тесты:** `accounts/tests_branch_region.py` (12 упом.), `accounts/tests.py`, `companies/tests_transfer.py` (23 упом.).

---

### User (god-node)
**Описание:** Пользователь системы = менеджер/РОП/директор подразделения/админ/тендерист. Наследует `AbstractUser`. Ссылается почти на всё в проекте через `settings.AUTH_USER_MODEL`.
**Verbose name:** нет (наследуется)
**Поля:**
| Имя | Тип | Null | Unique | Default |
|-----|-----|------|--------|---------|
| role | CharField(32, choices=Role) | - | - | `MANAGER` |
| messenger_online | BooleanField | - | - | False (db_index) |
| messenger_last_seen | DateTimeField | да | - | - |
| branch | FK(Branch) | да | - | SET_NULL |
| data_scope | CharField(16, choices=DataScope) | - | - | `GLOBAL` |
| email_signature_html | TextField | - | - | "" |
| avatar | ImageField | да | - | - |

**TextChoices:**
- `Role` = MANAGER / BRANCH_DIRECTOR / SALES_HEAD (РОП) / GROUP_MANAGER / TENDERIST / ADMIN
- `DataScope` = GLOBAL / BRANCH / SELF

**FK:** branch → accounts.Branch (SET_NULL, related_name='users')
**M2M:** наследует groups/user_permissions из AbstractUser.
**Meta:** унаследовано.
**Свойства / методы:**
- `@property is_tenderist` — True для TENDERIST роли
- `@property is_admin_role` — True для `is_superuser or role==ADMIN`
- `is_currently_absent(on_date=None)` — проверка активного UserAbsence (используется в `messenger.services.auto_assign_conversation`)

**Сервисы:** `accounts/security.py` (get_client_ip, lockout), `accounts/scope.py`, `accounts/permissions.py`.
**Тесты:** `accounts/tests.py`, `tests_magic_link.py`, `tests_tenderist.py` (16 упом.), `tests_signals.py`, `tests_templatetags.py`, а также использование почти во всех test-файлах других модулей.

---

### MagicLinkToken
**Описание:** Одноразовый токен входа без пароля. Генерируется администратором для конкретного пользователя. Хранится только SHA-256 хэш, plain token показывается один раз.
**Verbose name:** «Токен входа»
**Поля:**
| Имя | Тип | Null | Unique | Default |
|-----|-----|------|--------|---------|
| user | FK(User) CASCADE | - | - | - |
| token_hash | CharField(64) | - | да | db_index |
| created_at | DateTimeField auto_now_add | - | - | db_index |
| expires_at | DateTimeField | - | - | db_index |
| used_at | DateTimeField | да | - | db_index |
| created_by | FK(User) SET_NULL | да | - | - |
| ip_address | GenericIPAddressField | да | - | - |
| user_agent | CharField(255) | - | - | "" |

**Meta:** indexes=[`user`,`expires_at`,`used_at`]
**Бизнес-методы:** `is_valid()`, `mark_as_used(ip, ua)`, `@staticmethod generate_token()`, `@staticmethod create_for_user(user, created_by, ttl_minutes=1440)`.
**Тесты:** `accounts/tests_magic_link.py` (22 упом., 2 TestCase).

---

### UserAbsence
**Описание:** Отсутствие сотрудника (отпуск/больничный/отгул). F5 — используется в `messenger.services.auto_assign_conversation` чтобы не назначать диалоги отсутствующим.
**Verbose name:** «Отсутствие сотрудника»
**TextChoices (Type):** VACATION / SICK / DAYOFF / OTHER
**Поля:**
| Имя | Тип | Null | Unique | Default |
|-----|-----|------|--------|---------|
| user | FK(User) CASCADE | - | - | - |
| start_date | DateField | - | - | - |
| end_date | DateField | - | - | - |
| type | CharField(16, choices) | - | - | VACATION |
| note | CharField(255) | - | - | "" |
| created_at | DateTimeField auto_now_add | - | - | - |
| created_by | FK(User) SET_NULL | да | - | - |

**Meta:**
- ordering=['-start_date']
- indexes=[user+end_date `user_absence_user_end_idx`]
- constraints=[CheckConstraint end_date>=start_date `user_absence_end_after_start`]
**Бизнес-методы:** `is_active_on(date)`.
**Тесты:** упомянут косвенно через auto_assign (`messenger/tests/test_auto_assign.py`). Прямого tests_absence.py **нет** — красный флаг.

---

### BranchRegion (отдельный файл `models_region.py`)
**Описание:** Справочник регионов РФ, закреплённых за подразделениями (ЕКБ/Тюмень/Краснодар + общий пул). Источник — Положение о распределении входящих запросов.
**Verbose name:** «Регион подразделения»
**Поля:**
| Имя | Тип | Null | Unique | Default |
|-----|-----|------|--------|---------|
| branch | FK(Branch) CASCADE | - | - | - |
| region_name | CharField(128) | - | - | db_index |
| is_common_pool | BooleanField | - | - | False |
| ordering | PositiveSmallIntegerField | - | - | 0 |

**Meta:**
- unique_together = [branch+region_name]
- indexes = [region_name+branch, is_common_pool]
- ordering = ['branch', 'ordering']
**Тесты:** `accounts/tests_branch_region.py`.

---

## app: audit

### ActivityEvent
**Описание:** Универсальный журнал действий (create/update/delete/status/comment). 9.5M записей на проде по состоянию на 2026-04-19. Retention: 180 дней.
**Verbose name:** нет (!)
**TextChoices (Verb):** CREATE / UPDATE / DELETE / STATUS / COMMENT
**PK:** UUIDField
**Поля:**
| Имя | Тип | Null | Unique | Default |
|-----|-----|------|--------|---------|
| actor | FK(User) SET_NULL | да | - | - |
| verb | CharField(16, choices) | - | - | - |
| entity_type | CharField(32) | - | - | db_index |
| entity_id | CharField(64) | - | - | db_index |
| company_id | UUIDField | да | - | db_index |
| message | CharField(255) | - | - | "" |
| meta | JSONField | - | - | {} |
| created_at | DateTimeField auto_now_add | - | - | db_index |

**Meta:** ordering=['-created_at']. **Нет `verbose_name`.**
**Сервисы:** `audit/service.py::log_event()`. Retention: `audit/tasks.py` (Celery).
**Тесты:** `audit/tests_retention.py` (33 упом.).

---

### ErrorLog
**Описание:** Лог серверных ошибок. Аналог error_log из MODX. Retention: 90 дней. Интегрирован с `/process-logs` командой.
**Verbose name:** «Ошибка»
**TextChoices (Level):** ERROR / WARNING / CRITICAL / EXCEPTION
**PK:** UUIDField
**Поля (выборка):**
| Имя | Тип | Null | Default |
|-----|-----|------|---------|
| level | CharField(16) | - | ERROR (db_index) |
| message | TextField | - | "" |
| exception_type | CharField(255) | - | "" (db_index) |
| traceback | TextField | - | "" |
| path | CharField(500) | - | "" (db_index) |
| method | CharField(10) | - | "" (db_index) |
| user | FK(User) SET_NULL | да | - |
| user_agent | CharField(500) | - | "" |
| ip_address | GenericIPAddressField | да | - |
| request_data | JSONField | - | {} |
| resolved | BooleanField | - | False (db_index) |
| resolved_at | DateTimeField | да | - |
| resolved_by | FK(User) SET_NULL | да | - |
| notes | TextField | - | "" |
| created_at | DateTimeField auto_now_add | - | db_index |

**Meta:** ordering=['-created_at'], indexes=[created_at+resolved, level+resolved, path+resolved]
**Бизнес-методы:** `@classmethod log_error(exception, request, level, **kwargs)` — основной вход для логирования. Использует `accounts.security.get_client_ip` (SSOT PROXY_IPS). Скрывает password/token/secret/key/csrf.
**Тесты:** `audit/tests_retention.py`.

---

## app: companies (ядро CRM, god-node)

### CompanyStatus
**Описание:** Справочник статусов компании (например: Новый / В работе / Клиент / Отказ).
**Verbose name:** нет (!)
**Поля:** `name` CharField(120) unique.
**Meta:** нет (!)
**Тесты:** упоминается в `companies/tests.py`, `tests_api.py`, `tests_services.py`.

---

### CompanySphere
**Описание:** Сферы деятельности компаний. M2M связь с Company.
**Verbose name:** нет (!)
**Поля:** `name` CharField(120) unique, `is_important` BooleanField (оранжевый ? в UI).
**Meta:** нет (!)
**Тесты:** упоминается в companies tests.

---

### Region
**Описание:** Справочник регионов РФ (используется и в Company, и в messenger.Contact).
**Verbose name:** «Регион»
**Поля:** `name` CharField(120) unique.
**Тесты:** косвенно, через Company/Contact.

---

### ContractType
**Описание:** Виды договоров с настраиваемыми порогами предупреждений. Годовые (is_annual) имеют пороги по сумме, стандартные — по сроку окончания.
**Verbose name:** «Вид договора»
**Поля:**
| Имя | Тип | Null | Default |
|-----|-----|------|---------|
| name | CharField(120) | - | unique |
| is_annual | BooleanField | - | False |
| warning_days | PositiveIntegerField | - | 14 |
| danger_days | PositiveIntegerField | - | 7 |
| amount_danger_threshold | DecimalField(12,2) | да | 25000 |
| amount_warn_threshold | DecimalField(12,2) | да | 70000 |
| order | IntegerField | - | 0 (db_index) |

**Meta:** ordering=['order','name']
**Сервисы:** `companies/services/company_core.py::get_contract_alert(company)`, `_get_annual_contract_alert(amount, contract_type)`.
**Тесты:** `companies/tests_services.py::GetContractAlertTest`.

---

### Company (god-node — 11+ входящих FK)
**Описание:** Центральная сущность CRM. Организация-клиент с реквизитами, контактами, телефонами, сделками, историей, поиском. PK=UUID.
**Verbose name:** нет (!)
**PK:** UUIDField
**Поля (сокращённо, полей ~40):**
| Имя | Тип | Null | Index | Default |
|-----|-----|------|-------|---------|
| created_by | FK(User) SET_NULL | да | - | - |
| name | CharField(255) | - | db_index | - |
| legal_name | CharField(255) | - | - | "" |
| inn | CharField(255) | - | db_index | "" (!) |
| kpp | CharField(20) | - | - | "" |
| address | CharField(500) | - | - | "" |
| website | CharField(255) | - | - | "" |
| activity_kind | CharField(255) | - | db_index | "" |
| employees_count | PositiveIntegerField | да | - | - |
| workday_start | TimeField | да | - | - |
| workday_end | TimeField | да | - | - |
| work_timezone | CharField(64) | - | - | "" |
| work_schedule | TextField | - | - | "" (нормализуется) |
| is_cold_call | BooleanField | - | db_index | False (устар.) |
| primary_contact_is_cold_call | BooleanField | - | db_index | False |
| primary_cold_marked_at | DateTimeField | да | db_index | - |
| primary_cold_marked_by | FK(User) SET_NULL | да | - | - |
| primary_cold_marked_call | FK(phonebridge.CallRequest) SET_NULL | да | - | - |
| contract_type | FK(ContractType) SET_NULL | да | db_index | - |
| contract_until | DateField | да | db_index | - |
| contract_amount | DecimalField(12,2) | да | - | - |
| head_company | FK('self') SET_NULL | да | - | - |
| phone | CharField(50) | - | db_index | "" (нормализуется) |
| phone_comment | CharField(255) | - | - | "" |
| email | EmailField(254) | - | db_index | "" |
| contact_name | CharField(255) | - | - | "" (устар., есть Contact) |
| contact_position | CharField(255) | - | - | "" |
| status | FK(CompanyStatus) SET_NULL | да | - | - |
| region | FK(Region) SET_NULL | да | db_index | - |
| responsible | FK(User) SET_NULL | да | - | - |
| branch | FK(Branch) SET_NULL | да | - | автосинхрон из responsible |
| amocrm_company_id | BigIntegerField | да | db_index | - |
| raw_fields | JSONField | - | - | {} |
| created_at / updated_at | DateTimeField auto_now* | - | - | - |

**M2M:** `spheres` → CompanySphere (related_name='companies')

**Meta.indexes:** 10+ — базовые (inn, name, responsible+updated_at, responsible+contract_until) + **7 GIN trigram** индексов (name, legal_name, address, inn, kpp, phone, email) для быстрого ILIKE. `cmp_name_trgm_gin_idx` и др.

**Бизнес-методы:**
- `clean()` — защита от циклических head_company
- `save()` — автосинхрон branch из responsible.branch + normalize_phone + normalize_inn + normalize_work_schedule + обрезка всех длинных строк (inn 255, name 255, address 500, website 255, ...). **Последняя линия обороны.**

**Сервисы:**
- `companies/services/company_core.py::CompanyService`, `ColdCallService`, `get_contract_alert`, `get_worktime_status`, `get_org_root/companies`, `resolve_target_companies`, `get_dashboard_contracts`
- `companies/services/company_delete.py::execute_company_deletion` (+ error)
- `companies/services/company_phones.py`, `company_emails.py` — валидация
- `companies/services/timeline.py::build_company_timeline`
- `companies/normalizers.py` — normalize_phone, normalize_inn, normalize_work_schedule
- `companies/importer.py` — импорт из amoCRM
- `companies/search_service.py`, `search_index.py`, `search_backends/` — FTS поиск

**Тесты:** `companies/tests.py` (53 упом., 10 TestCase), `tests_api.py` (19), `tests_services.py` (32), `tests_search.py` (98), `tests_transfer.py` (23), `tests_delete_service.py` (27), `tests_phone_email_services.py` (10). Плюс почти все UI тесты.

---

### CompanyNote
**Описание:** Заметки / записи истории по компании (звонки, письма, SMS, pinned-заметки). Основной timeline-источник.
**Verbose name:** нет (!)
**TextChoices (NoteType):** note / email_in / email_out / call_in / call_out / sms
**Поля:**
| Имя | Тип | Null | Default |
|-----|-----|------|---------|
| company | FK(Company) CASCADE | - | - |
| author | FK(User) SET_NULL | да | - |
| text | TextField | - | - |
| note_type | CharField(16) | - | NOTE (db_index) |
| meta | JSONField | - | {} |
| attachment | FileField `company_notes/%Y/%m/%d/` | да | - |
| attachment_name | CharField(255) | - | "" |
| attachment_ext | CharField(16) | - | "" (db_index) |
| attachment_size | BigIntegerField | - | 0 |
| attachment_content_type | CharField(120) | - | "" |
| is_pinned | BooleanField | - | False (db_index) |
| pinned_at | DateTimeField | да | - |
| pinned_by | FK(User) SET_NULL | да | - |
| created_at | DateTimeField auto_now_add | - | - |
| edited_at | DateTimeField | да | db_index |
| external_source | CharField(32) | - | "" (db_index) |
| external_uid | CharField(120) | - | "" (db_index) |

**Meta:** нет (!) — ordering/indexes не определены.
**Бизнес-методы:** `save()` — снэпшот метаданных файла (name/ext/size) из attachment.
**Тесты:** упоминается в companies tests, messenger autolink.

---

### CompanyNoteAttachment
**Описание:** Дополнительные вложения к заметке (несколько файлов через ForeignKey).
**Verbose name:** «Вложение заметки»
**Поля:** note (FK CASCADE), file (FileField), file_name, file_ext (db_index), file_size, content_type, order (db_index).
**Meta:** ordering=['order','id']
**Бизнес-методы:** `save()` — snapshot метаданных файла.
**Тесты:** в companies tests (косвенно).

---

### CompanyDeal
**Описание:** Минимальная сделка по компании (программа/стоимость/кол-во слушателей). Простой учёт для менеджеров, чтобы не лезть в 1С.
**Verbose name:** нет (!)
**Поля:** company (FK CASCADE), created_by (FK SET_NULL), program TextField, price_per_person Decimal(12,2), listeners_count PositiveInt, created_at (db_index).
**Meta:** ordering=['-created_at'], indexes=[company+created_at]
**@property total_amount:** `price_per_person * listeners_count` или None.
**Тесты:** косвенно через companies tests.

---

### Contact
**Описание:** Физлицо-контакт, связанный с Company (или независимый). При удалении компании НЕ удаляется (SET_NULL). PK=UUID.
**Verbose name:** нет (!)
**PK:** UUIDField
**Поля:**
| Имя | Тип | Null | Default |
|-----|-----|------|---------|
| company | FK(Company) SET_NULL | да | - |
| first_name, last_name | CharField(120) | - | "" |
| position | CharField(255) | - | "" |
| status | CharField(120) | - | "" |
| note | TextField | - | "" |
| is_cold_call | BooleanField | - | False (db_index) |
| cold_marked_at | DateTimeField | да | db_index |
| cold_marked_by | FK(User) SET_NULL | да | - |
| cold_marked_call | FK(phonebridge.CallRequest) SET_NULL | да | - |
| amocrm_contact_id | BigIntegerField | да | db_index |
| raw_fields | JSONField | - | {} |
| created_at/updated_at | - | - | - |

**Meta.indexes:** GIN trigram (first_name, last_name).
**Тесты:** tests_api.py::ContactViewSetTest, tests_services.py::ColdCallServiceContactTest.

---

### CompanyEmail
**Описание:** Дополнительные email'ы компании (помимо Company.email).
**Verbose name:** нет (!)
**Поля:** company (FK CASCADE), value (EmailField 254, db_index), order (db_index).
**Meta:** indexes=[value, company+order, GIN trigram на value]; ordering=['order','value'].
**Тесты:** tests_phone_email_services.py.

---

### CompanyPhone
**Описание:** Дополнительные телефоны компании с флагом холодного звонка.
**Verbose name:** нет (!)
**Поля:** company (FK CASCADE), value CharField(50) db_index (нормализуется!), order (db_index), comment (255), is_cold_call (db_index), cold_marked_at (db_index), cold_marked_by (FK User SET_NULL), cold_marked_call (FK CallRequest SET_NULL).
**Meta:** indexes=[value, company+order, GIN trigram value]; ordering=['order','value'].
**Бизнес-методы:** `save()` — normalize_phone (НО ОБХОДИТСЯ через .update()/bulk_update!).
**Тесты:** tests_phone_email_services.py, tests_services.py::ColdCallServiceCompanyPhoneTest, companies/tests.py::CompanyPhoneNormalizationTestCase.

---

### ContactEmail
**Описание:** Email'ы контактов с типом (work/personal/other).
**Verbose name:** нет (!)
**TextChoices (EmailType):** WORK / PERSONAL / OTHER
**Поля:** contact (FK CASCADE), type CharField(16), value EmailField(254) db_index.
**Meta:** indexes=[value, GIN trigram].
**Тесты:** tests_phone_email_services.py.

---

### ContactPhone
**Описание:** Телефоны контактов с типом и флагом холодного звонка. PK=BigAutoField.
**Verbose name:** нет (!)
**TextChoices (PhoneType):** WORK / WORK_DIRECT / MOBILE / OTHER / HOME / FAX
**Поля:** contact (FK CASCADE), type CharField(24), value CharField(50) db_index, comment (255), is_cold_call (db_index), cold_marked_at (db_index), cold_marked_by (FK User), cold_marked_call (FK CallRequest).
**Meta:** indexes=[value, GIN trigram].
**Бизнес-методы:** `save()` — normalize_phone (обходит update()/bulk_update).
**Тесты:** tests_services.py::ColdCallServiceContactPhoneTest, companies/tests.py::ContactPhoneNormalizationTestCase.

---

### CompanyDeletionRequest
**Описание:** Заявка на удаление компании (сохраняет snapshot ID+имя). После одобрения админом компания физически удаляется. Снимки для истории.
**Verbose name:** нет (!)
**TextChoices (Status):** PENDING / CANCELLED / APPROVED
**Поля:** company (FK SET_NULL), company_id_snapshot (UUIDField db_index), company_name_snapshot (CharField 255), requested_by (FK User SET_NULL), requested_by_branch (FK Branch SET_NULL), note TextField, status (db_index), decided_by (FK User SET_NULL), decision_note TextField, decided_at, created_at (db_index).
**Meta:** indexes=[company_id_snapshot+status, status+created_at]
**Тесты:** companies/tests.py::CompanyDeletionRequestSignalTest, tasksapp/tests.py::CompanyDeletionRequestNotificationTestCase.

---

### CompanySearchIndex
**Описание:** Денормализованный FTS-индекс для поиска по карточке компании. Primary key = Company (OneToOne). tsvector'ы заполняются триггером БД.
**Verbose name:** нет (!)
**PK:** company OneToOneField(Company) CASCADE primary_key=True
**Поля:**
- Текстовые группы: `t_ident`, `t_name`, `t_contacts`, `t_other`
- `plain_text`, `digits` (TextField) — trigram fallback
- `normalized_phones` ArrayField(TextField) — E.164 номера
- `normalized_emails` ArrayField(TextField) — lowercased
- `normalized_inns` ArrayField(TextField) — 8–12 цифр
- `vector_a/b/c/d` SearchVectorField (tsvector, заполняется триггером)
- `updated_at`

**Meta.indexes:** 4 GIN на tsvector (a/b/c/d) + 2 trigram (plain_text, digits) + 3 GIN на ArrayField (normalized_phones/emails/inns). Всего **9 GIN индексов**.
**Сервисы:** `companies/search_index.py`, `companies/search_service.py`, `companies/search_backends/`.
**Тесты:** `companies/tests_search.py` (98 упом., 4 TestCase).

---

### CompanyHistoryEvent
**Описание:** История передач карточки (created/assigned). Источник: local или amocrm. Текстовые *_name поля заполняются всегда — если пользователь удалён или из amoCRM.
**Verbose name:** «История компании»
**TextChoices:**
- `EventType`: CREATED / ASSIGNED
- `Source`: LOCAL / AMOCRM
**Поля:** company (FK CASCADE), event_type (db_index), actor_name (255), actor (FK User SET_NULL), from_user_name/from_user, to_user_name/to_user, occurred_at (db_index), source (db_index, default LOCAL), external_id (db_index — синтетический `created_{amo_id}` для дедупа), created_at.
**Meta:** ordering=['-occurred_at'], indexes=[company+occurred_at]
**Тесты:** косвенно через companies/tests.py и amocrm migration.

---

## app: mailer

### MailAccount
**Описание:** Персональный SMTP-аккаунт пользователя (Яндекс/custom). Пароль — Fernet (`MAILER_FERNET_KEY`).
**Поля:** user (OneToOneField CASCADE), smtp_host (255, default smtp.yandex.ru), smtp_port (587), use_starttls (True), smtp_username, smtp_password_enc (TextField), from_email, from_name, reply_to, is_enabled, rate_per_minute (20), rate_per_day (500), updated_at.
**Meta:** нет (!)
**Бизнес-методы:** `set_password(str)` / `get_password() → str` (Fernet).
**Тесты:** mailer/tests.py::MailerBaseTestCase, ui/tests_settings_mail.py.

---

### GlobalMailAccount (singleton via load())
**Описание:** Глобальные SMTP-настройки (smtp.bz). Одна запись на CRM. Редактируется админом.
**Поля:** smtp_host (connect.smtp.bz), smtp_port (587), use_starttls, smtp_username, smtp_password_enc, from_email (default no-reply@groupprofi.ru), from_name (CRM ПРОФИ), is_enabled, rate_per_minute (1), rate_per_day (15000), per_user_daily_limit (100), smtp_bz_api_key_enc (Fernet, для API квоты), updated_at.
**Meta:** нет (!)
**Бизнес-методы:** `set_password()/get_password()`, `set_api_key()/get_api_key()`, `@property smtp_bz_api_key`, `@classmethod load()`.
**Тесты:** mailer/tests.py, ui/tests_settings_mail.py, tests_views.py::MailSettingsViewTest.

---

### Unsubscribe
**Описание:** Глобальный стоп-лист email (unique email). source=manual/token/smtp_bz.
**Поля:** email (unique), source (24), reason (24), last_seen_at, created_at.
**Meta:** нет (!)
**Тесты:** mailer/tests.py::MailerSafetyAndUnsubTests, tests_views.py::UnsubscribeViewTest.

---

### UnsubscribeToken
**Описание:** Токенизированная отписка (безопаснее /unsubscribe/<email>/).
**Поля:** token (64, unique, db_index), email (db_index), created_at.
**Тесты:** mailer/tests_views.py::UnsubscribeViewTest.

---

### Campaign (mailer)
**Описание:** Email-рассылка с телом, темой, статусом, расписанием. PK=UUID. ⚠️ Коллизия имени — есть и `messenger.Campaign` (проактивные сообщения виджета).
**TextChoices (Status):** DRAFT / READY / SENDING / PAUSED / SENT / STOPPED
**Поля:** created_by (FK User SET_NULL), name (200), subject (200), body_text, body_html, sender_name (120), attachment (FileField `campaign_attachments/%Y/%m/`), attachment_original_name (255), filter_meta JSONField, status, send_at DateTimeField, is_template (db_index), created_at/updated_at.
**Meta:** нет (!)
**Тесты:** mailer/tests.py (20+ TestCase), tests_views.py (12+ TestCase).

---

### CampaignRecipient
**Описание:** Получатель рассылки. PK=UUID.
**TextChoices (Status):** PENDING / SENT / FAILED / UNSUBSCRIBED
**Поля:** campaign (FK CASCADE), email, contact (FK companies.Contact SET_NULL, related_name='+'), company (FK Company SET_NULL, related_name='+'), status, last_error (2000), created_at/updated_at.
**Meta:** unique_together=(campaign, email), indexes=[campaign+status, email].
**Тесты:** mailer/tests.py.

---

### SendLog
**Описание:** Лог отправок. Используется для idempotency (не слать письмо дважды) и статистики (sent_today, sent_last_hour).
**TextChoices (Status):** SENT / FAILED
**PK:** UUIDField
**Поля:** campaign (FK CASCADE), recipient (FK CampaignRecipient SET_NULL), account (FK MailAccount SET_NULL), provider (50, smtp), message_id (255), status, error TextField, created_at.
**Meta:**
- indexes=[provider+status+created_at, campaign+recipient+status]
- constraints=[UniqueConstraint(campaign, recipient, condition=status='sent') `mailer_sendlog_unique_sent_per_recipient`]
**Тесты:** mailer/tests.py::MailerSendLogIdempotencyTests.

---

### EmailCooldown
**Описание:** Cooldown на повторное использование email после «очистки» кампании.
**Поля:** email (db_index), created_by (FK User SET_NULL), until_at (db_index), created_at.
**Meta:** unique_together=(email, created_by), indexes=[created_by+until_at].
**Тесты:** упомянут в mailer/tests.py.

---

### SmtpBzQuota (singleton via load())
**Описание:** Информация о тарифе и квоте smtp.bz (через API). Обновляется Celery.
**PK:** IntegerField default=1 (singleton).
**Поля:** tariff_name (50), tariff_renewal_date, emails_available, emails_limit, sent_per_hour, max_per_hour (100), last_synced_at, sync_error TextField, created_at/updated_at.
**Meta:** verbose_name=«Квота smtp.bz»
**Бизнес-методы:** `@classmethod load()`.
**Тесты:** упомянут в mailer/tests.py, tests_views.py.

---

### CampaignQueue
**Описание:** Очередь рассылок (последовательное выполнение). OneToOne с Campaign. Circuit breaker через consecutive_transient_errors.
**TextChoices (Status):** PENDING / PROCESSING / COMPLETED / CANCELLED
**PK:** UUIDField
**Поля:** campaign (OneToOne CASCADE), status, priority (Integer default 0), queued_at (auto_now_add), started_at, completed_at, deferred_until, defer_reason (24), consecutive_transient_errors (Integer).
**Meta:**
- ordering=['-priority','queued_at']
- indexes=[status+priority+queued_at, status+deferred_until+priority+queued_at]
**Сервисы:** `mailer/services/queue.py::defer_queue()`.
**Тесты:** mailer/tests.py::MailerQueueConsistencyTests (169), MailerDeferDailyLimitTests, MailerDeferQueueServiceTests, MailerRaceConditionTests, MailerEnterpriseFinishingTests, MailerExponentialBackoffTests.

---

### UserDailyLimitStatus
**Описание:** Отслеживание достижения дневного лимита юзером (для уведомлений).
**Поля:** user (OneToOne CASCADE), last_limit_reached_date, last_notified_date, updated_at.
**Meta:** indexes=[user+last_limit_reached_date]; verbose_name=«Статус дневного лимита пользователя»
**Тесты:** mailer/tests.py::MailerDeferDailyLimitTests.

---

## app: messenger (Chatwoot-клон)

> ⚠️ В этом app есть `messenger.Contact` и `messenger.Campaign` — НЕ путать с `companies.Contact` и `mailer.Campaign`.

### Inbox
**Описание:** Входящий ящик — точка входа виджета/канала (сайт/Telegram/VK). widget_token генерируется автоматически. Может быть глобальным (branch=null) или филиальным.
**Verbose name:** «Inbox»
**Поля:** name (255), branch (FK Branch CASCADE, null для global), is_active (db_index), widget_token (64, unique, auto-generated), settings JSONField, created_at (db_index).
**Бизнес-методы:** `clean()` — запрет на изменение branch после создания. `save()` — auto-генерация widget_token + full_clean().
**Тесты:** messenger/tests/test_widget_api.py, test_settings_ui.py, tests_tenderist.py (10).

---

### Channel
**Описание:** Канал под Inbox (сайт/Telegram/WhatsApp/VK/Email). config JSON.
**Verbose name:** «Канал»
**TextChoices (Type):** WEBSITE / TELEGRAM / WHATSAPP / VK / EMAIL
**Поля:** type, inbox (FK CASCADE), config JSONField, is_active (db_index).
**Тесты:** TODO: нет явных тестов — **красный флаг**.

---

### Contact (messenger)
**Описание:** Контакт посетителя/клиента в мессенджере. Может быть не привязан к Company. PK=UUID.
**Verbose name:** «Контакт»
**Поля:** external_id (255, visitor_id/Telegram user_id), name (255), email (254), phone (50), region_detected (FK companies.Region SET_NULL), created_at (db_index), last_activity_at (db_index), blocked (db_index).
**Meta:** indexes=[external_id, email, phone, last_activity_at, blocked]
**Бизнес-методы:** `clean()` — валидация email/phone (E.164-ish regex `\+?\d{7,15}`).
**Сервисы:** `messenger/services.py::create_or_get_contact`, `_normalize_contact_email`, `_normalize_contact_phone`.
**Тесты:** test_widget_api.py, test_company_autolink.py (21).

---

### Conversation (god-node для messenger)
**Описание:** Диалог клиента с оператором. Центральная модель мессенджера. Статусы open/pending/waiting_offline/resolved/closed. Имеет UI-статус (computed), эскалацию, оценку, резолюцию. Обязательно привязан к Branch (защита PROTECT).
**Verbose name:** «Диалог»
**TextChoices:**
- `Status`: OPEN / PENDING / WAITING_OFFLINE / RESOLVED / CLOSED
- `OffHoursChannel`: CALL / MESSENGER / EMAIL / OTHER
- `Priority` (IntegerChoices): LOW=10 / NORMAL=20 / HIGH=30
- `UiStatus`: NEW / WAITING / IN_PROGRESS / CLOSED (computed)
- `RegionSource`: GEOIP / FORM / COMPANY / UNKNOWN
**Поля (50+):**
| Поле | Тип | Примечание |
|------|-----|-----------|
| inbox | FK(Inbox) CASCADE | - |
| contact | FK(Contact) CASCADE | - |
| status | CharField(16) | db_index, default OPEN |
| priority | IntegerField | db_index, default NORMAL |
| assignee | FK(User) SET_NULL | назначенный оператор |
| assignee_assigned_at / assignee_opened_at / assignee_last_read_at | DateTimeField | эскалация по таймауту |
| needs_help | BooleanField | db_index |
| needs_help_at | DateTimeField | - |
| resolution | JSONField | {outcome, comment, resolved_at} |
| escalation_level | PositiveSmallIntegerField | db_index, 0–4 |
| last_escalated_at | DateTimeField | - |
| branch | FK(Branch) PROTECT editable=False | ИЗ inbox.branch, меняется маршрутизацией |
| company | FK(companies.Company) SET_NULL | автосвязь по email/phone |
| client_region | CharField(128) | db_index |
| client_region_source | CharField(16) | - |
| region | FK(companies.Region) SET_NULL | - |
| last_activity_at | DateTimeField | db_index |
| last_customer_msg_at / last_agent_msg_at | DateTimeField | db_index (для UiStatus) |
| waiting_since / first_reply_created_at | DateTimeField | db_index |
| contact_last_seen_at / agent_last_seen_at | - | - |
| off_hours_channel/contact/note/requested_at | F5 off-hours | - |
| contacted_back_at/by | - | - |
| snoozed_until | - | - |
| identifier | CharField(255) | для внешних систем |
| additional_attributes / custom_attributes | JSONField | referer/browser/OS/IP |
| created_at | DateTimeField | db_index |
| rating_score / rating_comment / rated_at | PositiveSmallIntegerField/TextField/DateTimeField | оценка контакта после закрытия |

**M2M:** `labels` → ConversationLabel (related_name='conversations')

**Meta:** verbose_name=«Диалог», ordering нет.
**indexes:** 9 — base (branch+status, created_at, last_activity_at, waiting_since, first_reply_created_at) + composite (inbox+status+assignee, status+priority, branch+status+assignee, contact+inbox+status).
**constraints:** CheckConstraint `conversation_valid_status` (open/pending/waiting_offline/resolved/closed).

**Свойства:**
- `@property ui_status` — computed NEW/WAITING/IN_PROGRESS/CLOSED по статусу+assignee+таймингам
- `@property waiting_minutes` — минуты ожидания клиента
- `@classmethod escalation_thresholds()` — warn/urgent/rop_alert/pool_return из PolicyConfig.livechat_escalation (дефолт 3/10/20/40)

**Бизнес-методы:**
- `clean()` — защита инварианта branch=inbox.branch (или branch задан для глобального inbox)
- `save()` — установка branch, waiting_since, инициализация JSON полей + диспатч событий (`CONVERSATION_CREATED/UPDATED/STATUS_CHANGED/OPENED/RESOLVED/CLOSED/ASSIGNEE_CHANGED`) через `messenger.dispatchers`
- `last_activity_at_fallback()`

**Сервисы:** `messenger/services.py` (16 функций: create_or_get_contact, record_message, assign_conversation, auto_assign_conversation, has_online_operators_for_branch, select_routing_rule, get_default_branch_for_messenger, get_conversations_eligible_for_escalation, escalate_conversation, touch_assignee_last_seen, touch_contact_last_seen, transfer_conversation_to_branch), `messenger/assignment_services/`, `messenger/automation.py`, `messenger/company_autolink.py`, `messenger/routing.py`, `messenger/selectors.py`, `messenger/reporting.py`, `messenger/online_status.py`.

**Тесты:** 17 тестовых файлов в `messenger/tests/` (test_auto_assign, test_escalation, test_transfer, test_ui_status, test_resolution_field, test_widget_api, test_visibility, test_heartbeat, test_canned_responses, test_operator_actions_api, test_private_messages, test_conversation_context_api, test_settings_ui, test_widget_offhours, test_widget_security_features, test_api_security, test_company_autolink).

---

### Message
**Описание:** Сообщение в диалоге. Направление IN/OUT/INTERNAL. Защита от флуда (20/мин). Лимит 150k символов. Автообновление last_activity_at/first_reply_created_at через save().
**Verbose name:** «Сообщение»
**TextChoices (Direction):** IN / OUT / INTERNAL (приватная заметка)
**Константы:** `NUMBER_OF_PERMITTED_ATTACHMENTS=15`, `MAX_CONTENT_LENGTH=150000`, `MESSAGE_PER_MINUTE_LIMIT=20`.
**Поля:** conversation (FK CASCADE), direction (db_index), body (TextField), processed_message_content, content_attributes JSONField, external_source_ids JSONField, source_id (TextField, null=True, db_index), sender_user (FK User SET_NULL), sender_contact (FK Contact SET_NULL), created_at (db_index), delivered_at, read_at, is_private (db_index).
**Meta:**
- ordering=['created_at','id']
- indexes=[conversation+direction+created_at, source_id, sender_contact+direction+created_at, sender_user+direction+created_at, conversation+is_private+created_at]

**Бизнес-методы:**
- `clean()` — инварианты direction/sender (IN→contact обязателен, OUT/INTERNAL→user обязателен) + anti-flood + длина
- `save()` — processed_message_content, атомарный update last_activity_at/last_customer_msg_at/last_agent_msg_at (через F), waiting_since, first_reply_created_at, диспатч событий (MESSAGE_CREATED/UPDATED/FIRST_REPLY_CREATED/REPLY_CREATED)
- `_update_waiting_since`, `_is_human_response`, `_update_first_reply`

**Тесты:** messenger/tests/test_private_messages.py, test_widget_api.py.

---

### MessageAttachment
**Описание:** Вложения сообщений (файлы). Лимит 15 на сообщение.
**Verbose name:** «Вложение сообщения»
**Поля:** message (FK CASCADE), file (FileField `messenger/attachments/%Y/%m/%d/`), original_name (255), content_type (120), size (BigInt), created_at (db_index).
**Бизнес-методы:** `save()` — snapshot метаданных + валидация лимита вложений.
**Тесты:** косвенно через widget_api тесты.

---

### ContactInbox
**Описание:** Связь контакта с inbox (Chatwoot-style). Один контакт может быть в нескольких inbox'ах. Хранит source_id (visitor_id) и pubsub_token для WebSocket.
**Verbose name:** «Связь контакта с inbox»
**Поля:** contact (FK CASCADE), inbox (FK CASCADE), source_id (TextField), pubsub_token (64, unique, auto-gen), created_at.
**Meta:** unique_together=[(inbox, source_id)], indexes=[inbox+source_id, pubsub_token].
**Бизнес-методы:** `save()` — auto-gen pubsub_token.
**Тесты:** косвенно через widget tests.

---

### RoutingRule
**Описание:** Правило маршрутизации диалога в подразделение. Приоритет по regions M2M + branch. Есть fallback-правила.
**Verbose name:** «Правило маршрутизации»
**Поля:** name (255), regions M2M(companies.Region), branch (FK CASCADE), inbox (FK CASCADE), priority (db_index, default 100), is_fallback, is_active (db_index).
**Meta:** ordering=['priority','id']
**Сервисы:** `messenger/services.py::select_routing_rule`, `messenger/routing.py`.
**Тесты:** messenger/tests/test_auto_assign.py.

---

### CannedResponse
**Описание:** Быстрые ответы оператора. Могут быть филиальные и глобальные. Есть «быстрые кнопки».
**Verbose name:** «Шаблон ответа»
**Поля:** title (255), body TextField, branch (FK Branch SET_NULL), created_by (FK User CASCADE), created_at (db_index), is_quick_button (db_index), sort_order (PositiveInt).
**Meta:** ordering=['sort_order','title']
**Тесты:** messenger/tests/test_canned_responses.py.

---

### ConversationLabel
**Описание:** Метки/теги для диалога. 7 предопределённых цветов.
**Verbose name:** «Метка диалога»
**Поля:** title (64, unique), color (7, hex, default #3B82F6), created_at.
**Meta:** ordering=['title']
**Тесты:** TODO — явных тестов labels нет.

---

### AgentProfile
**Описание:** Профиль оператора (Chatwoot-style). OneToOne с User. Статус онлайн/отошёл/занят/офлайн.
**Verbose name:** «Профиль оператора»
**TextChoices (Status):** ONLINE / AWAY / BUSY / OFFLINE
**Поля:** user (OneToOne CASCADE), avatar_url (URLField 500), display_name (255), status (db_index), updated_at.
**Сервисы:** `messenger/online_status.py`.
**Тесты:** messenger/tests/test_heartbeat.py (10).

---

### PushSubscription
**Описание:** Browser Push (Web Push API + VAPID). Аналог Chatwoot notification_subscriptions.
**Verbose name:** «Push-подписка»
**Поля:** user (FK CASCADE), endpoint (URLField 500, unique), p256dh (200), auth (100), created_at, is_active.
**Meta:** indexes=[user+is_active]
**Сервисы:** `messenger/push.py`.
**Тесты:** TODO — явных тестов push нет.

---

### Campaign (messenger)
**Описание:** Проактивная кампания виджета — сообщение посетителю после N секунд на странице. ⚠️ Коллизия имени с `mailer.Campaign`.
**Verbose name:** «Кампания»
**TextChoices (Status):** ACTIVE / DISABLED
**Поля:** inbox (FK CASCADE), title (255), message TextField, url_pattern (500, default '*'), time_on_page (PositiveInt 10), status, only_during_business_hours, created_at.
**Meta:** indexes=[inbox+status]
**Тесты:** TODO — отдельного test-файла нет.

---

### AutomationRule
**Описание:** Правила автоматизации (Chatwoot automation_rules). Event-driven: событие → условия → действия.
**Verbose name:** «Правило автоматизации»
**TextChoices (EventName):** CONVERSATION_CREATED / MESSAGE_CREATED / CONVERSATION_UPDATED
**Поля:** inbox (FK CASCADE nullable = для всех inbox), name (255), description TextField, event_name (32), conditions JSONField (list), actions JSONField (list), is_active, created_at.
**Meta:** indexes=[event_name+is_active]
**Сервисы:** `messenger/automation.py`.
**Тесты:** TODO — явных тестов automation rules нет.

---

### ReportingEvent
**Описание:** События для аналитики (first_response, reply_time, conversation_resolved, conversation_opened). Агрегация в дашборде.
**Verbose name:** «Событие аналитики»
**TextChoices (EventType):** FIRST_RESPONSE / REPLY_TIME / CONVERSATION_RESOLVED / CONVERSATION_OPENED
**Поля:** name (32), value FloatField (сек), conversation (FK CASCADE nullable), inbox (FK SET_NULL), user (FK SET_NULL), created_at.
**Meta:** indexes=[name+created_at, name+inbox+created_at, name+user+created_at]
**Сервисы:** `messenger/reporting.py`.
**Тесты:** TODO — нет.

---

### Macro
**Описание:** Макросы (Chatwoot macros). Один клик = несколько действий. Personal или global.
**Verbose name:** «Макрос»
**Поля:** name (255), actions JSONField (list), visibility CharField (personal/global), user (FK CASCADE nullable=global), created_at.
**Meta:** indexes=[user+visibility]
**Тесты:** TODO — нет.

---

### ConversationTransfer
**Описание:** Лог передачи диалога между операторами/подразделениями.
**Verbose name:** «Передача диалога»
**Поля:** conversation (FK CASCADE), from_user (FK User SET_NULL), to_user (FK User PROTECT), from_branch (FK SET_NULL), to_branch (FK PROTECT), reason TextField, cross_branch BooleanField, created_at (db_index).
**Meta:** ordering=['-created_at'], indexes=[conversation+created_at, to_user+created_at]
**Тесты:** messenger/tests/test_transfer.py (16).

---

## app: notifications

### Notification
**Описание:** Персональное уведомление пользователя (info/task/company/system).
**Verbose name:** «Уведомление»
**TextChoices (Kind):** INFO / TASK / COMPANY / SYSTEM
**Поля:** user (FK CASCADE), kind, title (200), body TextField, url (300), is_read, created_at, payload JSONField (null, default dict).
**Meta:** indexes=[user+is_read+created_at, user+created_at]
**Сервисы:** `notifications/service.py::notify()`.
**Тесты:** notifications/tests_views.py (5 TestCase).

---

### CompanyContractReminder
**Описание:** Дедупликация напоминаний по окончанию договора (чтобы не слать одно и то же каждый день).
**Verbose name:** «Напоминание по договору»
**Поля:** user (FK CASCADE), company (FK CASCADE), contract_until DateField, days_before PositiveSmallInt, created_at.
**Meta:**
- constraints=[UniqueConstraint(user, company, contract_until, days_before) `uniq_contract_reminder`]
- indexes=[user+created_at `contractrem_u_created_idx`, user+company+contract_until `contractrem_u_c_until_idx`]
**Тесты:** notifications/tests_views.py::AllRemindersViewTest.

---

### CrmAnnouncement
**Описание:** Анонс/новость CRM для всех пользователей (info/important/urgent). Отложенный показ через scheduled_at.
**Verbose name:** «Объявление CRM»
**TextChoices (Type):** INFO / IMPORTANT / URGENT
**Поля:** title (200), body TextField, announcement_type, created_by (FK User SET_NULL), created_at, is_active, scheduled_at.
**Meta:** ordering=['-created_at']
**@property is_published**, методы `read_count()`, `total_users()`.
**Тесты:** notifications/tests_views.py (косвенно).

---

### CrmAnnouncementRead
**Описание:** Факт прочтения объявления пользователем.
**Verbose name:** «Прочтение объявления»
**Поля:** user (FK CASCADE), announcement (FK CASCADE), read_at auto_now_add.
**Meta:** constraints=[UniqueConstraint(user, announcement) `uniq_announcement_read`]
**Тесты:** косвенно через notifications tests.

---

## app: phonebridge

### PhoneDevice
**Описание:** Android-устройство пользователя. Периодически опрашивает CRM и получает команды на звонок.
**PK:** UUIDField
**Поля:** user (FK CASCADE), device_id (64, db_index), device_name (120), platform (16, default=android), app_version (32), fcm_token TextField, created_at, last_seen_at, last_poll_code/at, last_ip, last_error_code/message, encryption_enabled (BooleanField, default True).
**Meta:** unique_together=((user, device_id)), indexes=[user+device_id, last_seen_at]
**Тесты:** phonebridge/tests.py::RegisterDeviceViewTest.

---

### CallRequest
**Описание:** Запрос на звонок (CRM → Android). Богатая телеметрия: статус звонка, направление, метод определения, количество попыток.
**PK:** UUIDField
**TextChoices:**
- `Status`: PENDING / DELIVERED / CONSUMED / CANCELLED
- `CallStatus`: CONNECTED / NO_ANSWER / BUSY / REJECTED / MISSED / UNKNOWN
- `CallDirection`: OUTGOING / INCOMING / MISSED / UNKNOWN
- `ResolveMethod`: OBSERVER / RETRY / UNKNOWN
- `ActionSource`: CRM_UI / NOTIFICATION / HISTORY / UNKNOWN
**Поля:** user (FK CASCADE), created_by (FK User SET_NULL), company (FK Company SET_NULL), contact (FK Contact SET_NULL), phone_raw (64), note (255), is_cold_call (db_index), status (db_index), created_at/delivered_at/consumed_at, call_status (db_index), call_started_at, call_duration_seconds, call_ended_at, direction (db_index), resolve_method (db_index), attempts_count, action_source (db_index).
**Meta:** indexes=[user+status+created_at]
**Тесты:** phonebridge/tests.py (33 TestCase + UpdateCallInfoViewTest, PullCallViewTest, JwtAuthEnforcementTest).

---

### PhoneTelemetry
**Описание:** Телеметрия Android (latency/error/auth/queue/other).
**TextChoices (Type):** LATENCY / ERROR / AUTH / QUEUE / OTHER
**Поля:** device (FK SET_NULL), user (FK CASCADE), ts (DateTimeField), type, endpoint (128), http_code, value_ms, extra JSONField.
**Meta:** ordering=['-ts'], indexes=[user+ts, endpoint+ts]
**Тесты:** phonebridge/tests_stats.py.

---

### PhoneLogBundle
**Описание:** Логи Android (bundle upload).
**Поля:** device (FK SET_NULL), user (FK CASCADE), ts, level_summary (64), source (64), payload TextField.
**Meta:** ordering=['-ts'], indexes=[user+ts, source+ts]
**Тесты:** TODO — прямых тестов нет.

---

### MobileAppBuild
**Описание:** Версия APK мобильного приложения (production only).
**PK:** UUIDField
**Поля:** env (16, default production), version_name (32), version_code (Integer), file (FileField `mobile_apps/`, валидатор .apk), sha256 (64, auto-computed в save()), uploaded_at, uploaded_by (FK User SET_NULL), is_active (db_index).
**Meta:** ordering=['-uploaded_at'], indexes=[env+is_active+uploaded_at]
**Бизнес-методы:** `save()` — auto-SHA256; `_calculate_sha256()`; `get_file_size()/_display()`.
**Тесты:** phonebridge/tests_mobile_app.py::MobileAppLatestViewTests, ui/tests_mobile_apps.py::MobileAppsUIUploadTests.

---

### MobileAppQrToken
**Описание:** Одноразовый QR-токен для логина в мобильное приложение. TTL 5 мин, одноразовый. Храним и plain (token), и hash (token_hash).
**PK:** UUIDField
**Поля:** user (FK CASCADE), token (128, unique, db_index), token_hash (64, unique, db_index, auto-computed), created_at, expires_at (auto +5min), used_at, ip_address, user_agent (255).
**Meta:** ordering=['-created_at'], indexes=[user+created_at, expires_at]
**Бизнес-методы:** `@staticmethod hash_token(token)`, `save()` (auto expires_at + hash), `is_valid()`, `mark_as_used()`, `@classmethod generate_token()`.
**Тесты:** phonebridge/tests.py::QrTokenCreateViewTest.

---

## app: policy

### PolicyConfig (singleton)
**Описание:** Глобальная конфигурация политики доступа (observe/enforce). Одна строка через `load()`.
**TextChoices (Mode):** OBSERVE_ONLY / ENFORCE
**Поля:** mode (32, db_index), livechat_escalation JSONField (пороги warn/urgent/rop_alert/pool_return min), updated_at.
**Бизнес-методы:** `@classmethod load()`.
**Тесты:** policy/tests.py (90 упом.), tests_enforce_views.py.

---

### PolicyRule
**Описание:** Правило доступа (pages + actions, для роли или конкретного юзера). Разрешить/запретить с приоритетом.
**TextChoices:**
- `SubjectType`: ROLE / USER
- `Effect`: ALLOW / DENY
- `ResourceType`: PAGE / ACTION
**Поля:** enabled (db_index), priority (Integer, db_index, default 100), subject_type (db_index), role (32, db_index), user (FK CASCADE nullable), resource_type (db_index), resource (120, db_index), effect (db_index), conditions JSONField (reserved), created_at, updated_at.
**Meta:** indexes=[enabled+resource_type+resource, subject_type+role, subject_type+user, priority]. **Нет verbose_name!**
**Тесты:** policy/tests.py (PolicyRuleOverrideTest, EnforceTest, BaselineAllowedForRoleTest).

---

## app: tasksapp

### TaskType
**Описание:** Справочник типов задач (иконка + цвет бейджа).
**Verbose name:** нет (!)
**Поля:** name CharField(120) unique, icon (32), color (32).
**Тесты:** tasksapp/tests.py (косвенно).

---

### Task
**Описание:** Задача менеджера. Может быть разовой или повторяющейся (RRULE RFC 5545). Есть race-защита для генерации рекуррентных экземпляров. PK=UUID.
**TextChoices (Status):** NEW / IN_PROGRESS / DONE / CANCELLED
**PK:** UUIDField
**Поля:** created_by (FK User SET_NULL), assigned_to (FK User SET_NULL), company (FK Company SET_NULL), type (FK TaskType SET_NULL), title (255), description TextField, status (db_index), created_at/updated_at, due_at (db_index), completed_at, is_urgent (db_index), recurrence_rrule (500), parent_recurring_task (FK self SET_NULL), recurrence_next_generate_after, external_source (32, db_index), external_uid (120, db_index).
**Meta:**
- **Нет verbose_name!**
- indexes=[assigned_to+status+due_at, company+status, status+due_at, assigned_to+updated_at]
- constraints=[UniqueConstraint(parent_recurring_task, due_at, condition=parent_recurring_task NOT NULL) `uniq_task_recurrence_occurrence`; CheckConstraint(status IN choices) `task_valid_status`]
**Сервисы:** `tasksapp/services.py::TaskService`, `tasksapp/policy.py`, `tasksapp/importer_ics.py`, `tasksapp/tasks.py` (Celery generate_recurring_tasks).
**Тесты:** tasksapp/tests.py (8 TestCase), tests_recurrence.py (ParseRRuleTest, GenerateRecurringTasksTest), tests_services.py (4 TaskService TestCase).

---

### TaskComment
**Описание:** Комментарий к задаче.
**Verbose name:** нет (!)
**Поля:** task (FK CASCADE), author (FK User SET_NULL), text TextField, created_at.
**Meta:** ordering=['created_at']
**Тесты:** tasksapp/tests.py::TaskCommentTestCase.

---

### TaskEvent
**Описание:** История изменений задачи (смена статуса, переназначение, перенос дедлайна).
**Verbose name:** нет (!)
**TextChoices (Kind):** CREATED / STATUS_CHANGED / ASSIGNED / DEADLINE_CHANGED
**Поля:** task (FK CASCADE), actor (FK User SET_NULL), kind (32), old_value (255), new_value (255), created_at.
**Meta:** ordering=['created_at']
**Тесты:** tasksapp/tests.py::TaskEventTestCase.

---

## app: ui

### UiGlobalConfig (singleton)
**Описание:** Глобальные настройки UI (колонки списка компаний). Одна запись pk=1.
**Verbose name:** «Настройки интерфейса»
**COMPANY_LIST_COLUMNS (class-level):** name, address, overdue, inn, status, spheres, responsible, branch, region, updated_at.
**Поля:** company_list_columns JSONField (default=list), updated_at.
**Бизнес-методы:** `@classmethod load()`.
**Тесты:** ui/tests_settings_views.py (8).

---

### AmoApiConfig (singleton)
**Описание:** Настройки OAuth подключения к amoCRM. Токены шифруются Fernet (`MAILER_FERNET_KEY`). Есть fallback на plaintext для миграции.
**Verbose name:** «Интеграция amoCRM»
**Поля:** domain (255), client_id (255), client_secret (255, plaintext — устар.), client_secret_enc TextField, redirect_uri (500), access_token_enc TextField, refresh_token_enc TextField, long_lived_token_enc TextField, token_type (32), expires_at, last_error TextField, region_custom_field_id IntegerField, updated_at.
**Бизнес-методы:**
- `@property access_token / refresh_token / long_lived_token` + setters (Fernet)
- `get_client_secret()/set_client_secret()` — с обратной совместимостью
- `@classmethod load()`
- `is_connected()` — проверка OAuth или long-lived
**Сервисы:** `amocrm/client.py`, `amocrm/migrate.py`, `ui/views/` migrate.
**Тесты:** ui/tests/test_amocrm_migrate.py, amocrm/tests.py, tests_client.py.

---

### UiUserPreference
**Описание:** Персональные настройки UI пользователя (масштаб, режим карточки, кол-во на странице, дефолтная вкладка задач). OneToOne с User.
**Verbose name:** «Настройки интерфейса (пользователь)»
**Поля:** user (OneToOne CASCADE), font_scale DecimalField(4,3) [0.850–1.300], company_detail_view_mode (20, choices classic/modern), tasks_per_page PositiveSmallInt (25/50/100/200), companies_per_page (same), default_task_tab (20, all/mine/overdue/today), updated_at.
**Бизнес-методы:** `@classmethod load_for_user(user)`, `font_scale_float()`.
**Тесты:** ui/tests/test_settings_views.py, tests_settings_mail.py.

---

## Красные флаги

### 1. Модели без верhose_name / verbose_name_plural (UX недоработка)
Django админ-панель будет показывать `companystatus`, `taskevent` и т.п. вместо человеческих названий.

- `accounts.Branch`
- `accounts.User` (AbstractUser не задаёт verbose_name)
- `audit.ActivityEvent`
- `companies.CompanyStatus`
- `companies.CompanySphere`
- `companies.Company` (god-node!)
- `companies.CompanyNote`
- `companies.CompanyDeal`
- `companies.Contact`
- `companies.CompanyEmail`
- `companies.CompanyPhone`
- `companies.ContactEmail`
- `companies.ContactPhone`
- `companies.CompanyDeletionRequest`
- `companies.CompanySearchIndex`
- `mailer.MailAccount`
- `mailer.Unsubscribe`
- `mailer.UnsubscribeToken`
- `mailer.Campaign`
- `mailer.CampaignRecipient`
- `mailer.SendLog`
- `mailer.EmailCooldown`
- `mailer.CampaignQueue`
- `phonebridge.PhoneDevice`
- `phonebridge.CallRequest`
- `phonebridge.PhoneTelemetry`
- `phonebridge.PhoneLogBundle`
- `phonebridge.MobileAppBuild`
- `phonebridge.MobileAppQrToken`
- `policy.PolicyConfig`
- `policy.PolicyRule`
- `tasksapp.TaskType`
- `tasksapp.Task` (god-node!)
- `tasksapp.TaskComment`
- `tasksapp.TaskEvent`

**Приоритет:** Company + Task критичны — показываются в админке чаще всего.

---

### 2. Модели без явных прямых тестов (test_{model_name}.py)
(косвенные упоминания могут быть, но нет дедицированного тест-файла под модель)

- `accounts.UserAbsence` — косвенно через auto_assign
- `messenger.Channel` — **нет тестов**
- `messenger.ConversationLabel` — нет
- `messenger.PushSubscription` — нет
- `messenger.Campaign` (messenger) — нет
- `messenger.AutomationRule` — нет
- `messenger.ReportingEvent` — нет
- `messenger.Macro` — нет
- `phonebridge.PhoneLogBundle` — нет
- `companies.CompanyDeal` — только косвенно

---

### 3. God-nodes / модели с ≥10 входящими FK (высокий риск рефакторинга)

1. **User (accounts.User)** — используется ~40+ моделями через `settings.AUTH_USER_MODEL`. Абсолютный god-node проекта. Любое изменение каскадно.
2. **Company (companies.Company)** — входящих FK 11+:
   - CompanyNote, CompanyDeal, Contact, CompanyEmail, CompanyPhone, CompanyDeletionRequest, CompanySearchIndex, CompanyHistoryEvent, tasksapp.Task, mailer.CampaignRecipient, phonebridge.CallRequest, messenger.Conversation, notifications.CompanyContractReminder, Company (self — head_company)
3. **Branch (accounts.Branch)** — входящих FK 10+:
   - User, Company, CompanyDeletionRequest, messenger.Inbox, messenger.Conversation (PROTECT), messenger.RoutingRule, messenger.CannedResponse, messenger.ConversationTransfer (×2 from/to), accounts.BranchRegion
4. **Contact (companies.Contact)** — FK от ContactPhone, ContactEmail, mailer.CampaignRecipient, phonebridge.CallRequest.
5. **Conversation (messenger.Conversation)** — FK от Message, ConversationTransfer, ReportingEvent.
6. **Inbox (messenger.Inbox)** — FK от Channel, Conversation, ContactInbox, RoutingRule, Campaign (messenger), AutomationRule, ReportingEvent.

---

### 4. Устаревшие CharField для номеров телефонов/email (нарушение нормализации)
Проект нормализовал телефоны/email через отдельные модели (CompanyPhone/CompanyEmail/ContactPhone/ContactEmail), но **единые поля остались в моделях**:

- `companies.Company.phone` CharField(50) + `phone_comment` — **должны идти в CompanyPhone**, но помечены как основные. Нормализуются в save() (обходится через update()).
- `companies.Company.email` EmailField(254) — аналогично.
- `companies.Company.contact_name` + `contact_position` — **помечены как устаревшие в коде** (есть полноценный Contact с FK), но не удалены.
- `companies.Company.is_cold_call` — **явно помечен как устар.**, заменён на `primary_contact_is_cold_call` + `CompanyPhone.is_cold_call`.
- `phonebridge.CallRequest.phone_raw` CharField(64) — не нормализуется (исторический raw).
- `messenger.Contact.phone` CharField(50), `messenger.Contact.email` EmailField(254) — нормализуется в `clean()`, но только при явном вызове.
- `mailer.CampaignRecipient.email` EmailField — самостоятельное поле (+FK на Contact) — дублирование.

**Рекомендация:** миграция на чистую модель «всегда использовать *Phone/*Email таблицы + view для обратной совместимости». Но это большая миграция (F8 по roadmap).

---

### 5. Коллизии имён моделей
Два app имеют одноимённые модели — легко запутаться в импортах:

- `mailer.Campaign` (email-рассылка) ≠ `messenger.Campaign` (proactive widget messages)
- `companies.Contact` (контакт компании) ≠ `messenger.Contact` (посетитель виджета)

**Рекомендация:** всегда использовать явный импорт `from mailer.models import Campaign as MailCampaign` или аналогично.

---

### 6. Нормализация обходится через .update()/bulk_update()
Модели явно документируют, что `.save()` — «последняя линия обороны», но `QuerySet.update()` **не вызывает** `save()` → нормализация не применяется.

**Пострадавшие поля:**
- `Company.phone`, `Company.inn`, `Company.work_schedule`
- `CompanyPhone.value`
- `ContactPhone.value`

**Рекомендация:** добавить DB-триггеры или миграцию на `pre_save` signals (уже частично есть в `signals.py`).

---

### 7. Singleton без защиты от дублей на уровне БД
Используется паттерн `classmethod load()` с `get_or_create(pk=1)`, но ничего не мешает создать строку с pk=2:

- `policy.PolicyConfig` — load(id=1)
- `ui.UiGlobalConfig` — load(pk=1)
- `ui.AmoApiConfig` — load(pk=1)
- `mailer.GlobalMailAccount` — load(id=1)
- `mailer.SmtpBzQuota` — PK IntegerField default=1, editable=False (единственный с частичной защитой)

**Рекомендация:** добавить `CheckConstraint(pk=1)` или ForeignKey-паттерн.

---

### 8. Таблица `ActivityEvent` = 9.5M строк на проде, retention 180 дней
Помечено в проектной памяти (`project_prod_state.md`). Индексы только на entity_type/entity_id/company_id/created_at — composite индексов нет. Запросы по actor+created_at или verb+created_at сканят таблицу.

**Рекомендация:** добавить составной индекс `(actor, created_at)` и `(entity_type, entity_id, created_at)` — или перевести на партиционирование по месяцам.

---

### 9. Permissive `on_delete=models.SET_NULL` на бизнес-критичных связях
Некоторые FK используют SET_NULL там, где CASCADE/PROTECT были бы уместнее:

- `Company.status` SET_NULL — при удалении CompanyStatus все компании теряют статус молча.
- `CompanyDeletionRequest.company` SET_NULL + company_id_snapshot — ОК, но требует проверки консистентности snapshot.
- `tasksapp.Task.company` SET_NULL — при удалении компании задачи «осиротеют» — это намеренно, но может путать.
- `messenger.Conversation.company` SET_NULL — диалог теряет связь с компанией.

**Для проверки:** свериться с `docs/decisions.md` на предмет L-7 (отвязка контактов при удалении компании — явно задокументировано).

---

## Сведённая таблица всех моделей

| # | App | Модель | PK | Verbose name | Полей | Fields (FK) | Тесты |
|---|-----|--------|-----|--------------|-------|-------------|-------|
| 1 | accounts | Branch | int | ✗ | 3 | 0 | да |
| 2 | accounts | User | int | ✗ | ~12 | 1 (branch) | да |
| 3 | accounts | MagicLinkToken | int | ✓ | 7 | 2 | да |
| 4 | accounts | UserAbsence | int | ✓ | 7 | 2 | косв. |
| 5 | accounts | BranchRegion | int | ✓ | 4 | 1 | да |
| 6 | audit | ActivityEvent | UUID | ✗ | 8 | 1 | да |
| 7 | audit | ErrorLog | UUID | ✓ | 15 | 2 | да |
| 8 | companies | CompanyStatus | int | ✗ | 1 | 0 | косв. |
| 9 | companies | CompanySphere | int | ✗ | 2 | 0 | косв. |
| 10 | companies | Region | int | ✓ | 1 | 0 | косв. |
| 11 | companies | ContractType | int | ✓ | 7 | 0 | да |
| 12 | companies | **Company** | UUID | ✗ | ~40 | 9 | да |
| 13 | companies | CompanyNote | int | ✗ | 17 | 3 | косв. |
| 14 | companies | CompanyNoteAttachment | int | ✓ | 6 | 1 | косв. |
| 15 | companies | CompanyDeal | int | ✗ | 6 | 2 | косв. |
| 16 | companies | Contact | UUID | ✗ | 13 | 3 | да |
| 17 | companies | CompanyEmail | int | ✗ | 3 | 1 | да |
| 18 | companies | CompanyPhone | int | ✗ | 7 | 3 | да |
| 19 | companies | ContactEmail | int | ✗ | 3 | 1 | да |
| 20 | companies | ContactPhone | int | ✗ | 7 | 3 | да |
| 21 | companies | CompanyDeletionRequest | int | ✗ | 11 | 4 | да |
| 22 | companies | CompanySearchIndex | FK | ✗ | 11 | 1 (OneToOne) | да |
| 23 | companies | CompanyHistoryEvent | int | ✓ | 11 | 4 | косв. |
| 24 | mailer | MailAccount | int | ✗ | 12 | 1 (OneToOne) | да |
| 25 | mailer | GlobalMailAccount | int | ✗ | 12 | 0 | да |
| 26 | mailer | Unsubscribe | int | ✗ | 5 | 0 | да |
| 27 | mailer | UnsubscribeToken | int | ✗ | 3 | 0 | да |
| 28 | mailer | Campaign | UUID | ✗ | 14 | 1 | да |
| 29 | mailer | CampaignRecipient | UUID | ✗ | 8 | 3 | да |
| 30 | mailer | SendLog | UUID | ✗ | 7 | 3 | да |
| 31 | mailer | EmailCooldown | int | ✗ | 4 | 1 | да |
| 32 | mailer | SmtpBzQuota | int | ✓ | 9 | 0 | да |
| 33 | mailer | CampaignQueue | UUID | ✗ | 9 | 1 (OneToOne) | да |
| 34 | mailer | UserDailyLimitStatus | int | ✓ | 4 | 1 (OneToOne) | да |
| 35 | messenger | Inbox | int | ✓ | 7 | 1 | да |
| 36 | messenger | Channel | int | ✓ | 4 | 1 | ✗ |
| 37 | messenger | Contact (messenger) | UUID | ✓ | 9 | 1 | да |
| 38 | messenger | **Conversation** | int | ✓ | ~35 | 8 | да |
| 39 | messenger | Message | BigAuto | ✓ | 15 | 3 | да |
| 40 | messenger | MessageAttachment | int | ✓ | 5 | 1 | косв. |
| 41 | messenger | ContactInbox | int | ✓ | 5 | 2 | косв. |
| 42 | messenger | RoutingRule | int | ✓ | 6 | 2 + M2M | да |
| 43 | messenger | CannedResponse | int | ✓ | 7 | 2 | да |
| 44 | messenger | ConversationLabel | int | ✓ | 3 | 0 | ✗ |
| 45 | messenger | AgentProfile | int | ✓ | 5 | 1 (OneToOne) | да |
| 46 | messenger | PushSubscription | int | ✓ | 6 | 1 | ✗ |
| 47 | messenger | Campaign (messenger) | int | ✓ | 8 | 1 | ✗ |
| 48 | messenger | AutomationRule | int | ✓ | 8 | 1 | ✗ |
| 49 | messenger | ReportingEvent | int | ✓ | 6 | 3 | ✗ |
| 50 | messenger | Macro | int | ✓ | 5 | 1 | ✗ |
| 51 | messenger | ConversationTransfer | int | ✓ | 8 | 5 | да |
| 52 | notifications | Notification | int | ✓ | 8 | 1 | да |
| 53 | notifications | CompanyContractReminder | int | ✓ | 5 | 2 | да |
| 54 | notifications | CrmAnnouncement | int | ✓ | 8 | 1 | да |
| 55 | notifications | CrmAnnouncementRead | int | ✓ | 3 | 2 | косв. |
| 56 | phonebridge | PhoneDevice | UUID | ✗ | 13 | 1 | да |
| 57 | phonebridge | CallRequest | UUID | ✗ | ~20 | 4 | да |
| 58 | phonebridge | PhoneTelemetry | int | ✗ | 8 | 2 | да |
| 59 | phonebridge | PhoneLogBundle | int | ✗ | 6 | 2 | ✗ |
| 60 | phonebridge | MobileAppBuild | UUID | ✗ | 8 | 1 | да |
| 61 | phonebridge | MobileAppQrToken | UUID | ✗ | 8 | 1 | да |
| 62 | policy | PolicyConfig | int | ✗ | 3 | 0 | да |
| 63 | policy | PolicyRule | int | ✗ | 9 | 1 | да |
| 64 | tasksapp | TaskType | int | ✗ | 3 | 0 | косв. |
| 65 | tasksapp | **Task** | UUID | ✗ | 15 | 5 | да |
| 66 | tasksapp | TaskComment | int | ✗ | 4 | 2 | да |
| 67 | tasksapp | TaskEvent | int | ✗ | 6 | 2 | да |
| 68 | ui | UiGlobalConfig | int | ✓ | 2 | 0 | да |
| 69 | ui | AmoApiConfig | int | ✓ | 13 | 0 | да |
| 70 | ui | UiUserPreference | int | ✓ | 7 | 1 (OneToOne) | да |

_Примечание: «полей» — приблизительное число без id/created_at/updated_at. «косв.» = покрыто только косвенно (фикстуры/setUp). «✗» = нет прямых тестов._

---

## TODO для следующих волн

### Wave 0.2 — Инвентаризация API
- REST endpoints (DRF ViewSets, urls.py): проверить mapping модель → endpoint.
- Widget API (публичный): `messenger/widget_api.py`, `messenger/api.py`.

### Wave 0.3 — Инвентаризация signals & tasks (Celery)
- `companies/signals.py`, `accounts/signals.py`, `ui/signals.py`, `messenger/signals.py`, `tasksapp/signals.py`
- Celery: `audit/tasks.py`, `companies/tasks.py`, `mailer/tasks/`, `messenger/tasks.py`, `notifications/tasks.py`, `phonebridge/tasks.py`, `tasksapp/tasks.py`

### Wave 0.4 — Инвентаризация миграций
- `backend/*/migrations/*.py` — по состоянию на 2026-04-19 у проекта 44 не применённых миграций на проде.

### Wave 1 — Рефакторинг god-nodes
- User, Company, Branch, Conversation, Inbox — разбор ответственности, DDD-границы.

### Wave 2 — Миграция устаревших полей
- Company.phone/email → CompanyPhone/CompanyEmail (SSOT)
- Company.contact_name/position → Contact

### Wave 3 — Закрытие тестовых пробелов
- Покрыть Channel, AutomationRule, Macro, ReportingEvent, PushSubscription прямыми unit-тестами.

---

_Документ сгенерирован на основе анализа 10 файлов моделей + 60+ тестовых файлов + 10+ services модулей. Wave 0.1 аудита._
