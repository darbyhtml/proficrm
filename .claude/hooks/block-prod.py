#!/usr/bin/env python3
"""PreToolUse hook — блокирует bash-команды, затрагивающие прод (/opt/proficrm/).

Разрешает /opt/proficrm-staging/, /opt/proficrm-backup/ и т.п. — только
голый /opt/proficrm/ считается продом.

Соответствует железному правилу из CLAUDE.md: прод деплоится только
вручную пользователем, Claude Code трогать не должен.
"""
import json
import re
import sys


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
        sys.stdout.reconfigure(encoding="utf-8")
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": (
                            "ЗАПРЕЩЕНО: команда затрагивает прод (/opt/proficrm/). "
                            "См. CLAUDE.md — прод деплоится только вручную пользователем. "
                            "Для staging использовать /opt/proficrm-staging/."
                        ),
                    }
                },
                ensure_ascii=False,
            )
        )
    sys.exit(0)


if __name__ == "__main__":
    main()
