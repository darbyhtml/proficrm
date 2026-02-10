#!/usr/bin/env bash
set -euo pipefail

# Быстрый линт: запрещаем TokenManager.getInstance() вне "белого списка" файлов.
# Идея:
# - Разрешено только в:
#   - auth/TokenManager.kt
#   - core/AppContainer.kt
#   - network/ApiClient.kt
# - В остальных местах нужно использовать init() или getInstanceOrNull().

ROOT="android/CRMProfiDialer/app/src/main/java"

if ! command -v rg >/dev/null 2>&1; then
  echo "ripgrep (rg) не найден в PATH. Установите rg, чтобы использовать check_token_manager_usage.sh."
  exit 1
fi

# Ищем все упоминания TokenManager.getInstance(
matches="$(rg 'TokenManager\.getInstance\(' \"$ROOT\" --glob '*.kt' || true)"

if [[ -z "$matches" ]]; then
  echo "✅ TokenManager.getInstance() не найден — правило пройдено"
  exit 0
fi

# Белый список файлов, где getInstance допустим
ALLOW=(
  "auth/TokenManager.kt"
  "core/AppContainer.kt"
  "network/ApiClient.kt"
)

filtered="$matches"
for p in "${ALLOW[@]}"; do
  # Отфильтровываем строки, где встречается путь из белого списка
  filtered="$(printf '%s\n' \"$filtered\" | grep -v \"$p\" || true)"
done

if [[ -n "$filtered" ]]; then
  echo "❌ Запрещённые использования TokenManager.getInstance() обнаружены вне белого списка:"
  echo
  echo "$filtered"
  echo
  echo "Разрешено использовать TokenManager.getInstance() только в следующих файлах (относительно $ROOT):"
  for p in "${ALLOW[@]}"; do
    echo " - $p"
  done
  exit 1
fi

echo "✅ TokenManager.getInstance() используется только в разрешённых местах"

