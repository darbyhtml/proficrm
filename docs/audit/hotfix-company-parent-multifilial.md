# Diagnosis: "Cannot attach multiple filials" — Company parent multi-select issue

**Report date**: 2026-04-22.
**Reporter**: РОП Тюменского филиала (via Dmitry).
**Claim**: "Раньше можно было присоединять несколько филиалов к одной карточке. Сейчас только одну."

---

## TL;DR — **Не регрессия, не баг**

- `Company.head_company` = **ForeignKey(self)** в обеих branches (prod `be569ad4` и main). Schema никогда не менялась.
- UI widget `data-ms-single="1"` identical в prod и main. Всегда был single-select.
- Migration `0009_company_head_company.py` (исторически первое появление поля) создал его как ForeignKey.
- **НЕТ commit в git log** который когда-либо делал multi-select вариант.

**Вероятная причина жалобы**: user flow misunderstanding + отсутствие "manage filials from head's edit page" UI. Не регрессия — design feature gap.

---

## Data model

### Current main (HEAD = `b8a235d4`)

```python
# backend/companies/models.py:184-192
head_company = models.ForeignKey(
    "self",
    verbose_name="Головная организация",
    null=True,
    blank=True,
    on_delete=models.SET_NULL,
    related_name="client_branches",
    help_text="Если эта карточка — подразделение клиента, выберите головную организацию.",
)
```

### Prod (`be569ad4`)

```python
# backend/companies/models.py:151-159
head_company = models.ForeignKey(
    "self",
    verbose_name="Головная организация",
    null=True,
    blank=True,
    on_delete=models.SET_NULL,
    related_name="client_branches",
    help_text="Если эта карточка — филиал/подразделение клиента, выберите головную организацию.",
)
```

### Conclusion

**Идентичны** по structure. Единственное отличие: help_text ("подразделение" vs "филиал/подразделение") — i18n rename из commit `9c60d1bb UI(i18n): Филиал → Подразделение`. Semantic field behavior unchanged.

**Regression? NO.**

### Migration history

Single migration creating the field:
- `0009_company_head_company.py` — ForeignKey("self") + SET_NULL + related_name="client_branches".

No subsequent migration ever altered field type.

---

## UI / Form analysis

### Form (`backend/ui/forms.py`)

`CompanyEditForm` declares:
```python
head_company = FlexibleCompanyChoiceField(queryset=Company.objects.none(), required=False)
```

`FlexibleCompanyChoiceField` = `ModelChoiceField` subclass → **single-value** field. Widget: `forms.Select` (single-select HTML).

### Template (`backend/templates/ui/company_edit.html`)

Line 146:
```html
<div class="ms" data-ms-single="1" data-placeholder="— Не выбрано —" ...>
```

`data-ms-single="1"` attribute forces multi-select JS component в **single-value mode**. Widget shows one button с одним выбранным значением.

### Prod template diff

Prod has identical `data-ms-single="1"`. Template help text tweaks но widget behavior identical.

**Widget NEVER был multi-select** на этом поле.

### Display on head's view (read-only aggregation)

`backend/templates/ui/company_detail.html:1242-1260`:
```html
{% if org_head and org_head.id == company.id %}
  {% if org_branches %}
    <section class="card card-pad">
      <div class="font-medium mb-3">Подразделения организации</div>
      <div class="space-y-2">
        {% for b in org_branches %}
          <a class="block rounded-lg border p-3 hover:bg-brand-soft/20" href="/companies/{{ b.id }}/">
            ...
          </a>
        {% endfor %}
      </div>
    </section>
  {% endif %}
{% endif %}
```

**Read-only list** children через reverse relation `client_branches` (unlimited count). No multi-select для bulk management.

---

## Git history

### Commits touching head_company / multi-filial logic

Relevant из `git log --follow backend/companies/models.py`:

| SHA | Msg | Impact |
|-----|-----|--------|
| `de043809` | Fix(R5-P2): валидация циклов head_company + 3 теста | Cycle prevention (не changes field type) |
| `9c60d1bb` | UI(i18n): Филиал → Подразделение в текстах шаблонов и verbose_name | Text rename only |

Из `git log --follow backend/templates/ui/company_edit.html`:

| SHA | Msg |
|-----|-----|
| `f0706541` | Fix: make head company selection reliable |
| `2c3b7688` | Исправлена инициализация виджета выбора головной компании и улучшена логика отображения филиалов |

**Никакого commit который вводил или удалял multi-select** для head_company field. History consistent: always ForeignKey + single-select widget.

### Commits `be569ad4..main` touching this area

Found in range: i18n rename, style fixes, но **нет behavioral changes** по multi-filial attachment logic.

---

## Prod DB verification

**BLOCKED**: Path E hook prevents any access к `/opt/proficrm/` (даже read-only `ls`). Cannot run SQL queries.

