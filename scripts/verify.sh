#!/usr/bin/env bash
# Run deterministic checks that do not build packages or alter application data.
set -Eeuo pipefail

readonly BRIDGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly PYTHON_BIN="${BRIDGE_DIR}/venv/bin/python"

cd "$BRIDGE_DIR"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "بيئة Python الافتراضية غير متاحة: $PYTHON_BIN" >&2
  exit 1
fi

if ! git diff --check; then
  echo "توجد أخطاء مسافات أو نهايات أسطر في الشجرة العاملة." >&2
  exit 1
fi

bash -n maintenance/daily-maintenance.sh
bash -n maintenance/reboot-guard.sh
bash -n maintenance/install-root-assets.sh
"$PYTHON_BIN" -m compileall -q .
"$PYTHON_BIN" -m pip check
"$PYTHON_BIN" -m unittest discover -s tests -v

echo "verification=passed"
