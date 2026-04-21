#!/usr/bin/env python3
"""Классификатор коммитов для selective prod deploy.

Каждый commit попадает в одну из 6 категорий (см. docs/release/classification-summary.md):
  🟢 ops       — infra, CI, config, tooling. Без user-visible behavior.
  🔵 refactor  — внутренний рефакторинг, dead code, extracts. Behaviour идентичен.
  🟡 featured  — за django-waffle flag OFF на prod. Deploy безопасен, activate позже.
  🟠 ux-gated  — UI/UX видимые изменения. Hold — требует rollout plan.
  🔴 hold      — incomplete, WIP, breaking без обучения.
  ⚫ trivial   — whitespace, typos, comments, docs.
  ❓ review    — эвристика не нашла соответствия, нужен manual review.

Применение:
  python scripts/release/classify_commits.py <PROD_HEAD> <MAIN_HEAD> > docs/release/classification-raw.csv
"""

from __future__ import annotations

import csv
import re
import subprocess
import sys
from pathlib import Path


# --- Heuristics -----------------------------------------------------------------

TRIVIAL_SUBJECT = re.compile(
    r"^(chore\(?readme|docs?[:(]|Docs?[:(]|typo|whitespace|comment|readme)",
    re.IGNORECASE,
)

OPS_SUBJECT = re.compile(
    r"^(chore\((ci|deps|docker|makefile)|ci[:(]|ops[:(]|infra[:(]|build[:(]|deploy[:(]"
    r"|Harden\(Deploy|Harden\(CI|Fix\(CI|Fix\(Deploy|Chore\(CI|Chore\(Deps|perf[:(])",
    re.IGNORECASE,
)

REFACTOR_SUBJECT = re.compile(
    r"^(refactor|rework|extract|split|cleanup|dedup|Refactor)",
    re.IGNORECASE,
)

FEATURE_SUBJECT = re.compile(
    r"^(feat[:(]|Feat[:(]|feature[:(])",
    re.IGNORECASE,
)

FIX_SUBJECT = re.compile(
    r"^(fix[:(]|Fix[:(]|hotfix[:(]|Harden[:(])",
    re.IGNORECASE,
)

UI_SUBJECT = re.compile(
    r"^(UI[:(]|ui[:(]|UX[:(]|ux[:(])",
    re.IGNORECASE,
)

WAFFLE_HINTS = re.compile(r"(waffle|feature[\s_-]?flag|feature-flag)", re.IGNORECASE)
WIP_HINTS = re.compile(r"\b(wip|WIP|incomplete|broken|TODO|FIXME)\b")

# Файлы, указывающие на ops-категорию
OPS_FILE_PATTERNS = [
    re.compile(r"^\.github/workflows/"),
    re.compile(r"^docker-compose.*\.ya?ml$"),
    re.compile(r"^Dockerfile"),
    re.compile(r"^Makefile$"),
    re.compile(r"^backend/requirements.*\.txt$"),
    re.compile(r"^backend/crm/settings(_[^/]+)?\.py$"),
    re.compile(r"^backend/crm/asgi\.py$"),
    re.compile(r"^backend/crm/wsgi\.py$"),
    re.compile(r"^backend/crm/urls\.py$"),
    re.compile(r"^scripts/"),
    re.compile(r"^configs/nginx/"),
    re.compile(r"^nginx/"),
    re.compile(r"^docker/"),
    re.compile(r"^pyproject\.toml$"),
    re.compile(r"^\.pre-commit-config"),
    re.compile(r"^tests/smoke/"),
    re.compile(r"^\.gitleaks\.toml$"),
    re.compile(r"^\.env\.example$"),
]

# Frontend / UI files
UI_FILE_PATTERNS = [
    re.compile(r"^backend/templates/"),
    re.compile(r"^backend/static/"),
    re.compile(r"^backend/ui/views/"),
    re.compile(r"^backend/ui/templates/"),
    re.compile(r"^backend/messenger/templates/"),
    re.compile(r"^backend/messenger/static/"),
    re.compile(r"^frontend/"),
]

# Trivial files (docs, readme)
TRIVIAL_FILE_PATTERNS = [
    re.compile(r"^docs/"),
    re.compile(r"^README\.md$"),
    re.compile(r"^CLAUDE\.md$"),
    re.compile(r"^CHANGELOG"),
    re.compile(r"^\.claude/"),
]

