#!/usr/bin/env python3
"""PostToolUse hook — прогоняет `ruff check --fix` на изменённых .py в backend/.

Если ruff не установлен на хосте — молча пропускает (fail-safe).
Вывод ruff возвращается в контекст модели через additionalContext,
чтобы Claude увидел оставшиеся проблемы и мог их исправить.
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys


def _find_ruff() -> str | None:
    """Ищет ruff в порядке: проектная .venv → системный PATH.

    Важно: проектная .venv имеет приоритет, чтобы версия ruff была
    зафиксирована через requirements-dev.txt (воспроизводимость у
    всей команды).
    """
    # Начинаем от текущей директории хука и идём вверх до корня проекта
    # (ищем pyproject.toml как маркер корня)
    here = pathlib.Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        if (parent / "pyproject.toml").exists() or (parent / ".git").exists():
            for candidate in [
                parent / ".venv" / "Scripts" / "ruff.exe",  # Windows venv
                parent / ".venv" / "bin" / "ruff",  # Unix venv
                parent / "venv" / "Scripts" / "ruff.exe",
                parent / "venv" / "bin" / "ruff",
            ]:
                if candidate.is_file():
                    return str(candidate)
            break
    # Фолбэк: системный ruff
    return shutil.which("ruff")


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
    if not fp:
        sys.exit(0)

    fp_norm = fp.replace("\\", "/")
    if not fp_norm.endswith(".py"):
        sys.exit(0)
    if "/backend/" not in fp_norm:
        sys.exit(0)

    ruff = _find_ruff()
    if not ruff:
        sys.exit(0)  # ruff не установлен — тихо пропускаем

    try:
        result = subprocess.run(
            [ruff, "check", "--fix", fp],
            capture_output=True,
            text=True,
            encoding="utf-8",  # Windows по умолчанию cp1251 — ломает ruff-вывод
            errors="replace",
            timeout=20,
        )
    except Exception:
        sys.exit(0)

    out = ((result.stdout or "") + (result.stderr or "")).strip()
    # Показываем только если остались нефиксящиеся проблемы
    if result.returncode != 0 and out:
        sys.stdout.reconfigure(encoding="utf-8")
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PostToolUse",
                        "additionalContext": (
                            f"ruff check на {os.path.basename(fp)} нашёл проблемы "
                            f"(автофикс уже применён где мог):\n{out[:1500]}"
                        ),
                    }
                },
                ensure_ascii=False,
            )
        )
    sys.exit(0)


if __name__ == "__main__":
    main()
