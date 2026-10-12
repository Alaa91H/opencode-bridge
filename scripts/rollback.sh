#!/usr/bin/env bash
# Restore a previously published release using the same guarded deployment path.
# deploy.sh accepts release tags, not raw commit IDs.
set -Eeuo pipefail

BRIDGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly BRIDGE_DIR
readonly PREVIOUS_DEPLOYED_REF_FILE="${BRIDGE_DIR}/runtime/previous-deployed-ref"
readonly RELEASE_TAG_PATTERN='^v[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?$'

cd "$BRIDGE_DIR"

if [[ $# -gt 1 ]]; then
  echo "Usage: scripts/rollback.sh [published release tag or commit]" >&2
  exit 64
fi

if [[ $# -eq 1 ]]; then
  TARGET_REF="$1"
else
  if [[ ! -s "$PREVIOUS_DEPLOYED_REF_FILE" ]]; then
    echo "No recorded previous deployment; pass a published release tag or commit." >&2
    exit 1
  fi
  TARGET_REF="$(tr -d '\r\n' < "$PREVIOUS_DEPLOYED_REF_FILE")"
fi

# Reject option injection and revision operators: only ordinary refs or SHAs.
if [[ ! "$TARGET_REF" =~ ^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$ ]]; then
  echo "Invalid rollback reference." >&2
  exit 64
fi

if ! TARGET_COMMIT="$(git rev-parse --verify --quiet "${TARGET_REF}^{commit}")"; then
  echo "Rollback reference does not resolve to a local commit: $TARGET_REF" >&2
  exit 66
fi
if ! TARGET_VERSION="$(git show "${TARGET_COMMIT}:VERSION" | tr -d '\r\n')"; then
  echo "Rollback commit does not contain VERSION." >&2
  exit 65
fi
TARGET_TAG="v${TARGET_VERSION}"
if ! [[ "$TARGET_TAG" =~ $RELEASE_TAG_PATTERN ]]; then
  echo "Rollback commit has an invalid published version: $TARGET_VERSION" >&2
  exit 65
fi

# Require the matching local release tag and disallow a different commit with
# the same version. No remote access is needed in an emergency rollback.
if ! TAG_COMMIT="$(git rev-parse --verify --quiet "refs/tags/${TARGET_TAG}^{commit}")"; then
  echo "Cannot roll back: release tag $TARGET_TAG is not available locally." >&2
  exit 66
fi
if [[ "$TAG_COMMIT" != "$TARGET_COMMIT" ]]; then
  echo "Cannot roll back: tag $TARGET_TAG points to a different commit." >&2
  exit 65
fi

CURRENT_COMMIT="$(git rev-parse HEAD)"
if [[ "$TARGET_COMMIT" == "$CURRENT_COMMIT" ]]; then
  echo "Already at release $TARGET_TAG (${TARGET_COMMIT:0:12})."
  exit 0
fi

echo "Restoring verified release $TARGET_TAG (${TARGET_COMMIT:0:12})."
# Replace any inherited update identity so deploy.sh validates this exact tag.
export OPENCODE_EXPECTED_RELEASE_COMMIT="$TARGET_COMMIT"
exec "${BRIDGE_DIR}/scripts/deploy.sh" "$TARGET_TAG"
