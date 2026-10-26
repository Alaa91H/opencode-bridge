#!/usr/bin/env bash
# Managed daily maintenance for the OpenCode Telegram Bridge host.
# It never notifies external channels for routine work and never deletes user files.
# The sole deletion exception is bridge-managed attachment artifacts older than seven days.
set -Eeuo pipefail

readonly BRIDGE_DIR="/home/ubuntu/opencode-bridge"
readonly BRIDGE_USER="ubuntu"
BRIDGE_UID="$(id -u "$BRIDGE_USER")"
readonly BRIDGE_UID
readonly USER_RUNTIME_DIR="/run/user/${BRIDGE_UID}"
readonly PYTHON_BIN="${BRIDGE_DIR}/venv/bin/python"
readonly RUNTIME_DIR="${BRIDGE_DIR}/runtime"
readonly REPORT_PATH="${RUNTIME_DIR}/maintenance-latest.md"
readonly HISTORY_DIR="${RUNTIME_DIR}/maintenance-history"
readonly ATTACHMENT_ROOT="${RUNTIME_DIR}/attachments"
# Retention window is published by the bridge from the owner's stored preference.
# The constant below is only the fallback when that file is missing or invalid.
readonly ATTACHMENT_RETENTION_DEFAULT_DAYS=7
readonly ATTACHMENT_RETENTION_DAYS_FILE="${RUNTIME_DIR}/retention-days"
readonly LOG_PATH="/var/log/opencode-bridge-maintenance.log"
readonly LOCK_PATH="/run/lock/opencode-bridge-maintenance.lock"
readonly APT_OPTIONS=(
  "-o" "Dpkg::Lock::Timeout=300"
  "-o" "Dpkg::Options::=--force-confold"
)

mkdir -p "$RUNTIME_DIR" "$HISTORY_DIR"
touch "$LOG_PATH"
chmod 640 "$LOG_PATH"
exec >>"$LOG_PATH" 2>&1

if [[ "${EUID}" -ne 0 ]]; then
  echo "This script must run as root." >&2
  exit 1
fi

exec 9>"$LOCK_PATH"
if ! flock -n 9; then
  echo "$(date -Is) maintenance skipped: another run is already active"
  exit 0
fi

# Honor the owner's emergency stop before any package or service mutation.
# A malformed or symlinked policy also stops privileged maintenance safely.
MODE_PATH="${RUNTIME_DIR}/autopilot-mode"
if [[ -L "$MODE_PATH" ]]; then
  echo "Automatic maintenance blocked: unsafe autopilot policy"
  exit 1
fi
if [[ -e "$MODE_PATH" ]]; then
  [[ -f "$MODE_PATH" ]] || { echo "Invalid autopilot mode file"; exit 1; }
  MODE_VALUE="$(cat "$MODE_PATH")"
  case "$MODE_VALUE" in
    off|notify|auto-safe|maintenance-window) ;;
    emergency-stop)
      echo "Automatic maintenance paused by owner emergency-stop"
      exit 0
      ;;
    *)
      echo "Automatic maintenance blocked by invalid policy"
      exit 1
      ;;
  esac
fi

STARTED_AT="$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
RUN_ID="$(date -u +'%Y%m%dT%H%M%SZ')"
STATUS="نجاح"
FAILURES=()
STEPS=()
BRIDGE_UPDATE_JSON='{"status":"not_checked"}'
BRIDGE_UPDATE_STATUS="not_checked"
AGENT_MAINTENANCE_JSON='{"status":"not_run"}'
AGENT_MAINTENANCE_STATUS="not_run"

record_step() {
  local label="$1"
  shift
  printf '[%s] %s\n' "$(date -Is)" "$label"
  if "$@"; then
    STEPS+=("✅ ${label}")
  else
    local code=$?
    STATUS="نجاح جزئي"
    FAILURES+=("${label} (رمز الخروج ${code})")
    STEPS+=("⚠️ ${label}")
  fi
}

run_as_bridge_user() {
  runuser -u "$BRIDGE_USER" -- env \
    XDG_RUNTIME_DIR="$USER_RUNTIME_DIR" \
    DBUS_SESSION_BUS_ADDRESS="unix:path=${USER_RUNTIME_DIR}/bus" \
    "$@"
}