**Alternative evidence (without DB access)**:
- Migration `0009` confirms FK schema on prod (applied before prod froze at `be569ad4`).
- No migration between `0009` и current что could have altered type.
- Thus prod DB = FK schema, same as main.

**Что blockiruет прямое verification**: CLAUDE.md Path E hook.

**Pre-fix verification (если пользователь approves fix)**: user runs manually на prod:
```sql
\d companies_company
-- Expected output includes:
--   head_company_id | bigint | FOREIGN KEY (companies_company.id)
```

---

## User flow analysis — **что РОП видит vs что ожидает**

### Actual behavior (current и historical)

**Attach filial_1 к head_company_A**:
1. Open filial_1's edit page: `/companies/<filial_1_id>/edit/`.
2. В поле "Головная организация" выбрать head_company_A (single-select AJAX widget).
3. Save.

**Attach filial_2 к head_company_A**:
1. Open filial_2's edit page.
2. Set "Головная организация" = head_company_A.
3. Save.

Результат: head_company_A's detail page shows **both filials** в section "Подразделения организации" (unlimited count, read-only aggregation).

### РОП's possible mental model

**Scenario 1 (most likely)**: Ожидает на **HEAD company's edit page** видеть поле "Филиалы" с multi-select → bulk attach/detach children. Такой UI **never existed** в этой системе.

**Scenario 2**: Confusion с другим полем. Например, `spheres` (сферы деятельности) — ДА multi-select (`data-ms="1"`). Возможно expects similar UI для filials.

**Scenario 3**: Reference к другой системе. РОП мог работать раньше с другим CRM где multi-filial assignment был feature.

**Scenario 4 (unlikely)**: В какой-то период кто-то custom-patched prod с multi-select (не через git). Но нет evidence.

---

## Fix complexity assessment

| Option | Scope | Risk | Fits Path E? |
|--------|-------|------|--------------|
| **A** — Documentation / user education | Explain attach-per-child flow + point at "Подразделения организации" reverse list | Zero | ✅ |
| **B** — UI improvement на head's edit page | Add read-only list "Подразделения" на head's edit form + explicit "add filial" button что opens filial's edit page | Low (UI only) | ✅ (staging → W9 prod) |
| **C** — Bulk-attach feature | Add form на head's page: multi-search children → set head_company=head в batch. Still FK (no schema change) | Medium (new view + form + JS) | ✅ (staging → W9 prod) |
| **D** — Schema change к M2M | Change FK to ManyToManyField, data migration, all reverse usages rewrite | HIGH | ❌ (requires prod migration dedicated window) |

### Recommendation

**Option A first** — respond to РОП: "Current behavior correct, здесь как работает: ...". Если РОП все равно wants feature — consider Option B или C в W9 UI redesign (deferred prod deploy = no urgency).

**NOT Option D** — schema change для перевода в M2M contradicts current design (one child может legitimately иметь только one parent в business sense — иначе org structure ambiguous).

---

## Hotfix feasibility

**No hotfix needed** — не баг. Respond с explanation.

If user wants UX improvement (Option B/C):
- **NOT point-fix на прод сегодня** — нет urgent security/data issue.
- **Defer to W9 UX redesign** — natural fit с broader frontend work.
- Alternative: emergency mini-wave W2.5 staging-first, deploy при W9 accumulated.

---

## Blocker for W2?

**NO**. This diagnostic took ~15 min of read-only work. W2 progress (2FA infrastructure, staging synced на `b8a235d4`, awaiting user manual 2FA setup) **continues undisturbed**.

---

## Recommendation — conversation с РОПом

Suggested response template:

> Проверили — функциональность «одна головная компания на филиал» **всегда такой и была** в системе. Это не регрессия и не недавнее изменение.
>
> **Как работает сейчас**:
> 1. Открой карточку ФИЛИАЛА (не головной), нажми редактировать.
> 2. Поле "Головная организация" — поиск и выбор одной головной.
> 3. Сохранить.
> 4. На странице ГОЛОВНОЙ компании автоматически появится секция «Подразделения организации» — там видны все привязанные филиалы.
>
> Если нужно массово привязать несколько филиалов — сейчас это по одному через редактирование каждого филиала. Bulk-интерфейс (одна форма для нескольких филиалов сразу) может быть добавлен в рамках **W9 UX редизайн** (плановая волна).
>
> **Вопрос для уточнения**: Раньше как именно ты прикреплял несколько филиалов? Был такой UI — форма со списком множественного выбора? На какой странице? Это поможет понять, думаешь ли ты о фиче из другой системы или о чём-то конкретном у нас.

---

## Session artifacts

- Docs only: `docs/audit/hotfix-company-parent-multifilial.md` (this file).
- Zero code changes.
- Zero prod touches.
- W2 progress не blocker'нут.