# Test files
TEST_FILE_PATTERNS = [
    re.compile(r"^backend/.*/tests?/"),
    re.compile(r"^backend/.*/test_[^/]+\.py$"),
    re.compile(r"^backend/.*_test\.py$"),
    re.compile(r"^backend/.*/tests\.py$"),
    re.compile(r"^tests/"),
]

# Migration files
MIGRATION_FILE_PATTERN = re.compile(r"^backend/[^/]+/migrations/\d+_[^/]+\.py$")

# Model changes (could affect schema)
MODEL_FILE_PATTERNS = [
    re.compile(r"^backend/[^/]+/models\.py$"),
    re.compile(r"^backend/[^/]+/models/"),
]

# API / views (backend logic)
API_FILE_PATTERNS = [
    re.compile(r"^backend/[^/]+/api\.py$"),
    re.compile(r"^backend/[^/]+/api/"),
    re.compile(r"^backend/[^/]+/views\.py$"),
    re.compile(r"^backend/[^/]+/views/"),
    re.compile(r"^backend/[^/]+/serializers\.py$"),
    re.compile(r"^backend/[^/]+/urls\.py$"),
]


def matches_any(path: str, patterns: list[re.Pattern]) -> bool:
    return any(p.search(path) for p in patterns)


def classify(sha: str, subject: str, files: list[str]) -> tuple[str, str]:
    """Возвращает (category, rationale_hint).

    Rules приоритетно:
    1. Subject WIP → 🔴 hold.
    2. Только trivial files (docs, readme) → ⚫ trivial.
    3. Subject trivial + только trivial files → ⚫ trivial.
    4. Subject UI/UX → 🟠 ux-gated.
    5. Touches UI files → 🟠 ux-gated.
    6. Subject mentions waffle/feature flag → 🟡 featured.
    7. Subject feat + backend logic → 🟡 featured (unless UI).
    8. Subject ops/ci/harden/fix OR only ops files → 🟢 ops.
    9. Subject refactor + internal-only files → 🔵 refactor.
    10. Tests-only → 🔵 refactor.
    11. Иначе ❓ review.
    """
    # Rule 1: WIP
    if WIP_HINTS.search(subject):
        return ("🔴hold", "wip-in-subject")

    # Rule 2: файлов нет (merge commit пустой? maybe tag-create?)
    if not files:
        return ("⚫trivial", "empty-files")

    # Rule 3: только trivial files (docs, README, CLAUDE.md, .claude/)
    if all(matches_any(f, TRIVIAL_FILE_PATTERNS) for f in files):
        return ("⚫trivial", "docs-only")

    # Rule 4: subject explicitly UI/UX
    if UI_SUBJECT.match(subject):
        return ("🟠ux-gated", "ui-subject")

    # Rule 5: trivial subject (docs/readme) + mixed files
    if TRIVIAL_SUBJECT.match(subject) and all(
        matches_any(f, TRIVIAL_FILE_PATTERNS + [re.compile(r"^$")]) for f in files
    ):
        return ("⚫trivial", "trivial-subject+docs-files")

    # Rule 6: touches UI files — ux-gated
    touches_ui = any(matches_any(f, UI_FILE_PATTERNS) for f in files)
    touches_templates = any(
        f.startswith("backend/templates/") or ".html" in f for f in files
    )
    touches_static_ui_frontend = any(
        f.startswith("backend/static/") or f.startswith("frontend/") for f in files
    )

    if touches_ui and (touches_templates or touches_static_ui_frontend):
        return ("🟠ux-gated", "touches-templates-or-static")

    # Rule 7: waffle / feature flag mentions
    if WAFFLE_HINTS.search(subject):
        return ("🟡featured", "waffle-in-subject")

    # Rule 8: feat prefix → featured (unless touches UI which would have caught above)
    if FEATURE_SUBJECT.match(subject):
        # but if it touches UI files — ux-gated (already handled in rule 6)
        if touches_ui:
            return ("🟠ux-gated", "feat+ui")
        # Добавление нового модуля / new routes — featured если ещё за waffle, иначе review
        return ("🟡featured", "feat-subject-backend")

    # Rule 9: ops subject
    if OPS_SUBJECT.match(subject):
        return ("🟢ops", "ops-subject")

    # Rule 10: все файлы ops-like
    if all(matches_any(f, OPS_FILE_PATTERNS + TRIVIAL_FILE_PATTERNS) for f in files):
        return ("🟢ops", "only-ops-files")

    # Rule 11: refactor subject
    if REFACTOR_SUBJECT.match(subject):
        if touches_ui:
            return ("🟠ux-gated", "refactor+ui-touched")
        return ("🔵refactor", "refactor-subject")

    # Rule 12: только tests + (может быть) ops
    if all(
        matches_any(f, TEST_FILE_PATTERNS + OPS_FILE_PATTERNS + TRIVIAL_FILE_PATTERNS)
        for f in files
    ):
        return ("🔵refactor", "tests-only")

    # Rule 13: fix subject
    if FIX_SUBJECT.match(subject):
        if touches_ui:
            return ("🟠ux-gated", "fix+ui-touched")
        # Fix на backend logic — обычно ops-safe, но может быть behavior change.
        # Пометим как review чтобы manual check.
        touches_models = any(matches_any(f, MODEL_FILE_PATTERNS) for f in files)
        touches_migrations = any(MIGRATION_FILE_PATTERN.search(f) for f in files)
        if touches_models or touches_migrations:
            return ("❓review", "fix+models-or-migrations")
        touches_api = any(matches_any(f, API_FILE_PATTERNS) for f in files)
        if touches_api:
            return ("❓review", "fix+api-or-views")
        return ("🟢ops", "fix-subject-internal")

    # Default — review
    return ("❓review", "no-heuristic-match")


