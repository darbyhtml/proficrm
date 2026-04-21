#!/usr/bin/env python3
"""Generate masked summaries from gitleaks + trufflehog raw outputs.

Input:  /tmp/scan-results/gitleaks-full.json, trufflehog-full.jsonl
Output: /tmp/gitleaks-summary.md, /tmp/trufflehog-summary.md

Masks secret values — outputs только file, line, rule, SHA256 prefix.
Safe to commit the summaries; raw JSON files remain on scan host only.
"""
import json
import hashlib
from collections import Counter


def gitleaks_summary():
    with open("/tmp/scan-results/gitleaks-full.json") as f:
        leaks = json.load(f)

    lines = []
    lines.append("# Gitleaks findings — masked summary")
    lines.append("")
    lines.append(f"Total: {len(leaks)}")
    lines.append("")
    lines.append("## By Rule")
    lines.append("")
    for r, c in Counter(x["RuleID"] for x in leaks).most_common():
        lines.append(f"- {c:3}x  {r}")
    lines.append("")
    lines.append("## By File")
    lines.append("")
    for f, c in Counter(x["File"] for x in leaks).most_common():
        lines.append(f"- {c:3}x  {f}")
    lines.append("")
    lines.append("## Each finding (no raw values)")
    lines.append("")
    lines.append("| # | File | Line | Rule | SHA256-prefix |")
    lines.append("|---|------|------|------|---------------|")
    for i, x in enumerate(leaks, 1):
        secret = x.get("Secret", "")
        h = hashlib.sha256(secret.encode()).hexdigest()[:12]
        file_val = x["File"]
        line_val = x.get("StartLine", "?")
        rule = x["RuleID"]
        lines.append(f"| {i} | `{file_val}` | {line_val} | `{rule}` | `{h}` |")

    with open("/tmp/gitleaks-summary.md", "w", encoding="utf-8") as out:
        out.write("\n".join(lines) + "\n")


def trufflehog_summary():
    findings = []
    with open("/tmp/scan-results/trufflehog-full.jsonl") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                findings.append(json.loads(line))
            except Exception:
                pass

    lines = []
    lines.append("# TruffleHog findings — masked summary")
    lines.append("")
    lines.append(f"Total: {len(findings)}")
    live = sum(1 for d in findings if d.get("Verified"))
    lines.append(f"Verified (live): {live}")
    lines.append("")
    lines.append("## By Detector")
    lines.append("")
    for d, c in Counter(x.get("DetectorName", "?") for x in findings).most_common():
        lines.append(f"- {c}x  {d}")
    lines.append("")
    lines.append("## Each finding (no raw values)")
    lines.append("")
    for i, d in enumerate(findings, 1):
        meta = d.get("SourceMetadata", {}).get("Data", {}).get("Git", {})
        secret_raw = d.get("Raw", "")
        h = hashlib.sha256(secret_raw.encode()).hexdigest()[:12]
        ver = "LIVE" if d.get("Verified") else "unverified"
        det = d.get("DetectorName", "?")
        file_val = meta.get("file", "?")
        line_val = meta.get("line", "?")
        commit_val = meta.get("commit", "?")[:8]
        lines.append(
            f"- `{det}` [{ver}] `{file_val}`:{line_val} commit=`{commit_val}` hash=`{h}`"
        )

    with open("/tmp/trufflehog-summary.md", "w", encoding="utf-8") as out:
        out.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    gitleaks_summary()
    trufflehog_summary()
    print("Summaries written to /tmp/gitleaks-summary.md and /tmp/trufflehog-summary.md")
