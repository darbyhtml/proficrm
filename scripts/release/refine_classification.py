#!/usr/bin/env python3
"""Ручной review ❓review коммитов + применение overrides.

Читает docs/release/classification-raw.csv + apply manual review overrides,
выдаёт docs/release/classification-reviewed.csv.

Каждый override — результат просмотра subject + first_file + (для unclear)
git show <sha> --stat. Conservative rule: при сомнении — 🟠 ux-gated или 🟡 featured,
не 🟢 ops (лучше перестраховаться).
"""

from __future__ import annotations

import csv
from pathlib import Path


# Manual overrides — после review 63 ❓review коммитов.
# Key: sha[:8]. Value: (category, rationale).
#
# Правила по типу файла:
# - operator-panel.js / widget.js / widget.css / widget-loader.js → 🟠 ux-gated (public + user-visible)
# - backend/ui/views.py (single-file, pre-refactor) → 🟠 ux-gated (renders templates)
# - backend/messenger/api.py / views.py / utils.py / widget_api.py → 🟡 featured (messenger за MESSENGER_ENABLED)
# - backend/messenger/migrations/ → 🟡 featured (messenger db schema, flag-safe)
# - docs/wiki + docs/current-sprint + docs/... → ⚫ trivial
# - ruff autofixes, black format, mypy baseline — ⚫ trivial (format) или 🟢 ops
# - Harden/security + settings.py — 🟢 ops
# - Revert commits — 🟢 ops (undo)
# - Migration schema fix без модельного изменения → 🔵 refactor
# - Management commands (opt-in) → 🟢 ops

OVERRIDES: dict[str, tuple[str, str]] = {
    # --- Widget / operator-panel UI (public-visible) ---
    "691ed72": ("🟠ux-gated", "operator-panel.js UI fix"),
    "681e69e": ("🟠ux-gated", "widget-loader.js user-visible"),
    "bdc9367": ("🟠ux-gated", "widget.js SSE reconnect UI"),
    "814fa39": ("🟠ux-gated", "widget.css height/scroll"),
    "083ed99": ("🟠ux-gated", "widget.css text/icons/UI"),
    "546c035": ("🟠ux-gated", "widget.css CORS+UI"),
    "414af36": ("🟠ux-gated", "widget.js message history UX"),
    "ec56c0d": ("🟠ux-gated", "widget.js debug logging (behavior change)"),
    # --- backend/ui/views.py (pre-refactor monolith) ---
    "206ce1f": ("🟠ux-gated", "ui/views.py logger fix (conservative)"),
    "6076cad": ("🟠ux-gated", "ui/views.py logger inbox_name"),
    "d545148": ("🟠ux-gated", "ui/views.py last_activity_at rename"),
    "7ce8934": ("🟠ux-gated", "ui/views.py messenger analytics UI"),
    # --- Messenger backend (за MESSENGER_ENABLED flag) ---
    "f5d235f": ("🟡featured", "messenger/utils.py working_hours fix"),
    "875084": ("🟡featured", "messenger/widget_api.py SSE endpoint"),
    "8750843": ("🟡featured", "messenger/widget_api.py SSE endpoint"),
    "f461f72": ("🟡featured", "messenger/api.py rename fix"),
    "7fe9fd4": ("🟡featured", "middleware CSP skip widget-test"),
    "eeb1f16": ("🟡featured", "messenger/views.py HTTPS fix"),
    "c723d6e": ("🟡featured", "messenger/api.py SSE 406 fix"),
    "32e8716": ("🟡featured", "crm/urls.py sw-push.js"),
    "861375e": ("🟡featured", "messenger/api.py validation"),
    "f8e8b20": ("🟡featured", "messenger/api.py transaction+IP"),
    "c96781f": ("🟡featured", "messenger/api.py serializer whitelist"),
    "5a88c6e": ("🟡featured", "messenger/api.py auth check"),
    "7fcdcdd": ("🟡featured", "messenger/api.py UUID handling"),
    "50f1efe": ("🟡featured", "messenger/api.py ValidationError"),
    "b9e3f8b": ("🟡featured", "Dockerfile.staging + messenger SSE gevent"),
    "05cec09": ("🟡featured", "messenger/api.py XSS+deps+query"),
    # --- Messenger migrations (schema, за flag) ---
    "a18d211": ("🟡featured", "messenger migration name shortening"),
    "5e030ec": ("🟡featured", "messenger migration AddField/AlterField"),
    "a9b1b96": ("🟡featured", "messenger status constraint"),
    # --- Docs only ---
    "fa1627f": ("⚫trivial", "docs/wiki SSE fix"),
    "12325a0": ("⚫trivial", "docs/current-sprint Plan 1"),
    "da00391": ("⚫trivial", "docs/current-sprint Plan 2"),
    "c849952": ("⚫trivial", "docs/current-sprint Plan 3"),
    "87650f2": ("⚫trivial", "docs/current-sprint Plan 4"),
    "24e6c84": ("⚫trivial", "docs mass sync"),
    "b1fb00a": ("⚫trivial", "docs Release-0 postmortem"),
    "fc98953": ("⚫trivial", "CLAUDE.md graphify section"),
    "3e8df09": ("⚫trivial", "scope.py docstring only"),
    "26989d7": ("⚫trivial", "CLAUDE.md post-W0.3 cleanup"),
    # --- Ops / config / settings / CI tooling ---
    "48c2eb8": ("🟢ops", "deploy_staging.sh + CORS config"),
    "975418a": ("🟢ops", "logging_utils internal safe log"),
    "982b6ed": ("🟢ops", "settings.py 429 backoff rate"),
    "8df8beb": ("🟢ops", ".gitignore repo cleanup"),
    "d48f741": ("🟢ops", "Harden Security Phase 0 P0 (Android+backend)"),
    "4378f3e": ("🟢ops", "Harden Security Phase 1 P1 settings"),
    "5874749": ("🟢ops", "Harden Phonebridge rate-limit"),
    "4fdb934": ("🟢ops", "Harden P1 observability/SSOT/authz"),
    "441ccb7": ("🟢ops", "Revert F5 R1 auto_assign (undo)"),
    "c992270": ("🟢ops", "Chore cleanup_orphan_contacts cmd"),
    "cc183ee": ("⚫trivial", ".gitignore claude+playwright"),
    "c3e7809": ("🟢ops", "Wave0.2c ruff autofixes"),
    "0ccc672": ("🟢ops", "Wave0.2d mypy baseline"),
    "791cddf": ("🟢ops", "Wave0.2f pre-commit + linter"),
    "172d97c": ("🟢ops", "Wave0.2h JS minify build (not wired)"),
    "7e83482": ("🟢ops", "W0.4 /_staff/trigger-test-error (env-gated)"),
    # --- Format-only (behavior-preserving) ---
    "ea72704": ("⚫trivial", "black initial format pass"),
    # --- False-positive hold (слово "broken" в subject, но это Fix) ---
    "1787744": ("🟡featured", "messenger widget_demo URL fix (false-positive hold)"),
    "ef825af": ("🟡featured", "messenger macros/mentions auth fix (false-positive hold)"),
    # --- Refactor internal ---
    "880d445": ("🔵refactor", "tasksapp migration race fix (internal)"),
    "51b7ca7": ("🔵refactor", "remove unused imports view files"),
    "d43afe8": ("🔵refactor", "remove unused dashboard imports"),
    "dd23bea": ("🔵refactor", "phonebridge QR _like index fix"),
    "02efd88": ("⚫trivial", "drop Playwright snapshot (misc file)"),
    "0c142be": ("🔵refactor", "model drift pending migrations"),
}


