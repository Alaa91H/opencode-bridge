#!/usr/bin/env bash
# Create a tested semantic-version release and deploy its immutable Git tag.
set -Eeuo pipefail

readonly BRIDGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$BRIDGE_DIR"

if [[ $# -ne 2 ]]; then
  echo "الاستخدام: scripts/release.sh رقم_الإصدار \"ملخص الإصدار\"" >&2
  exit 64
fi

VERSION="$1"
SUMMARY="$2"
TAG="v${VERSION}"

if [[ ! "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "رقم الإصدار يجب أن يتبع النسخ الدلالية مثل 1.0.1." >&2
  exit 64
fi
if [[ -z "$SUMMARY" ]]; then
  echo "ملخص الإصدار مطلوب." >&2
  exit 64
fi

if [[ "$(git branch --show-current)" != "main" ]]; then
  echo "أنشئ الإصدار من فرع main فقط. نفّذ: git checkout main" >&2
  exit 1
fi
if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "توجد تغييرات غير ملتزم بها. راجعها قبل إنشاء الإصدار." >&2
  exit 1
fi
if git rev-parse -q --verify "refs/tags/${TAG}" >/dev/null; then
  echo "الوسم موجود مسبقًا: ${TAG}" >&2
  exit 1
fi

PREVIOUS_TAG="$(git describe --tags --abbrev=0 2>/dev/null || true)"
printf '%s\n' "$VERSION" > VERSION
{
  printf '## [%s] - %s\n\n' "$VERSION" "$(date -u +%Y-%m-%d)"
  printf '### ملخص\n\n%s\n\n' "$SUMMARY"
  printf '### التغييرات منذ %s\n\n' "${PREVIOUS_TAG:-بداية المستودع}"
  if [[ -n "$PREVIOUS_TAG" ]]; then
    git log --pretty=format:'- %s (%h)' "${PREVIOUS_TAG}..HEAD" || true
  else
    git log --pretty=format:'- %s (%h)' HEAD || true
  fi
  printf '\n\n'
} > /tmp/opencode-bridge-changelog-entry.md
if [[ -f CHANGELOG.md ]]; then
  cat /tmp/opencode-bridge-changelog-entry.md CHANGELOG.md > /tmp/opencode-bridge-changelog.md
else
  {
    printf '# سجل التغييرات\n\n'
    cat /tmp/opencode-bridge-changelog-entry.md
  } > /tmp/opencode-bridge-changelog.md
fi
install -m 0644 /tmp/opencode-bridge-changelog.md CHANGELOG.md
rm -f /tmp/opencode-bridge-changelog-entry.md /tmp/opencode-bridge-changelog.md

scripts/verify.sh
git add VERSION CHANGELOG.md
git commit -m "release: ${TAG}"
git tag -a "$TAG" -m "$SUMMARY"
scripts/deploy.sh "$TAG"

echo "release=passed tag=${TAG}"