def git_log(prod_head: str, main_head: str) -> list[dict]:
    """Получает список коммитов в диапазоне prod_head..main_head."""
    format_str = "%H|%ci|%s"
    cmd = [
        "git",
        "log",
        "--reverse",
        f"--format={format_str}",
        "--no-merges",
        f"{prod_head}..{main_head}",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True, encoding="utf-8")
    commits = []
    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        parts = line.split("|", 2)
        if len(parts) != 3:
            continue
        commits.append({"sha": parts[0], "date": parts[1], "subject": parts[2]})
    return commits


def files_for(sha: str) -> list[str]:
    """Список файлов, затронутых коммитом."""
    result = subprocess.run(
        ["git", "show", "--name-only", "--format=", sha],
        capture_output=True,
        text=True,
        check=True,
        encoding="utf-8",
    )
    return [f for f in result.stdout.strip().split("\n") if f]


def stats_for(sha: str) -> tuple[int, int]:
    """(adds, dels) строк в коммите."""
    result = subprocess.run(
        ["git", "show", "--numstat", "--format=", sha],
        capture_output=True,
        text=True,
        check=True,
        encoding="utf-8",
    )
    adds = dels = 0
    for line in result.stdout.strip().split("\n"):
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        try:
            adds += int(parts[0])
            dels += int(parts[1])
        except ValueError:
            # binary file: '-'
            pass
    return adds, dels


def main(prod_head: str, main_head: str, out_path: Path) -> None:
    commits = git_log(prod_head, main_head)
    print(f"Processing {len(commits)} commits...", file=sys.stderr)

    rows = []
    for i, commit in enumerate(commits):
        if i % 50 == 0:
            print(f"  {i}/{len(commits)}...", file=sys.stderr)
        files = files_for(commit["sha"])
        adds, dels = stats_for(commit["sha"])
        category, hint = classify(commit["sha"], commit["subject"], files)
        rows.append(
            {
                "sha": commit["sha"][:8],
                "date": commit["date"][:10],
                "subject": commit["subject"],
                "files": len(files),
                "lines": adds + dels,
                "category": category,
                "hint": hint,
                "first_file": files[0] if files else "",
            }
        )

    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "sha",
                "date",
                "subject",
                "files",
                "lines",
                "category",
                "hint",
                "first_file",
            ],
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)

    # Summary
    from collections import Counter
    cats = Counter(r["category"] for r in rows)
    print("\n=== Classification summary ===", file=sys.stderr)
    for cat, count in sorted(cats.items(), key=lambda kv: -kv[1]):
        print(f"  {cat:15} {count:4} ({count*100//len(rows)}%)", file=sys.stderr)
    print(f"  {'TOTAL':15} {len(rows):4}", file=sys.stderr)


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print(
            "Usage: classify_commits.py <prod_head> <main_head> <output_csv>",
            file=sys.stderr,
        )
        sys.exit(2)
    main(sys.argv[1], sys.argv[2], Path(sys.argv[3]))
