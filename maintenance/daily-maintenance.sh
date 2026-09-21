#!/usr/bin/env bash
# Managed daily maintenance for the OpenCode Telegram Bridge host.
# It never notifies external channels for routine work and never deletes user files.
# The sole deletion exception is bridge-managed attachment artifacts older than seven days.
set -Eeuo pipefail

readonly BRIDGE_DIR="/home/ubuntu/opencode-bridge"
readonly BRIDGE_USER="ubuntu"
readonly BRIDGE_UID="$(id -u "$BRIDGE_USER")"
readonly USER_RUNTIME_DIR="/run/user/${BRIDGE_UID}"
readonly PYTHON_BIN="${BRIDGE_DIR}/venv/bin/python"
readonly RUNTIME_DIR="${BRIDGE_DIR}/runtime"
readonly REPORT_PATH="${RUNTIME_DIR}/maintenance-latest.md"
readonly HISTORY_DIR="${RUNTIME_DIR}/maintenance-history"
readonly ATTACHMENT_ROOT="${RUNTIME_DIR}/attachments"
readonly ATTACHMENT_RETENTION_MINUTES=10080
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
    updated|up_to_date) return 0 ;;
    skipped) return 3 ;;
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
  run_as_bridge_user "$PYTHON_BIN" "${BRIDGE_DIR}/systemd.py"
}

restart_opencode_after_update() {
  [[ "$BRIDGE_UPDATE_STATUS" == "updated" ]] || return 0
  run_as_bridge_user systemctl --user restart opencode-serve.service
  sleep 3
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

apt_autoremove() {
  env DEBIAN_FRONTEND=noninteractive apt-get "${APT_OPTIONS[@]}" -y autoremove --purge
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

cleanup_managed_attachments() {
  ATTACHMENT_CLEANUP_SUMMARY="0 ملف (0B)"
  [[ -d "$ATTACHMENT_ROOT" ]] || return 0
  local stale_files stale_bytes
  stale_files="$(find "$ATTACHMENT_ROOT" -xdev -type f -mmin +"$ATTACHMENT_RETENTION_MINUTES" -printf '.' 2>/dev/null | wc -c | tr -d ' ')"
  stale_bytes="$(find "$ATTACHMENT_ROOT" -xdev -type f -mmin +"$ATTACHMENT_RETENTION_MINUTES" -printf '%s\n' 2>/dev/null | awk '{total += $1} END {print total + 0}')"
  find "$ATTACHMENT_ROOT" -xdev -depth -type f -mmin +"$ATTACHMENT_RETENTION_MINUTES" -delete
  find "$ATTACHMENT_ROOT" -xdev -depth -mindepth 2 -type d -empty -mmin +"$ATTACHMENT_RETENTION_MINUTES" -delete
  ATTACHMENT_CLEANUP_SUMMARY="${stale_files} ملف (${stale_bytes}B)"
}

record_step "تحديث فهارس الحزم" apt_update
record_step "تثبيت تحديثات الحزم الآمنة" apt_upgrade
record_step "إزالة الحزم غير المطلوبة" apt_autoremove
record_step "تنظيف ذاكرة حزم APT" apt_clean
record_step "تنظيف الملفات المؤقتة وفق سياسة النظام" cleanup_temporary_files
record_step "الاحتفاظ بسجل النظام لآخر 14 يومًا" cleanup_journal
record_step "حذف مرفقات البوت المدارة الأقدم من 7 أيام" cleanup_managed_attachments
record_step "فحص GitHub وتطبيق تحديث OpenCode Bridge الموثق" bridge_self_update
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
  printf '\n> تحديث البرنامج ذاتي وآمن: fetch من المستودع الموثوق فقط، تحقق كامل في worktree مؤقت، ثم fast-forward فقط إذا كان الفرع نظيفًا وغير متشعب. لا يوجد force/reset. تحديث النظام يستخدم APT فقط، ولا ينفّذ بناء مشاريع أو تثبيت اعتماديات تطبيقات. إعادة تشغيل النظام تمر حصريًا عبر حارس التأكيد.\n'
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
