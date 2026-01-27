#!/usr/bin/env python
"""
Скрипт для аудита покрытия policy проверок в UI views.

Проверяет, что все функции с @login_required также имеют @policy_required.
"""
import re
import sys
from pathlib import Path


def find_functions_without_policy():
    """Найти функции с @login_required, но без @policy_required."""
    views_file = Path(__file__).parent.parent / "backend" / "ui" / "views.py"
    
    if not views_file.exists():
        print(f"Файл не найден: {views_file}")
        return []
    
    content = views_file.read_text(encoding="utf-8")
    lines = content.split("\n")
    
    issues = []
    i = 0
    while i < len(lines):
        line = lines[i]
        
        # Ищем @login_required
        if "@login_required" in line:
            # Проверяем следующие несколько строк на наличие @policy_required
            has_policy = False
            func_name = None
            
            # Ищем функцию в следующих строках
            for j in range(i, min(i + 5, len(lines))):
                if "@policy_required" in lines[j]:
                    has_policy = True
                    break
                # Ищем определение функции
                match = re.search(r"^def\s+(\w+)\s*\(", lines[j])
                if match:
                    func_name = match.group(1)
                    break
            
            if not has_policy and func_name:
                # Проверяем, не является ли это исключением (settings функции обычно имеют require_admin)
                is_exception = False
                for k in range(i, min(i + 10, len(lines))):
                    if "require_admin" in lines[k] or "settings_" in func_name:
                        is_exception = True
                        break
                
                if not is_exception:
                    issues.append({
                        "line": i + 1,
                        "function": func_name,
                        "code": line.strip(),
                    })
        
        i += 1
    
    return issues


def main():
    """Главная функция."""
    issues = find_functions_without_policy()
    
    if not issues:
        print("✅ Все функции с @login_required имеют @policy_required!")
        return 0
    
    print(f"WARNING: Found {len(issues)} functions without @policy_required:\n")
    
    for issue in issues:
        print(f"Строка {issue['line']}: {issue['function']}")
        print(f"  {issue['code']}\n")
    
    return 1


if __name__ == "__main__":
    sys.exit(main())
