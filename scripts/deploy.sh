#!/usr/bin/env bash
# Deploy a tagged Git release without installing dependencies or building artifacts.
#
# The target must be an annotated or lightweight TAG, never a branch, a raw
# commit, or HEAD. Pinning to a tag is what makes a deployment identifiable,
# reproducible, and auditable: the tag is the version, and VERSION inside the
# tree must agree with it.
set -Eeuo pipefail

BRIDGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly BRIDGE_DIR
readonly PYTHON_BIN="${BRIDGE_DIR}/venv/bin/python"
readonly SERVICE_NAME="opencode-bridge-telegram.service"
readonly DEPLOY_STATE_DIR="${BRIDGE_DIR}/runtime"
readonly BACKUP_DIR="/home/ubuntu/opencode-backups/releases"
readonly IDENTITY_FILE="${DEPLOY_STATE_DIR}/deployment-identity"
readonly RELEASE_TAG_PATTERN='^v[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?$'

cd "$BRIDGE_DIR"

usage() {
  cat >&2 <<'USAGE'
الاستخدام: scripts/deploy.sh <وسم>

يجب أن يكون الهدف وسمًا منشورًا، مثل: scripts/deploy.sh v2.0.0-rc.6
لا يُقبل فرع أو commit أو HEAD، لأن النشر يجب أن يكون مربوطًا بوسم.
USAGE
}

if [[ $# -ne 1 ]]; then
  usage
  exit 64
fi

TARGET_TAG="$1"

if ! [[ "$TARGET_TAG" =~ $RELEASE_TAG_PATTERN ]]; then
  echo "المرجع ليس وسم إصدار صالح: ${TARGET_TAG}" >&2
  echo "الصيغة المطلوبة: v<major>.<minor>.<patch>‎[-<suffix>]" >&2
  usage
  exit 64
fi

# A tag must exist locally and be fetched, so a typo cannot silently deploy HEAD.
git fetch --prune --tags origin >/dev/null 2>&1 || true

if ! git rev-parse --verify --quiet "refs/tags/${TARGET_TAG}" >/dev/null; then
  echo "الوسم غير موجود محليًا: ${TARGET_TAG}" >&2
  echo "نفّذ: git fetch origin --tags" >&2
  exit 66
fi

TARGET_COMMIT="$(git rev-parse --verify "refs/tags/${TARGET_TAG}^{commit}")"
TARGET_VERSION="$(git show "${TARGET_COMMIT}:VERSION" 2>/dev/null | tr -d '[:space:]' || true)"
if [[ -z "$TARGET_VERSION" ]]; then
  echo "الوسم ${TARGET_TAG} لا يحتوي ملف VERSION." >&2
  exit 65
fi
if [[ "v${TARGET_VERSION}" != "${TARGET_TAG}" ]]; then
  echo "عدم تطابق الإصدار: الوسم ${TARGET_TAG} لكن VERSION هو ${TARGET_VERSION}" >&2
  echo "يجب أن يطابق ملف VERSION وسم الإصدار المنشور." >&2
  exit 65
fi

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
  sudo -n "${BRIDGE_DIR}/maintenance/install-root-assets.sh" || true
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
sudo -n "${BRIDGE_DIR}/maintenance/install-root-assets.sh"

systemctl --user restart "$SERVICE_NAME"
for _attempt in {1..15}; do
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
printf '%s %s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$TARGET_COMMIT" "$TARGET_TAG" >> "${DEPLOY_STATE_DIR}/deployment-history.log"
printf '%s\n' "$TARGET_COMMIT" > "${DEPLOY_STATE_DIR}/deployed-ref"

# A single machine-readable identity file: the running service, the watchdog, and
# an operator can all read the deployed version from one place.
cat >"${IDENTITY_FILE}" <<EOF
{
  "tag": "${TARGET_TAG}",
  "version": "${TARGET_VERSION}",
  "commit": "${TARGET_COMMIT}",
  "deployed_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF
chmod 0644 "${IDENTITY_FILE}"

# Stamp the unit so `systemctl --user cat` shows what is actually deployed.
systemctl --user set-property --runtime "${SERVICE_NAME}" \
  "Environment=BRIDGE_RELEASE_TAG=${TARGET_TAG}" 2>/dev/null || true

echo "deployment=passed tag=${TARGET_TAG} version=${TARGET_VERSION} revision=${TARGET_COMMIT:0:12} backup=$(basename "$archive")"
trap - ERR
