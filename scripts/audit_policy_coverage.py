#!/usr/bin/env python
"""
Policy coverage audit for UI views.

Goal:
- find functions protected by @login_required but missing policy enforcement
  (either via @policy_required decorator OR an early enforce(...) call).

Notes:
- Keep output ASCII-friendly (Windows console encodings vary).
"""
import re
import sys
from pathlib import Path


def find_functions_without_policy():
    """Find @login_required functions without @policy_required or enforce()."""
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
            has_policy_decorator = False
            func_name = None
            def_line_idx = None

            # Look ahead for @policy_required and def
            for j in range(i, min(i + 8, len(lines))):
                if "@policy_required" in lines[j]:
                    has_policy_decorator = True
                match = re.search(r"^def\s+(\w+)\s*\(", lines[j])
                if match:
                    func_name = match.group(1)
                    def_line_idx = j
                    break

            if not func_name or def_line_idx is None:
                i += 1
                continue

            # Exception allowlist: admin-only settings & view-as toggles
            is_exception = func_name.startswith("settings_") or func_name.startswith("view_as_")
            if is_exception:
                i += 1
                continue

            if has_policy_decorator:
                i += 1
                continue

            # If no decorator, accept early enforce(...) call in the first N lines of the function body
            has_enforce_call = False
            for k in range(def_line_idx + 1, min(def_line_idx + 40, len(lines))):
                if "enforce(" in lines[k]:
                    has_enforce_call = True
                    break
                # Stop early if we reached next top-level def/decorator
                if lines[k].startswith("@") or re.match(r"^def\s+\w+\s*\(", lines[k]):
                    break

            if not has_enforce_call:
                issues.append(
                    {
                        "line": def_line_idx + 1,
                        "function": func_name,
                    }
                )
        
        i += 1
    
    return issues


def main():
    """Main."""
    issues = find_functions_without_policy()
    
    if not issues:
        print("OK: all @login_required functions enforce policy.")
        return 0
    
    print(f"WARNING: found {len(issues)} functions without policy enforcement.\n")
    
    for issue in issues:
        print(f"LINE {issue['line']}: {issue['function']}")
    
    return 1


if __name__ == "__main__":
    sys.exit(main())
