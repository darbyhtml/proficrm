"""W3.2 (hotlist #7): composite indexes для audit_activityevent performance.

Motivation:
- Частые queries: company timeline ("все events для entity_type='Company' ORDER BY
  created_at DESC") + user activity feed ("все events actor=X ORDER BY created_at DESC").
- Current indexes — single-column btree (entity_type, created_at, actor_id, company_id).
- PostgreSQL planner умеет combine singletons bitmap-and, но composite index
  (entity_type, created_at DESC) даёт ordered scan → LIMIT 50 stops раньше.

Indexes added:
1. (entity_type, created_at DESC) — timeline queries per entity type.
2. (actor_id, created_at DESC) — user activity feed.

CREATE INDEX CONCURRENTLY — non-blocking (production-safe).
Requires atomic=False (cannot run внутри transaction).
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        (
            "audit",
            "0003_rename_audit_error_created_idx_audit_error_created_dc9bc2_idx_and_more",
        ),
    ]

    atomic = False  # CONCURRENTLY requires autocommit

    operations = [
        migrations.RunSQL(
            sql=(
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
                "audit_activityevent_entity_type_created_idx "
                "ON audit_activityevent (entity_type, created_at DESC);"
            ),
            reverse_sql=(
                "DROP INDEX IF EXISTS audit_activityevent_entity_type_created_idx;"
            ),
        ),
        migrations.RunSQL(
            sql=(
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
                "audit_activityevent_actor_created_idx "
                "ON audit_activityevent (actor_id, created_at DESC);"
            ),
            reverse_sql=(
                "DROP INDEX IF EXISTS audit_activityevent_actor_created_idx;"
            ),
        ),
    ]
