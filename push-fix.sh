#!/usr/bin/env bash
# Commit and push the section-label and budget fix from this host.
set -Eeuo pipefail

REPO_DIR=/home/ubuntu/bridge-push
cd "$REPO_DIR"

git config user.name "Alaa91H"
git config user.email "143001551+Alaa91H@users.noreply.github.com"
gh auth setup-git

git add -A
if git diff --cached --quiet; then
  echo "RESULT: nothing staged"
  exit 1
fi

git commit -q -m "fix: keep section labels with their first item and raise the digest budget (v3.46.1)"
git push origin main
printf 'RESULT: pushed %s\n' "$(git log -1 --format=%H)"
printf 'RESULT: version %s\n' "$(cat VERSION)"
