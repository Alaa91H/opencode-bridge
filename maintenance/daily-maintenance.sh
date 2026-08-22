#!/usr/bin/env bash
# Managed daily maintenance for the OpenCode Telegram Bridge host.
# It never notifies external channels for routine work and never deletes user files.
# The sole deletion exception is bridge-managed attachment artifacts older than seven days.
set -Eeuo pipefail

readonly BRIDGE_DIR="/home/ubuntu/opencode-bridge"
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
  printf '| مرفقات البوت المحذوفة (أقدم من 7 أيام) | %s |\n\n' "$ATTACHMENT_CLEANUP_SUMMARY"
  printf '## الخطوات المنفذة\n\n'
  printf '%s\n' "${STEPS[@]}"
  if (( ${#FAILURES[@]} > 0 )); then
    printf '\n## ملاحظات تحتاج متابعة\n\n'
    printf '%s\n' "${FAILURES[@]}"
  fi
  printf '\n> لا ينفّذ هذا السكربت بناء مشاريع، أو تثبيت اعتماديات تطبيقات، أو حذف ملفات مستخدمين أو ملفات مشروع. الاستثناء الوحيد هو مرفقات البوت المدارة داخل runtime/attachments بعد مرور 7 أيام. إعادة التشغيل، عند الحاجة، تمر حصريًا عبر حارس منفصل يطلب التأكيد ثم يتحقق من خلو الطابور.\n'
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
