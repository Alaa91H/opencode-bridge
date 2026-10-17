#!/usr/bin/env bash
# Publish the accumulated working tree as one verified release: commit, push,
# wait for CI, tag, deploy, and publish the GitHub release.
#
# This exists because the build workspace has no Git history and no git binary,
# so the whole cumulative changeset has to be committed in one step from a real
# checkout. It never forces, never amends, and never pushes a tag before the
# commit it belongs to has passed CI.
#
# Usage:
#   scripts/publish-cumulative.sh <version> "<release summary>" [--skip-ci] [--yes]
set -Eeuo pipefail

readonly PROGRAM="${0##*/}"
BRIDGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly BRIDGE_DIR
cd "$BRIDGE_DIR"

SKIP_CI=0
ASSUME_YES=0
VERSION=""
SUMMARY=""
COMMIT_MESSAGE=""

log() { printf '%s: %s\n' "$PROGRAM" "$*"; }
fail() { printf '%s: %s\n' "$PROGRAM" "$*" >&2; exit 1; }

usage() {
  cat >&2 <<'USAGE'
الاستخدام: scripts/publish-cumulative.sh <version> "<summary>" [--skip-ci] [--yes]

  --skip-ci   ادفع الالتزامات ثم توقّف قبل الوسم (لمتابعة CI يدويًا)
  --yes       لا تطلب تأكيدًا (لصدفة غير تفاعلية)
USAGE
  exit 64
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-ci) SKIP_CI=1 ;;
    --yes) ASSUME_YES=1 ;;
    -h|--help) usage ;;
    -*) usage ;;
    *)
      if [[ -z "$VERSION" ]]; then
        VERSION="$1"
      elif [[ -z "$SUMMARY" ]]; then
        SUMMARY="$1"
      elif [[ -z "$COMMIT_MESSAGE" ]]; then
        COMMIT_MESSAGE="$1"
      else
        usage
      fi
      ;;
  esac
  shift
done

[[ -n "$VERSION" && -n "$SUMMARY" ]] || usage
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail "رقم الإصدار يجب أن يكون 1.0.1"
[[ -d .git ]] || fail "هذه ليست نسخة git؛ نفّذ السكربت داخل المستودع على الخادم"
[[ "$(git branch --show-current)" == "main" ]] || fail "أنشئ الإصدار من فرع main فقط"

# Scratch file produced by the build workspace tooling; never part of a release.
if [[ -f CHANGELOG_HEAD.tmp ]]; then
  rm -f CHANGELOG_HEAD.tmp
  log "أُزيل CHANGELOG_HEAD.tmp"
fi

log "المستودع:"
git remote -v | sed 's/^/  /'

if git diff --quiet && git diff --cached --quiet; then
  log "لا توجد تغييرات غير ملتزم بها"
else
  git status --short | sed 's/^/  /'
  if [[ "$ASSUME_YES" -eq 0 ]]; then
    [[ -t 0 ]] || fail "التأكيد مطلوب والطرفية ليست تفاعلية؛ أعد التشغيل مع --yes"
    read -r -p "  committing all of the above as one changeset؟ [y/N] " reply
    [[ "$reply" == "y" || "$reply" == "Y" ]] || fail "أُلغي بناءً على طلبك"
  fi
  git add -A
  git commit -q -m "${COMMIT_MESSAGE:-feat: cumulative bridge cycles ${VERSION}}"
  log "commit=$(git rev-parse --short HEAD)"
fi

log "بوابة الجودة المحلية قبل الدفع"
scripts/verify.sh

log "دفع main"
git push origin main

if [[ "$SKIP_CI" -eq 1 ]]; then
  log "‏--skip-ci: توقّف قبل الوسم؛ راقب Actions ثم أعد التشغيل لإتمام الإصدار"
  exit 0
fi

if ! command -v gh >/dev/null 2>&1; then
  log "‏gh غير متاح: راقب CI يدويًا ثم أعد التشغيل لإتمام الوسم"
  exit 0
fi

log "انتظار بوابة الجودة على main"
attempt=0
while [[ "$attempt" -lt 60 ]]; do
  status="$(gh run list --branch main --limit 1 --json conclusion,status \
    --jq '.[0] | "\(.status):\(.conclusion // "pending")"' 2>/dev/null || printf 'unknown')"
  case "$status" in
    completed:success)
      log "CI: نجاح"
      break
      ;;
    completed:*)
      fail "CI فشل ($status)؛ لم يُنشر أي وسم. راجع: gh run view <id> --log-failed"
      ;;
    *)
      attempt=$((attempt + 1))
      sleep 10
      ;;
  esac
  [[ "$attempt" -lt 60 ]] || fail "انتهت مهلة انتظار CI؛ لم يُنشر أي وسم"
done

log "scripts/release.sh ${VERSION}"
scripts/release.sh "$VERSION" "$SUMMARY"

log "دفع الالتزامات والوسم"
git push origin main --follow-tags
log "تم: https://github.com/Alaa91H/opencode-bridge/releases/tag/v${VERSION}"