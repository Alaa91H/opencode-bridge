#!/usr/bin/env bash
# Request a reboot only when package maintenance requires it; never notify for routine work.
set -Eeuo pipefail

readonly BRIDGE_DIR="/home/ubuntu/opencode-bridge"
readonly RUNTIME_DIR="${BRIDGE_DIR}/runtime"
readonly REQUEST_PATH="${RUNTIME_DIR}/reboot-request.json"
readonly DECISION_PATH="${RUNTIME_DIR}/reboot-decision.json"
readonly LOG_PATH="/var/log/opencode-bridge-reboot-guard.log"
readonly LOCK_PATH="/run/lock/opencode-bridge-reboot-guard.lock"
readonly WAIT_SECONDS="${REBOOT_WAIT_SECONDS:-300}"
readonly REBOOT_COMMAND="${REBOOT_COMMAND:-/usr/bin/systemctl reboot}"
readonly PYTHON_BIN="${BRIDGE_DIR}/venv/bin/python"

mkdir -p "$RUNTIME_DIR"
touch "$LOG_PATH"
chmod 640 "$LOG_PATH"
exec >>"$LOG_PATH" 2>&1

if [[ "${EUID}" -ne 0 ]]; then
  echo "$(date -Is) reboot guard must run as root" >&2
  exit 1
fi

if [[ ! -f /var/run/reboot-required ]]; then
  echo "$(date -Is) reboot guard skipped: reboot is no longer required"
  exit 0
fi

if [[ ! -f "${BRIDGE_DIR}/.env" ]]; then
  echo "$(date -Is) reboot guard failed: bridge environment file is missing" >&2
  exit 1
fi

exec 9>"$LOCK_PATH"
if ! flock -n 9; then
  echo "$(date -Is) reboot guard skipped: another guard is active"
  exit 0
fi

set -a
# shellcheck disable=SC1091
source "${BRIDGE_DIR}/.env"
set +a

TELEGRAM_CHAT_ID="${TELEGRAM_MAINTENANCE_CHAT_ID:-${TELEGRAM_ALLOWED_USERS%%,*}}"
if [[ -z "${TELEGRAM_BOT_TOKEN:-}" || -z "$TELEGRAM_CHAT_ID" ]]; then
  echo "$(date -Is) reboot guard skipped: no Telegram destination configured"
  exit 0
fi

now_epoch="$(date -u +%s)"
deadline_epoch="$((now_epoch + WAIT_SECONDS))"
request_id="reboot-$(date -u +%Y%m%dT%H%M%SZ)"
printf '{"deadline_epoch":%s,"request_id":"%s","status":"awaiting","wait_seconds":%s}\n' \
  "$deadline_epoch" "$request_id" "$WAIT_SECONDS" >"$REQUEST_PATH"
chown ubuntu:ubuntu "$REQUEST_PATH"
chmod 600 "$REQUEST_PATH"
rm -f "$DECISION_PATH"

reply_markup='{"inline_keyboard":[[{"text":"إعادة التشغيل الآن","callback_data":"reboot:now"},{"text":"إلغاء","callback_data":"reboot:cancel"}]]}'
message="تحتاج تحديثات النظام إلى إعادة تشغيل. أرسل «إعادة التشغيل الآن» أو «إلغاء». إذا لم يصل رد خلال 5 دقائق ولم تكن هناك مهمة قيد التنفيذ، سيُعاد تشغيل الخادم تلقائيًا."
if ! curl --fail --silent --show-error --max-time 20 \
  --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
  --data-urlencode "text=${message}" \
  --data-urlencode "reply_markup=${reply_markup}" \
  "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" >/dev/null; then
  rm -f "$REQUEST_PATH" "$DECISION_PATH"
  echo "$(date -Is) reboot guard stopped: reboot request could not be delivered" >&2
  exit 1
fi

echo "$(date -Is) reboot request sent: ${request_id}"

read_decision() {
  "$PYTHON_BIN" - "$DECISION_PATH" <<'PY'
import json
import sys
from pathlib import Path
try:
    data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
except (OSError, ValueError):
    print("")
else:
    print(data.get("action", ""))
PY
}

while (( "$(date -u +%s)" < deadline_epoch )); do
  decision="$(read_decision)"
  case "$decision" in
    cancel)
      printf '{"request_id":"%s","status":"cancelled"}\n' "$request_id" >"$REQUEST_PATH"
      chown ubuntu:ubuntu "$REQUEST_PATH"
      chmod 600 "$REQUEST_PATH"
      echo "$(date -Is) reboot cancelled by owner"
      exit 0
      ;;
    reboot_now)
      echo "$(date -Is) reboot approved by owner"
      break
      ;;
  esac
  sleep 2
done

if [[ "$(read_decision)" == "cancel" ]]; then
  printf '{"request_id":"%s","status":"cancelled"}\n' "$request_id" >"$REQUEST_PATH"
  chown ubuntu:ubuntu "$REQUEST_PATH"
  chmod 600 "$REQUEST_PATH"
  echo "$(date -Is) reboot cancelled at deadline"
  exit 0
fi

if ! "$PYTHON_BIN" "${BRIDGE_DIR}/scripts/check_queue.py"; then
  printf '{"request_id":"%s","status":"deferred_running_task"}\n' "$request_id" >"$REQUEST_PATH"
  chown ubuntu:ubuntu "$REQUEST_PATH"
  chmod 600 "$REQUEST_PATH"
  echo "$(date -Is) reboot deferred: task queue is not clear"
  exit 0
fi

printf '{"request_id":"%s","status":"rebooting"}\n' "$request_id" >"$REQUEST_PATH"
chown ubuntu:ubuntu "$REQUEST_PATH"
chmod 600 "$REQUEST_PATH"
echo "$(date -Is) rebooting after verified clear queue"
exec $REBOOT_COMMAND