bridge_self_update() {
  [[ -x "$PYTHON_BIN" ]] || { echo "prepared Python environment is missing"; return 1; }
  local output
  if ! output="$(run_as_bridge_user "$PYTHON_BIN" "${BRIDGE_DIR}/maintenance/self_update.py")"; then
    BRIDGE_UPDATE_JSON="${output:-{\"status\":\"error\"}}"
    BRIDGE_UPDATE_STATUS="error"
    printf '%s\n' "$BRIDGE_UPDATE_JSON"
    return 1
  fi
  BRIDGE_UPDATE_JSON="$output"
  BRIDGE_UPDATE_STATUS="$(printf '%s' "$output" | "$PYTHON_BIN" -c 'import json,sys; print(json.load(sys.stdin).get("status","unknown"))' 2>/dev/null || echo unknown)"
  printf '%s\n' "$BRIDGE_UPDATE_JSON"
  case "$BRIDGE_UPDATE_STATUS" in
    updated|up_to_date|available) return 0 ;;
    skipped) return 0 ;;
    *) return 2 ;;
  esac
}

refresh_deployment_assets() {
  case "$BRIDGE_UPDATE_STATUS" in
    updated|up_to_date) ;;
    *)
      echo "deployment asset refresh skipped because repository state is not trusted: $BRIDGE_UPDATE_STATUS"
      return 0
      ;;
  esac
  bash "${BRIDGE_DIR}/maintenance/install-root-assets.sh"
  rm -f "${RUNTIME_DIR}/root-assets-stale"
  run_as_bridge_user "$PYTHON_BIN" "${BRIDGE_DIR}/systemd.py"
}

restart_opencode_after_update() {
  [[ "$BRIDGE_UPDATE_STATUS" == "updated" ]] || return 0
  run_as_bridge_user systemctl --user restart opencode-serve.service
  # The daily agent task runs immediately after this step, so the restart is
  # only complete once the server answers its own health endpoint. A fixed
  # sleep used to race the start-up and failed the agent task with
  # RemoteProtocolError even though the update itself had succeeded.
  run_as_bridge_user "$PYTHON_BIN" "${BRIDGE_DIR}/wait_for_opencode.py"
}

run_daily_agent_maintenance() {
  [[ -x "$PYTHON_BIN" ]] || return 1
  local output
  output="$(run_as_bridge_user "$PYTHON_BIN" "${BRIDGE_DIR}/maintenance/daily_agent_maintenance.py")" || {
    AGENT_MAINTENANCE_JSON="${output:-{\"status\":\"error\"}}"
    printf '%s\n' "$AGENT_MAINTENANCE_JSON"
    return 1
  }
  AGENT_MAINTENANCE_JSON="$output"
  AGENT_MAINTENANCE_STATUS="$(printf '%s' "$output" | "$PYTHON_BIN" -c 'import json,sys; print(json.load(sys.stdin).get("status","unknown"))' 2>/dev/null || echo unknown)"
  printf '%s\n' "$AGENT_MAINTENANCE_JSON"
}

activate_telegram_after_maintenance() {
  if [[ "$BRIDGE_UPDATE_STATUS" != "updated" && "$AGENT_MAINTENANCE_STATUS" != "success" ]]; then
    return 0
  fi
  run_as_bridge_user systemctl --user restart opencode-bridge-telegram.service
  if [[ "$BRIDGE_UPDATE_STATUS" == "updated" ]]; then
    run_as_bridge_user bash -c 'git -C "$1" rev-parse HEAD > "$1/runtime/deployed-ref"' _ "$BRIDGE_DIR"
  fi
}

apt_update() {
  env DEBIAN_FRONTEND=noninteractive apt-get "${APT_OPTIONS[@]}" update
}

apt_upgrade() {
  env DEBIAN_FRONTEND=noninteractive apt-get "${APT_OPTIONS[@]}" -y upgrade
}

apt_autoremove_preview() {
  # Do not automatically purge packages that may be required by a local service.
  env DEBIAN_FRONTEND=noninteractive apt-get "${APT_OPTIONS[@]}" -s autoremove
}

apt_clean() {
  apt-get clean
}

cleanup_temporary_files() {
  systemd-tmpfiles --clean
}

cleanup_journal() {
  journalctl --vacuum-time=14d
}

