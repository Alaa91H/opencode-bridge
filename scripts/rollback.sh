#!/usr/bin/env bash
# Roll back to the Git revision recorded before the last successful deployment.
set -Eeuo pipefail

readonly BRIDGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly PREVIOUS_DEPLOYED_REF_FILE="${BRIDGE_DIR}/runtime/previous-deployed-ref"

cd "$BRIDGE_DIR"

if [[ $# -gt 1 ]]; then
  echo "الاستخدام: scripts/rollback.sh [مرجع_Git_محدد]" >&2
  exit 64
fi

if [[ $# -eq 1 ]]; then
  TARGET_REF="$1"
else
  if [[ ! -s "$PREVIOUS_DEPLOYED_REF_FILE" ]]; then
    echo "لا يوجد مرجع منشور سابق مسجل. مرر مرجع Git أو وسمًا يدويًا." >&2
    exit 1
  fi
  TARGET_REF="$(cat "$PREVIOUS_DEPLOYED_REF_FILE")"
fi

TARGET_COMMIT="$(git rev-parse --verify "${TARGET_REF}^{commit}")"
CURRENT_COMMIT="$(git rev-parse HEAD)"

if [[ "$TARGET_COMMIT" == "$CURRENT_COMMIT" ]]; then
  echo "التراجع غير مطلوب؛ الإصدار الحالي يطابق ${TARGET_COMMIT:0:12}."
  exit 0
fi

echo "سيجري التراجع من ${CURRENT_COMMIT:0:12} إلى ${TARGET_COMMIT:0:12}."
exec scripts/deploy.sh "$TARGET_COMMIT"
