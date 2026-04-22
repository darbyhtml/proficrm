# W2 Shadow logs analysis — NOT APPLICABLE (enforce already active)

**Snapshot**: 2026-04-22.

---

## TL;DR

Staging already в **enforce mode** (`PolicyConfig.mode = enforce`). Shadow-→-enforce transition **already completed** до начала W2 (вероятно в W0 era). План W2.1.1 ожидал shadow analysis, но она не применима — нет shadow decisions логгируются.

Вместо shadow log analysis используем **existing enforce state evidence**:

---

## Evidence 1: ErrorLog PermissionDenied (14 days)

```
ErrorLog total (14 days): 55
Top exception_types:
  django.db.utils.OperationalError: 28
  cryptography.fernet.InvalidToken: 23
  builtins.RuntimeError: 4
PermissionDenied count: 0
```

**Zero PermissionDenied exceptions** за 14 дней в enforce mode.

Caveat: staging = 1 user (Dmitry). Low traffic означает sample size ограничен. Это не доказывает absence false positives — просто значит user Dmitry в своём use case не triggered им.

Real validation будет в W9 prod deploy с 48-72h observation window + immediate rollback если rate of PermissionDenied spikes.

---

## Evidence 2: Policy decision logging disabled

```python
# settings.py
POLICY_DECISION_LOGGING_ENABLED = False  # default
```

Per `backend/policy/engine.py::_log_decision()` docstring:
> ВАЖНО: по умолчанию ВЫКЛЮЧЕНО. Без флага эта функция пишет ActivityEvent
> на каждый HTTP-запрос через @policy_required, что создаёт 150K+ записей
> в день при 50 пользователях.

Release 0 (2026-04-20) отключил logging для performance. Re-enable нужен только для targeted audit windows.

**W2.1 decision**: keep disabled, rely on ErrorLog + existing tests + W9 prod monitoring.

---

## Evidence 3: Existing rules coverage

- 330 DB rules (66 per role × 5 roles: manager/branch_director/sales_head/group_manager/admin).
- 66 / 102 registered resources имеют explicit DB rules (65% coverage).
- Остальные 36 (в основном API: api:companies/tasks/contacts/company_notes) идут через `_baseline_allowed()` defaults по role.

---

## Staging limitation acknowledgment

Per original plan language:
> Since staging = solo user (Dmitry), shadow log volume will be small — mostly
> Dmitry's exploration. This is expected и OK. Real validation happens in W9
> (prod accumulated deploy) via carefully observed enforce.

Этот analysis подтверждает: **staging не пригоден для real-world shadow validation**.

Staging purpose для W2:
1. Validate **rule logic** (unit/integration tests).
2. Validate **infrastructure** (engine performance, DB rule admin UI).
3. Catch **bugs в rule changes** до W9 prod deploy.

Real шapes validation happens в W9 prod:
- Deploy enforce mode post-W1-W8 accumulated.
- 48-72h monitoring window.
- Immediate rollback if PermissionDenied rate > threshold.
- User feedback channel для false positive reports.

---

## W2 safety net recommendations

Since real shadow data unavailable на staging:

1. **Targeted enable POLICY_DECISION_LOGGING_ENABLED** on staging для 1-2 days W2 mid-session:
   - Turn on before making rule changes.
   - Let Dmitry exercise все основные flows.
   - Review logged decisions для obvious false pattern.
   - Turn off после review.

2. **Integration test coverage boost**: `policy/tests_enforce_views.py` уже имеет 122 LOC, расширить до cover:
   - Each role × top-20 resources (5 × 20 = 100 test cases).
   - Expected allow/deny matrix per role.
   - Edge cases: superuser override, default behavior for rules-less resources.

3. **Pre-W9 dry-run**: включить logging на 1 неделю перед W9 deploy, посмотреть decisions против real-world-like traffic если получится.

---

## Conclusion

Shadow analysis **skipped** (inapplicable — already enforce).

Replaced with:
- ErrorLog review (0 PermissionDenied last 14d ✅)
- Rules coverage audit (330 rules, 66/102 resources)
- Baseline defaults confirmation (36 API resources use baseline_allowed_for_role)

Next: W2 plan focus сместился на **codification + migration** tasks, not shadow→enforce transition.
