#!/usr/bin/env bash
# Return to a previously deployed, version-tagged Git release.
# deploy.sh intentionally accepts tags, not arbitrary commits. The recorded
# previous SHA must therefore be resolved to the exact matching release tag.
set -Eeuo pipefail

BRIDGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly BRIDGE_DIR
readonly PREVIOUS_DEPLOYED_REF_FILE="${BRIDGE_DIR}/runtime/previous-deployed-ref"
readonly DEPLOYED_REF_FILE="${BRIDGE_DIR}/runtime/deployed-ref"
readonly TAG_PATTERN='^v[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?$'
readonly SHA_PATTERN='^[0-9a-f]{40}$'

cd "$BRIDGE_DIR"

if [[ $# -gt 1 ]]; then
  echo "الاستخدام: scripts/rollback.sh [وسم_إصدار_أو_SHA_كامل]" >&2
  exit 64
fi

if [[ $# -eq 1 ]]; then
  TARGET_REF="$1"
else
  if [[ ! -s "$PREVIOUS_DEPLOYED_REF_FILE" ]]; then
    echo "لا يوجد إصدار سابق مسجل؛ لا يمكن إجراء تراجع تلقائي." >&2
    exit 1
  fi
  TARGET_REF="$(cat "$PREVIOUS_DEPLOYED_REF_FILE")"
fi

# Accept only an exact full commit hash or a version tag. Do not allow git
# revision expressions, branches, short hashes, or user-controlled paths.
if [[ "$TARGET_REF" =~ $TAG_PATTERN ]]; then
  TARGET_COMMIT="$(git rev-parse --verify "refs/tags/${TARGET_REF}^{commit}" 2>/dev/null)" || {
    echo "وسم الإصدار غير موجود: $TARGET_REF" >&2
    exit 1
  }
elif [[ "$TARGET_REF" =~ $SHA_PATTERN ]]; then
  TARGET_COMMIT="$(git rev-parse --verify "${TARGET_REF}^{commit}" 2>/dev/null)" || {
    echo "المرجع غير متوفر محليًا: $TARGET_REF" >&2
    exit 1
  }
else
  echo "المرجع يجب أن يكون وسم إصدار صالحًا أو SHA كاملًا من 40 خانة." >&2
  exit 64
fi

# A version tag is the only legal deployment identity. Validate its exact
# VERSION file and ensure that it points to the requested commit, not merely
# another commit with the same version.
TARGET_VERSION="$(git show "${TARGET_COMMIT}:VERSION" 2>/dev/null)" || {
  echo "الإصدار المختار لا يحتوي ملف VERSION." >&2
  exit 1
}
TARGET_TAG="v${TARGET_VERSION}"
if ! [[ "$TARGET_TAG" =~ $TAG_PATTERN ]]; then
  echo "رقم VERSION للإصدار السابق غير صالح." >&2
  exit 1
fi
TAG_COMMIT="$(git rev-parse --verify "refs/tags/${TARGET_TAG}^{commit}" 2>/dev/null)" || {
  echo "لا يوجد وسم مطابق للإصدار السابق: $TARGET_TAG" >&2
  exit 1
}
if [[ "$TAG_COMMIT" != "$TARGET_COMMIT" ]]; then
  echo "وسم $TARGET_TAG لا يشير إلى SHA المطلوب؛ إيقاف التراجع." >&2
  exit 1
fi
if [[ "$TARGET_REF" =~ $TAG_PATTERN && "$TARGET_REF" != "$TARGET_TAG" ]]; then
  echo "وسم الإصدار لا يتطابق مع VERSION." >&2
  exit 1
fi

CURRENT_COMMIT="$(git rev-parse --verify HEAD)"
if [[ -s "$DEPLOYED_REF_FILE" ]]; then
  RECORDED_COMMIT="$(cat "$DEPLOYED_REF_FILE")"
  if [[ "$RECORDED_COMMIT" =~ $SHA_PATTERN ]] && git cat-file -e "${RECORDED_COMMIT}^{commit}" 2>/dev/null; then
    CURRENT_COMMIT="$RECORDED_COMMIT"
  fi
fi

if [[ "$TARGET_COMMIT" == "$CURRENT_COMMIT" ]]; then
  echo "التراجع غير مطلوب؛ الإصدار المنشور يطابق ${TARGET_COMMIT:0:12}."
  exit 0
fi

echo "الرجوع من ${CURRENT_COMMIT:0:12} إلى $TARGET_TAG (${TARGET_COMMIT:0:12})."
exec scripts/deploy.sh "$TARGET_TAG"
