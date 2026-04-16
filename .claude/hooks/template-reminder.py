#!/usr/bin/env python3
"""PostToolUse hook — напоминает про docker restart при правке Django-шаблонов.

Gunicorn кэширует скомпилированные шаблоны в памяти воркеров, поэтому
`docker compose up -d web` на staging не всегда подхватывает изменения
(см. docs/problems-solved.md 2026-04-16).
"""
import json
import sys


def main() -> None:
    try:
        data = json.loads(sys.stdin.read())
    except Exception:
        sys.exit(0)

    fp = (
        data.get("tool_input", {}).get("file_path")
        or data.get("tool_response", {}).get("filePath")
        or ""
    )
    fp_norm = fp.replace("\\", "/")

    if not fp_norm.endswith(".html"):
        sys.exit(0)
    if "backend/templates/" not in fp_norm:
        sys.exit(0)

    sys.stdout.reconfigure(encoding="utf-8")
    print(
        json.dumps(
            {
                "systemMessage": (
                    "Шаблон изменён. При деплое на staging — "
                    "`docker compose -f docker-compose.staging.yml "
                    "-p proficrm-staging restart web` "
                    "(НЕ `up -d web`): gunicorn кэширует скомпилированные "
                    "шаблоны в памяти воркеров."
                )
            },
            ensure_ascii=False,
        )
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