resolve_retention_minutes() {
  local days="${ATTACHMENT_RETENTION_DEFAULT_DAYS}"
  if [[ -r "$ATTACHMENT_RETENTION_DAYS_FILE" ]]; then
    local raw
    # Strip surrounding whitespace only. A malformed value must fall back to the
    # default instead of being sanitized into a different, unexpected window.
    raw="$(tr -d '[:space:]' <"$ATTACHMENT_RETENTION_DAYS_FILE" 2>/dev/null || true)"
    if [[ "$raw" =~ ^[0-9]+$ ]] && (( raw >= 1 && raw <= 365 )); then
      days="$raw"
    else
      echo "قيمة مدة الاحتفاظ غير صالحة (${raw:-فارغة})؛ استخدام الافتراضي ${ATTACHMENT_RETENTION_DEFAULT_DAYS} يوم." >&2
    fi
  fi
  echo $(( days * 24 * 60 ))
}

cleanup_managed_attachments() {
  # Deferred work may outlive retention. Keep files if queue state is unreadable.
  local active_tasks
  active_tasks="$(run_as_bridge_user "$PYTHON_BIN" -c 'import sqlite3,sys; db=sqlite3.connect("file:" + sys.argv[1] + "?mode=ro", uri=True); print(db.execute("SELECT COUNT(*) FROM agent_tasks WHERE status IN (?,?,?,?,?)", ("queued","leased","running","retrying","scheduled")).fetchone()[0])' "${BRIDGE_DIR}/sessions.db")" || return 1
  if [[ "$active_tasks" != "0" ]]; then
    ATTACHMENT_CLEANUP_SUMMARY="محفوظة لوجود مهام غير مكتملة"
    echo "Attachment retention deferred: unfinished tasks still need managed files."
    return 0
  fi
  local retention_minutes
  retention_minutes="$(resolve_retention_minutes)"
  ATTACHMENT_CLEANUP_SUMMARY="0 ملف (0B)"
  [[ -d "$ATTACHMENT_ROOT" ]] || return 0
  local stale_files stale_bytes
  stale_files="$(find "$ATTACHMENT_ROOT" -xdev -type f -mmin +"$retention_minutes" -printf '.' 2>/dev/null | wc -c | tr -d ' ')"
  stale_bytes="$(find "$ATTACHMENT_ROOT" -xdev -type f -mmin +"$retention_minutes" -printf '%s\n' 2>/dev/null | awk '{total += $1} END {print total + 0}')"
  find "$ATTACHMENT_ROOT" -xdev -depth -type f -mmin +"$retention_minutes" -delete
  find "$ATTACHMENT_ROOT" -xdev -depth -mindepth 2 -type d -empty -mmin +"$retention_minutes" -delete
  ATTACHMENT_CLEANUP_SUMMARY="${stale_files} ملف (${stale_bytes}B)"
}

prune_expired_backups() {
  # The bridge deletes snapshots and release archives older than three days,
  # together with their metadata.  This runs even when no new backup was made.
  run_as_bridge_user "$PYTHON_BIN" -c 'import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); from bridge.services.admin_backups import AdminBackups; print(AdminBackups(Path(sys.argv[1])).prune_older_than(3))' "${BRIDGE_DIR}"
}

# Privileged host updates require explicit consent; missing, invalid and
# symlinked switches are never permission to modify the operating system.
if [[ -f "${RUNTIME_DIR}/host-auto-update-enabled" ]] &&
   [[ ! -L "${RUNTIME_DIR}/host-auto-update-enabled" ]] &&
   [[ "$(cat "${RUNTIME_DIR}/host-auto-update-enabled")" == "1" ]]; then
  record_step "تحديث فهارس الحزم" apt_update
  record_step "تثبيت تحديثات الحزم الآمنة" apt_upgrade
  record_step "معاينة الحزم غير المستخدمة دون حذفها" apt_autoremove_preview
  record_step "تنظيف ذاكرة حزم APT" apt_clean
else
  STEPS+=("⏸ Automatic operating-system updates disabled by owner")
fi
record_step "تنظيف الملفات المؤقتة وفق سياسة النظام" cleanup_temporary_files
record_step "الاحتفاظ بسجل النظام لآخر 14 يومًا" cleanup_journal
record_step "حذف مرفقات البوت المدارة وفق مدة الاحتفاظ المختارة" cleanup_managed_attachments
record_step "فحص GitHub وتطبيق تحديث OpenCode Bridge الموثق" bridge_self_update
record_step "حذف النسخ الاحتياطية الأقدم من 3 أيام" prune_expired_backups
if [[ "$BRIDGE_UPDATE_STATUS" == "updated" && -f "$RUNTIME_DIR/purge-backups-after-update" ]] &&
   [[ "$(cat "$RUNTIME_DIR/purge-backups-after-update")" == "1" ]]; then
  record_step "حذف النسخ الاحتياطية بعد نجاح التحديث والتحقق" \
    run_as_bridge_user "$PYTHON_BIN" -c 'import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); from bridge.services.admin_backups import AdminBackups; print(AdminBackups(Path(sys.argv[1])).purge_after_verified_update())' "${BRIDGE_DIR}"
