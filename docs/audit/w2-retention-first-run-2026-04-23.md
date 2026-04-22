# Retention first-run verification — 2026-04-22

**Status**: PENDING (first run scheduled 2026-04-23 at 03:15 MSK).

---

## Why not run yet

- Beat task `purge-old-policy-events` added в commit `56b54890` (2026-04-22 ~06:25 UTC).
- Container auto-deploy завершён ~06:35 UTC on 2026-04-22.
- Scheduled time crontab(hour=3, minute=15) = 00:15 UTC = 03:15 MSK.
- At 03:15 MSK on 2026-04-22 — container ещё был на старом коде (без task entry).
- First actual run: **2026-04-23 at 03:15 MSK**.

## Current state (session start, 10:22 MSK 2026-04-22)

| Metric | Value |
|--------|-------|
| total `entity_type='policy'` events | 9 489 689 |
| older than 14 days | 7 492 558 |
| last 24h | 1 (from W2.1.3a verification) |
| latest event | 2026-04-22 06:34 UTC (09:34 MSK, W2.1.3a qa_manager probe) |

## Expected after first run (2026-04-23 03:15 MSK)

- Batch processing: 749 batches × 10 000 rows ≈ 7.49M deletions.
- Estimated duration: 15-30 min (chunked, не блокирует DB).
- After run: total ≈ ~2M (только last 14 days retained).

## Verification plan for 2026-04-23 session

1. After 03:30 MSK on 2026-04-23:
   ```bash
   ssh sdm@5.181.254.172 "docker compose ... logs celery-beat --since 24h | grep purge"
   ssh sdm@5.181.254.172 "docker compose ... logs celery --since 24h | grep purge_old_policy"
   ```
2. Count policy events:
   - Expected < 2.5M (reduced from 9.5M).
   - Expected `older_than_14d` ≈ 0.

3. If run errored — investigate и file issue. Не blocker для ongoing work.

## Decision for this session

- Continue W2.1.3c + W2.2 as planned.
- Revisit retention verification в следующей session (2026-04-23+).
