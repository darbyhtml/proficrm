#!/usr/bin/env python3
"""PreToolUse hook — блокирует bash-команды, затрагивающие прод (/opt/proficrm/).

Разрешает /opt/proficrm-staging/, /opt/proficrm-backup/ и т.п. — только
голый /opt/proficrm/ считается продом.

Соответствует железному правилу из CLAUDE.md: прод деплоится только
вручную пользователем, Claude Code трогать не должен.

READ-ONLY MODE: если существует файл `.claude/.prod-readonly-allowed`
(временный, не в git), команды чтения разрешены, команды записи —
по-прежнему блокируются. Для аудит-сессий.
"""
import json
import pathlib
import re
import sys

# Паттерны команд, которые могут МОДИФИЦИРОВАТЬ прод.
# Если любая встречена в команде с /opt/proficrm/ — блок (даже в read-only mode).
WRITE_PATTERNS = [
    r"\brm\b",                     # удаление
    r"\bmv\b",                     # перемещение
    r"\bcp\b",                     # копирование (может перетереть)
    r"\bchmod\b",
    r"\bchown\b",
    r"\btruncate\b",
    r"\brmdir\b",
    r"\btee\b(?!\s+-a)",          # tee без -a = перезапись
    r"\bdd\b",                     # низкоуровневая запись
    r"\bsed\s+.*-i",               # in-place sed
    r"\bgit\s+reset\b",
    r"\bgit\s+checkout\s+--",
    r"\bgit\s+clean\b",
    r"\bgit\s+push\b",
    r"\bgit\s+pull\b",             # pull может менять состояние
    r"\bgit\s+stash\b",
    r"\bgit\s+commit\b",
    r"\bgit\s+rebase\b",
    r"\bgit\s+merge\b",
    r"\bdocker\s+compose\s+up\b",
    r"\bdocker\s+compose\s+down\b",
    r"\bdocker\s+compose\s+restart\b",
    r"\bdocker\s+compose\s+build\b",
    r"\bdocker\s+compose\s+rm\b",
    r"\bdocker\s+compose\s+stop\b",
    r"\bdocker\s+compose\s+start\b",
    r"\bdocker\s+rm\b",
    r"\bdocker\s+stop\b",
    r"\bdocker\s+kill\b",
    r"\bdocker\s+run\b",           # может записать
    # migrate (без --plan/--check/--list) = apply
    r"\bmanage\.py\s+migrate\b(?!\s+(--plan|--check|--list))",
    r"\bmanage\.py\s+flush\b",
    r"\bmanage\.py\s+makemigrations\b",
    r"\bmanage\.py\s+loaddata\b",
    r"\bmanage\.py\s+dumpdata\s+.*>",  # dumpdata с redirect = write
    r"\bmanage\.py\s+collectstatic\b",
    r"\bmanage\.py\s+createsuperuser\b",
    r"\bmanage\.py\s+changepassword\b",
    r"\bmanage\.py\s+shell_plus?\b", # shell разрешает любые .save()
    # Перенаправление вывода в /opt/proficrm/
    r">\s*/?opt/proficrm/",
    r">>\s*/?opt/proficrm/",
]


def _is_readonly_allowed() -> bool:
    """Временное разрешение read-only через файл-маркер."""
    here = pathlib.Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        if (parent / ".claude" / ".prod-readonly-allowed").is_file():
            return True
    return False


def main() -> None:
    try:
        data = json.loads(sys.stdin.read())
    except Exception:
        sys.exit(0)  # fail-open на мусоре

    cmd = data.get("tool_input", {}).get("command", "")
    if not cmd:
        sys.exit(0)

    # Whitelist безопасных команд: они работают внутри репозитория
    # или просто выводят текст, физически не могут тронуть /opt/proficrm/
    # на сервере, даже если строка содержит этот путь (например, в тексте
    # commit-message или эхо-вывода).
    SAFE_PREFIXES = (
        "git ",        # git не ходит за пределы репозитория
        "gh ",         # GitHub CLI — тоже репо-only
        "echo ",       # просто вывод
        "echo(",       # echo без пробела, редко, но бывает
        "printf ",
    )

    def _is_safe_segment(segment: str) -> bool:
        """Проверяет одну подкоманду (между &&, ||, |, ;, или строкой)."""
        s = segment.strip()
        if not s or s.startswith("#"):
            return True
        # Убираем префикс cd — он сам по себе не трогает /opt/proficrm/,
        # и часто идёт первой строкой многострочных скриптов
        if s.startswith("cd "):
            return True
        return s.startswith(SAFE_PREFIXES)

    # Бьём команду на сегменты по строкам и операторам bash-пайплайна.
    # Если ВСЕ сегменты безопасны — пропускаем, даже если упомянут /opt/proficrm/
    # (например, в тексте echo или commit-message).
    segments = re.split(r"[\n;&|]+", cmd)
    if all(_is_safe_segment(seg) for seg in segments):
        sys.exit(0)

    # Иначе — оставляем только ОПАСНЫЕ сегменты для проверки
    # (чтобы упоминания /opt/proficrm/ в безопасных частях не провоцировали блок).
    unsafe_text = "\n".join(seg for seg in segments if not _is_safe_segment(seg))

    # Убираем все вхождения /opt/proficrm-<что-угодно-кроме-слеша-и-пробела>
    # (staging, backup, old и т.п.). Если после этого в строке остаётся
    # /opt/proficrm — значит это голый прод-путь.
    cleaned = re.sub(r"/opt/proficrm-[^\s/'\"]+", "", unsafe_text)

    if "/opt/proficrm" in cleaned:
        # READ-ONLY режим: проверяем, есть ли write-операции.
        # Если нет — пропускаем, иначе блокируем.
        readonly_mode = _is_readonly_allowed()
        has_write = any(re.search(p, cmd, re.IGNORECASE) for p in WRITE_PATTERNS)

        if readonly_mode and not has_write:
            sys.exit(0)  # read-only команда в readonly режиме — разрешаем

        sys.stdout.reconfigure(encoding="utf-8")
        reason = (
            "ЗАПРЕЩЕНО: команда затрагивает прод (/opt/proficrm/). "
            "См. CLAUDE.md — прод деплоится только вручную пользователем. "
            "Для staging использовать /opt/proficrm-staging/."
        )
        if readonly_mode and has_write:
            reason += (
                "\n[READ-ONLY режим активен, но команда содержит write-операции — "
                "блокирую. Для только-чтения используй ls/cat/git log/docker ps/"
                "manage.py showmigrations или migrate --plan.]"
            )
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
