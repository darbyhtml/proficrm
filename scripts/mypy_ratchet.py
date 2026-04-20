#!/usr/bin/env python3
"""
Mypy ratchet check — для CI и локальной валидации.

Сравнивает текущий прогон mypy с baseline в docs/audit/mypy-baseline.json.
Политика:
- FAIL, если total_errors > baseline + tolerance_buffer (по умолчанию +5).
- PASS, если total_errors <= baseline (не растёт).
- Если total_errors < baseline — PASS + подсказка обновить baseline
  (после устранения группы ошибок разработчик коммитит новый baseline,
   CI «застёгивается» на новой отметке).

Wave 0.2 (2026-04-20). Автор: Claude Opus.

Usage:
    scripts/mypy_ratchet.py           # проверить текущий код vs baseline
    scripts/mypy_ratchet.py --update  # обновить baseline на текущее состояние
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE_FILE = REPO_ROOT / "docs" / "audit" / "mypy-baseline.json"
MYPY_CONFIG = REPO_ROOT / "mypy.ini"

ERROR_RE = re.compile(r"^(backend/[^:]+):(\d+):(\d+)?:?\s*error:\s*.+\[([a-z-]+)\]\s*$")


def run_mypy() -> tuple[int, dict[str, int], dict[str, int], list[str]]:
    """Запускает mypy и возвращает (total, by_code, by_file, raw_lines)."""
    result = subprocess.run(
        [
            "mypy",
            "backend/",
            "--no-error-summary",
            f"--config-file={MYPY_CONFIG}",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    lines = (result.stdout + result.stderr).splitlines()
    by_code: Counter[str] = Counter()
    by_file: Counter[str] = Counter()
    total = 0
    for line in lines:
        m = ERROR_RE.match(line)
        if not m:
            continue
        total += 1
        path, _ln, _col, code = m.groups()
        by_code[code] += 1
        by_file[path] += 1
    return total, dict(by_code), dict(by_file), lines


def load_baseline() -> dict:
    if not BASELINE_FILE.exists():
        print(f"❌ Baseline not found: {BASELINE_FILE}", file=sys.stderr)
        print("Run: scripts/mypy_ratchet.py --update", file=sys.stderr)
        sys.exit(2)
    return json.loads(BASELINE_FILE.read_text(encoding="utf-8"))


def save_baseline(total: int, by_code: dict, by_file: dict) -> None:
    """Сохраняет текущее состояние как новый baseline."""
    existing: dict = {}
    if BASELINE_FILE.exists():
        existing = json.loads(BASELINE_FILE.read_text(encoding="utf-8"))
    data = {
        "snapshot_date": existing.get("snapshot_date", "2026-04-20"),
        "last_updated": subprocess.check_output(
            ["git", "log", "-1", "--format=%cs"], cwd=REPO_ROOT, text=True
        ).strip(),
        "wave": existing.get("wave", "0.2"),
        "mypy_version": "1.17.1",
        "python_version": "3.13",
        "total_errors": total,
        "files_with_errors": len(by_file),
        "top_10_error_codes": dict(Counter(by_code).most_common(10)),
        "top_20_files": dict(Counter(by_file).most_common(20)),
        "policy": existing.get(
            "policy",
            {
                "gate": "ratchet — CI fails if PR adds new errors beyond tolerance",
                "script": "scripts/mypy_ratchet.py",
                "tolerance_buffer": 5,
                "next_wave_target": "W1: -100, W2: -200, ..., W14: 0",
            },
        ),
    }
    BASELINE_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"✅ Baseline updated: {total} errors in {len(by_file)} files → {BASELINE_FILE}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--update", action="store_true", help="Save current state as new baseline"
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Print raw mypy output on failure"
    )
    args = parser.parse_args()

    print("▶ Running mypy on backend/...")
    total, by_code, by_file, raw_lines = run_mypy()

    if args.update:
        save_baseline(total, by_code, by_file)
        return 0

    baseline = load_baseline()
    baseline_count = baseline["total_errors"]
    tolerance = baseline.get("policy", {}).get("tolerance_buffer", 5)
    limit = baseline_count + tolerance

    print(f"  Current:  {total} errors in {len(by_file)} files")
    print(f"  Baseline: {baseline_count} errors ({baseline.get('snapshot_date', '?')})")
    print(f"  Tolerance: +{tolerance}  (limit: {limit})")

    if total > limit:
        delta = total - baseline_count
        print()
        print(f"❌ FAIL: {delta:+d} new errors beyond baseline (tolerance +{tolerance}).")
        print()
        print("Most common new errors (by code):")
        baseline_codes = Counter(baseline.get("top_10_error_codes", {}))
        current_codes = Counter(by_code)
        diff = (current_codes - baseline_codes).most_common(5)
        for code, cnt in diff:
            print(f"  +{cnt:4d}  {code}")

        if args.verbose:
            print("\nFirst 30 errors:")
            shown = 0
            for line in raw_lines:
                if ERROR_RE.match(line):
                    print(f"  {line}")
                    shown += 1
                    if shown >= 30:
                        break
        return 1

    if total < baseline_count:
        saved = baseline_count - total
        print()
        print(f"✅ PASS (improved by {saved} errors).")
        print(f"   Consider updating baseline: scripts/mypy_ratchet.py --update")
        return 0

    print()
    print("✅ PASS (at baseline).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