fi
record_step "مزامنة ملفات systemd والصيانة من النسخة الحالية" refresh_deployment_assets
record_step "إعادة تشغيل OpenCode بعد تحديث الكود فقط" restart_opencode_after_update
record_step "تشغيل مهمة الوكيل اليومية واختيار أقوى نموذج مجاني" run_daily_agent_maintenance
record_step "تفعيل الكود والنموذج اليومي في جسر تيليجرام" activate_telegram_after_maintenance

REBOOT_REQUIRED="لا"
[[ -f /var/run/reboot-required ]] && REBOOT_REQUIRED="نعم — سيُطلب التأكيد عبر حارس إعادة التشغيل"
DISK_SUMMARY="$(df -h / | awk 'NR==2 {print $3 " مستخدم من " $2 " (" $5 ")"}')"
UPGRADABLE_LEFT="$(apt list --upgradable 2>/dev/null | tail -n +2 | wc -l | tr -d ' ')"
COMPLETED_AT="$(date -u +'%Y-%m-%dT%H:%M:%SZ')"

REPORT_TMP="$(mktemp "${RUNTIME_DIR}/.maintenance-${RUN_ID}.XXXXXX")"
{
  printf '# تقرير الصيانة اليومية\n\n'
  printf '| البند | القيمة |\n|---|---|\n'
  printf '| معرّف التنفيذ | `%s` |\n' "$RUN_ID"
  printf '| البداية (UTC) | %s |\n' "$STARTED_AT"
  printf '| النهاية (UTC) | %s |\n' "$COMPLETED_AT"
  printf '| الحالة | **%s** |\n' "$STATUS"
  printf '| مساحة القرص | %s |\n' "$DISK_SUMMARY"
  printf '| تحديثات متبقية | %s |\n' "$UPGRADABLE_LEFT"
  printf '| إعادة تشغيل مطلوبة | %s |\n' "$REBOOT_REQUIRED"
  printf '| مرفقات البوت المحذوفة (أقدم من 7 أيام) | %s |\n' "$ATTACHMENT_CLEANUP_SUMMARY"
  printf '| تحديث المستودع | `%s` |\n' "$BRIDGE_UPDATE_STATUS"
  printf '| نتيجة مهمة الوكيل | `%s` |\n\n' "$(printf '%s' "$AGENT_MAINTENANCE_JSON" | tr '\n' ' ' | cut -c1-300)"
  printf '## الخطوات المنفذة\n\n'
  printf '%s\n' "${STEPS[@]}"
  if (( ${#FAILURES[@]} > 0 )); then
    printf '\n## ملاحظات تحتاج متابعة\n\n'
    printf '%s\n' "${FAILURES[@]}"
  fi
  printf '\n> تحديث البرنامج ذاتي وآمن: fetch للوسوم من المستودع الموثوق فقط، اختيار أحدث release tag مستقر، تحقق كامل في worktree مؤقت، ثم تفعيل commit الإصدار بوضع detached release. التعديلات tracked أو التعارض مع ملفات untracked يوقفان التحديث، ولا يوجد force/reset/clean. تحديث النظام يستخدم APT فقط، ولا ينفّذ بناء مشاريع أو تثبيت اعتماديات تطبيقات. إعادة تشغيل النظام تمر حصريًا عبر حارس التأكيد.\n'
} >"$REPORT_TMP"

install -m 640 -o ubuntu -g ubuntu "$REPORT_TMP" "$REPORT_PATH"
install -m 640 -o ubuntu -g ubuntu "$REPORT_TMP" "${HISTORY_DIR}/${RUN_ID}.md"
rm -f "$REPORT_TMP"

# Routine maintenance is deliberately silent. A reboot requirement is the
# only event that may trigger an owner-facing request, handled independently.
if [[ -f /var/run/reboot-required ]]; then
  systemctl start --no-block opencode-bridge-reboot-guard.service
  printf '[%s] reboot guard started\n' "$(date -Is)"
fi

printf '[%s] maintenance completed: %s\n' "$(date -Is)" "$STATUS"
[[ "$STATUS" == "نجاح" ]] || exit 2
