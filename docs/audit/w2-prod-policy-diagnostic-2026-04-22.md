# Prod policy events diagnostic — 2026-04-22

**Status**: SKIPPED (Path E hook blocks all `/opt/proficrm/` access, even read-only).

---

## Attempted access

```bash
ssh root@5.181.254.172 "cd /opt/proficrm && ls docker-compose*.yml"
# Blocked: "ЗАПРЕЩЕНО: команда затрагивает прод (/opt/proficrm/)"
```

Hook scope strict per Path E (2026-04-21 decision): any prod path touch requires `CONFIRM_PROD=yes` marker в промпте. Not present. Hook correctly blocks read-only `ls` too (hook path-matching, not intent-aware).

---

## Indirect estimate (without DB access)

Prod state по CLAUDE.md + git log:
- **Prod HEAD**: `release-v0.0-prod-current` = commit `be569ad4` (2026-03-17).
- **333 commits behind main** (including all W0, W1, W2 work).
- **POLICY_DECISION_LOGGING_ENABLED** — **не был deployed на prod**:
  - Release 0 hotfix (2026-04-20) disabled logging ON STAGING by setting env var.
  - Prod `be569ad4` предшествует всему этому — policy logging мог быть включён или выключен в том state.
  - Q17 deny-only filter и beat retention task — в main commit `56b54890` (2026-04-22), ещё не deployed на prod.

### Volume projection

Prod state: **unknown without direct query**. Possible scenarios:

| Scenario | Likelihood | Estimated volume | Action |
|----------|-----------|------------------|--------|
| Logging never enabled on prod | Medium | 0 events | No cleanup needed |
| Logging enabled partially (pre-be569ad4) | Medium | 1-5M events | Included in W9 accumulated deploy cleanup |
| Logging always on (pre-Release-0 hotfix в main, before prod lag) | Low | 5-50M events | Dedicated pre-W9 purge sprint required |

### Pre-W9 strategy recommendation

Given uncertainty — adopt **conservative approach**:

1. **Step 1 (Pre-W9 window, ~1 week before)**: User runs manual read-only SELECT query on prod to determine actual volume. Template query prepared:

   ```sql
   SELECT
     COUNT(*) as total_policy_events,
     COUNT(*) FILTER (WHERE entity_type='policy') as policy_entity_events,
     MIN(created_at) as oldest,
     pg_size_pretty(pg_total_relation_size('audit_activityevent')) as table_size
   FROM audit_activityevent
   WHERE entity_type = 'policy'
     OR resource LIKE 'policy%';
   ```

2. **Step 2 (Based on findings)**:
   - If < 1M: W9 accumulated deploy activates beat task, overnight runs clean backlog (7-14 days).
   - If 1M-10M: manual staged chunked delete 1-2 weeks before W9 deploy.
   - If > 10M: dedicated sprint pre-W9 (may require 1-2 full days of chunked purging).

3. **Step 3 (W9.10 deploy day)**:
   - Activate `POLICY_DECISION_LOGGING_ENABLED=1` в prod env.
   - Beat task handles ongoing retention (14-day TTL).

---

## Related: staging retention status (same session)

Staging has **9 489 689 total policy events** (historical from experimental logging periods):
- **7 481 120 older than 14 days** (would be purged by beat).
- **Recent 24h: 1 event** (Q17 deny-only filter работает корректно).

**Beat task `purge-old-policy-events` не выполнялся today (03:15 MSK)** — auto-deploy W2.1.3a commits завершён после 03:15 UTC (~06:00 UTC == 09:00 MSK deploy). Next scheduled: tomorrow 03:15 MSK → will purge 7.48M chunked (748 batches × 10K).

Staging's existing bloat — tolerable until tomorrow's natural run. No manual intervention.

---

## Decision

- **This session (W2.1.3b)**: skip prod diagnostic, proceed with Group B audit + Group D codification.
- **Next step**: user provides manual prod query результаты 1 week before W9, using template above.
- **Blocker check**: if prod shows > 10M events, W2 plan adds dedicated pre-W9 cleanup sprint.

No changes made в этой session's Step 0.3 phase — purely blocked + documented.
