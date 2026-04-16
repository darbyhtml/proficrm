#!/usr/bin/env python3
"""PreToolUse hook — блокирует git commit при утечках секретов.

Сканирует `git diff --cached` на паттерны секретов: Fernet/Django
ключи, пароли, API-ключи, приватные ключи в PEM-формате. Смотрит
только ДОБАВЛЕННЫЕ строки (удалённые игнорирует).

Срабатывает только на `git commit` — не на log, diff, show и т.п.
"""
import json
import re
import subprocess
import sys

# Паттерны намеренно строгие, чтобы не флагать фикстуры/тесты
SECRET_PATTERNS = [
    (r"FERNET_KEY\s*=\s*[\"']?[A-Za-z0-9_\-=]{20,}", "FERNET_KEY"),
    (r"DJANGO_SECRET_KEY\s*=\s*[\"']?[^\s\"']{20,}", "DJANGO_SECRET_KEY"),
    (r"SECRET_KEY\s*=\s*[\"'][^\"'\s]{20,}[\"']", "SECRET_KEY"),
    (r"password\s*=\s*[\"'][^\"'\s]{8,}[\"']", "password="),
    (r"api[_-]?key\s*=\s*[\"'][^\"'\s]{16,}[\"']", "api_key="),
    (r"-----BEGIN (RSA |DSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----", "PRIVATE KEY"),
    (r"AKIA[0-9A-Z]{16}", "AWS access key"),
    (r"ghp_[A-Za-z0-9]{36}", "GitHub PAT"),
]


def main() -> None:
    try:
        data = json.loads(sys.stdin.read())
    except Exception:
        sys.exit(0)

    cmd = data.get("tool_input", {}).get("command", "")
    if not re.match(r"^\s*git\s+commit\b", cmd):
        sys.exit(0)

    cwd = data.get("cwd") or data.get("tool_input", {}).get("cwd")

    try:
        diff = subprocess.check_output(
            ["git", "diff", "--cached", "--no-color"],
            cwd=cwd,
            stderr=subprocess.DEVNULL,
            timeout=10,
        ).decode("utf-8", errors="replace")
    except Exception:
        sys.exit(0)  # не репа или git упал — пропускаем

    added = [
        ln[1:] for ln in diff.splitlines()
        if ln.startswith("+") and not ln.startswith("+++")
    ]

    matched: list[tuple[str, str]] = []
    for line in added:
        for pattern, label in SECRET_PATTERNS:
            if re.search(pattern, line, re.IGNORECASE):
                snippet = line.strip()[:100]
                matched.append((label, snippet))
                break

    if matched:
        reason = "ЗАПРЕЩЕНО: в staged-файлах найдены признаки секретов:\n"
        for label, snippet in matched[:5]:
            reason += f"  [{label}] {snippet}\n"
        reason += (
            "Убери секрет из коммита (git reset <file>), "
            "вынеси в .env, проверь .gitignore."
        )
        sys.stdout.reconfigure(encoding="utf-8")
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": reason,
                    }
                },
                ensure_ascii=False,
            )
        )
    sys.exit(0)


if __name__ == "__main__":
    main()