def refine(raw_path: Path, out_path: Path) -> None:
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    rows = list(csv.DictReader(raw_path.open(encoding="utf-8")))

    applied = unapplied = 0
    missing_sha_for_override = []
    sha_prefix_map = {r["sha"]: r for r in rows}

    for sha_short, (category, rationale) in OVERRIDES.items():
        # Нормализация: 8 chars, попадает ровно
        found = None
        for csv_sha in sha_prefix_map:
            if csv_sha.startswith(sha_short):
                found = csv_sha
                break
        if found is None:
            missing_sha_for_override.append(sha_short)
            continue
        row = sha_prefix_map[found]
        if "review" in row["category"]:
            row["category"] = category
            row["hint"] = f"reviewed: {rationale}"
            applied += 1
        else:
            # Не review, но есть override — apply anyway с rationale reviewed
            row["category"] = category
            row["hint"] = f"forced-review: {rationale}"
            unapplied += 1

    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    # Summary
    from collections import Counter
    cats = Counter(r["category"] for r in rows)
    print(f"Applied overrides: {applied}")
    print(f"Overrides on non-review: {unapplied}")
    if missing_sha_for_override:
        print(f"Missing sha prefixes: {missing_sha_for_override}")
    print("\n=== Final distribution ===")
    for cat, count in sorted(cats.items(), key=lambda kv: -kv[1]):
        print(f"  {cat:20} {count:4} ({count*100//len(rows)}%)")
    print(f"  {'TOTAL':20} {len(rows):4}")

    # Check remaining review
    remaining = [r for r in rows if "review" in r["category"]]
    if remaining:
        print(f"\n⚠ Remaining review commits: {len(remaining)}")
        for r in remaining:
            print(f"  {r['sha']} {r['subject'][:70]}")


if __name__ == "__main__":
    refine(
        Path("docs/release/classification-raw.csv"),
        Path("docs/release/classification-reviewed.csv"),
    )
