#!/usr/bin/env bash
# Deploy a committed Git revision without installing dependencies or building artifacts.
set -Eeuo pipefail

readonly BRIDGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly PYTHON_BIN="${BRIDGE_DIR}/venv/bin/python"
readonly SERVICE_NAME="opencode-bridge-telegram.service"
readonly DEPLOY_STATE_DIR="${BRIDGE_DIR}/runtime"
readonly BACKUP_DIR="/home/ubuntu/opencode-backups/releases"

cd "$BRIDGE_DIR"

if [[ $# -gt 1 ]]; then
  echo "الاستخدام: scripts/deploy.sh [مرجع_Git_أو_وسم]" >&2
  exit 64
fi

TARGET_REF="${1:-HEAD}"
TARGET_COMMIT="$(git rev-parse --verify "${TARGET_REF}^{commit}")"
WORKTREE_COMMIT="$(git rev-parse HEAD)"
DEPLOYED_COMMIT="$WORKTREE_COMMIT"
if [[ -s "${DEPLOY_STATE_DIR}/deployed-ref" ]]; then
  DEPLOYED_COMMIT="$(git rev-parse --verify "$(cat "${DEPLOY_STATE_DIR}/deployed-ref")^{commit}")"
fi

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "يرفض النشر لأن شجرة Git تحتوي تعديلات غير ملتزم بها." >&2
  exit 1
fi

"$PYTHON_BIN" scripts/check_queue.py

if [[ "$TARGET_COMMIT" == "$DEPLOYED_COMMIT" ]]; then
  echo "الخدمة المنشورة تطابق المرجع المطلوب: ${TARGET_COMMIT:0:12}"
  exit 0
fi

rollback() {
  local code=$?
  echo "فشل النشر؛ تجري استعادة الإصدار السابق ${DEPLOYED_COMMIT:0:12}." >&2
  git checkout --detach --quiet "$DEPLOYED_COMMIT" || true
  sudo -n maintenance/install-root-assets.sh || true
  systemctl --user restart "$SERVICE_NAME" || true
  exit "$code"
}
trap rollback ERR

install -d -m 0700 -o ubuntu -g ubuntu "$BACKUP_DIR"
archive="${BACKUP_DIR}/opencode-bridge-${DEPLOYED_COMMIT:0:12}-$(date -u +%Y%m%dT%H%M%SZ).tar.gz"
git archive --format=tar "$DEPLOYED_COMMIT" | gzip -9 > "$archive"
chmod 0600 "$archive"
sha256sum "$archive" > "${archive}.sha256"
chmod 0600 "${archive}.sha256"

git checkout --detach --quiet "$TARGET_COMMIT"
"$PYTHON_BIN" systemd.py
scripts/verify.sh
sudo -n maintenance/install-root-assets.sh

systemctl --user restart "$SERVICE_NAME"
for attempt in {1..15}; do
  if [[ "$(systemctl --user is-active "$SERVICE_NAME")" == "active" ]]; then
    break
  fi
  sleep 1
done
[[ "$(systemctl --user is-active "$SERVICE_NAME")" == "active" ]]

set -a
# shellcheck disable=SC1091
source .env
set +a
curl --fail --silent --show-error --max-time 15 \
  --user "${OPENCODE_SERVER_USERNAME:-opencode}:${OPENCODE_SERVER_PASSWORD}" \
  "http://${OPENCODE_HOST:-127.0.0.1}:${OPENCODE_PORT:-4096}/global/health" >/dev/null

if [[ -s "${DEPLOY_STATE_DIR}/deployed-ref" ]]; then
  cp -f "${DEPLOY_STATE_DIR}/deployed-ref" "${DEPLOY_STATE_DIR}/previous-deployed-ref"
fi
printf '%s %s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$TARGET_COMMIT" "$TARGET_REF" >> "${DEPLOY_STATE_DIR}/deployment-history.log"
printf '%s\n' "$TARGET_COMMIT" > "${DEPLOY_STATE_DIR}/deployed-ref"

echo "deployment=passed revision=${TARGET_COMMIT:0:12} backup=$(basename "$archive")"
trap - ERR
