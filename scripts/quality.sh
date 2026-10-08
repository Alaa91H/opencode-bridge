#!/usr/bin/env bash
# Strict code-hygiene gate.
#
# Runs in CI and on demand. Every blocking check must pass. A check that cannot
# fail the build is a report, not a gate, so the blocking path contains no
# exit-zero fallback operators. CI greps this file for that pattern, which is why
# even the comments above avoid writing it literally.
#
# The production venv is locked to three runtime packages, so these tools are
# development-only and never installed on the host.
set -uo pipefail

BRIDGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$BRIDGE_DIR" || exit 1

failures=0
declare -a FAILED=()

step() {
  local name="$1"
  shift
  printf '\n=== %s ===\n' "$name"
  if "$@"; then
    printf 'PASS  %s\n' "$name"
  else
    printf 'FAIL  %s\n' "$name"
    FAILED+=("$name")
    failures=1
  fi
}

have() { command -v "$1" >/dev/null 2>&1; }

# ------------------------------------------------------------------ required

if ! have ruff; then
  echo "ruff is required for the quality gate: pip install ruff" >&2
  exit 2
fi

# ---------------------------------------------------------------------- lint

# This is the blocking core: pyflakes, bugbear, bandit, pyupgrade, and the ruff
# correctness rules. Every rule that can hide a defect is enabled; the style
# exclusions live in ruff.toml, each with a written reason.
step "ruff lint" ruff check . --output-format=concise

# NOTE: `ruff format --check` is deliberately NOT in this gate. The tree is not
# ruff-format formatted (175 files differ) and adopting it would bury the defect
# fixes in a whitespace-only diff. It belongs in its own commit.

# ---------------------------------------------------------------- type checks

# mypy is REPORT-ONLY, and labelled as such. The tree has ~80 pre-existing type
# errors that are a typing cleanup in their own right, not runtime defects.
# Reporting the exact count every run keeps it visible and stops it from being
# forgotten, without pretending the tree is clean when it is not.
if have mypy; then
  printf '\n=== mypy type check (REPORT ONLY - not yet green) ===\n'
  mypy_out="$(mktemp)"
  # mypy is expected to exit non-zero on this tree, so the status is captured
  # and printed rather than swallowed. The gate never branches on it.
  mypy_status=0
  mypy --ignore-missing-imports --no-error-summary bridge >"$mypy_out" 2>&1 || mypy_status=$?
  # grep -c exits 1 when it matches nothing, yet still prints the count 0, which
  # is the answer this report needs. Its status is therefore also recorded.
  grep_status=0
  mypy_errors="$(grep -cE ': error:' "$mypy_out")" || grep_status=$?
  printf 'mypy_exit=%s grep_exit=%s mypy_errors=%s (tracked, not blocking yet)\n' \
    "$mypy_status" "$grep_status" "$mypy_errors"
  if [[ "$mypy_errors" -gt 0 ]]; then
    printf 'top error codes:\n'
    grep -oE '\[[a-z-]+\]$' "$mypy_out" | sort | uniq -c | sort -rn | head -8
  fi
  rm -f "$mypy_out"
else
  printf 'SKIP  mypy (not installed)\n'
fi

# ---------------------------------------------------------------- dead code

# Imports only. Vulture's "unused variable" check reports every parameter of a
# Protocol method as dead, because a Protocol declares the interface and never
# calls the body. That is a false-positive class, not a finding, so the gate
# asserts on the half of vulture that is unambiguous.
vulture_dead_imports() {
  local out
  # vulture exits non-zero whenever it reports anything, so a non-zero status
  # here is expected and must not abort the gate. Only the filtered output
  # decides the result; the script runs without errexit, so the status of the
  # assignment carries no further meaning.
  out="$(vulture bridge bot.py --min-confidence 90 2>&1 | grep 'unused import')"
  if [[ -n "$out" ]]; then
    printf '%s\n' "$out"
    return 1
  fi
  return 0
}

if have vulture; then
  step "vulture dead imports" vulture_dead_imports
else
  printf 'SKIP  vulture (not installed)\n'
fi

# ------------------------------------------------------------------- shells

shellcheck_all() {
  local status=0 file
  for file in start.sh scripts/*.sh maintenance/*.sh; do
    [[ -f "$file" ]] || continue
    printf -- '--- %s\n' "$file"
    shellcheck -S warning "$file" || status=1
  done
  return "$status"
}

if have shellcheck; then
  step "shellcheck every shell script" shellcheck_all
else
  printf 'SKIP  shellcheck (not installed)\n'
fi

# ------------------------------------------------------------------- hygiene

no_debug_markers() {
  local status=0
  if grep -rnE '\b(pdb\.set_trace|breakpoint\(\))' --include='*.py' bridge bot.py 2>/dev/null; then
    status=1
  fi
  if grep -rnE '^\s*print\(.*DEBUG' --include='*.py' bridge 2>/dev/null; then
    status=1
  fi
  return "$status"
}

no_secrets() {
  local status=0
  if grep -rnE 'BEGIN [A-Z ]*PRIVATE KEY' --include='*.py' --include='*.sh' --include='*.json' . 2>/dev/null; then
    status=1
  fi
  if grep -rnE '[0-9]{8,10}:[A-Za-z0-9_-]{35}' --include='*.py' --include='*.sh' \
       --include='*.json' --include='*.md' . 2>/dev/null; then
    status=1
  fi
  return "$status"
}

no_conflict_markers() {
  local status=0
  if grep -rnE '^(<<<<<<< |>>>>>>> )' --include='*' . 2>/dev/null | grep -v '\.git/'; then
    status=1
  fi
  return "$status"
}

no_crlf_in_scripts() {
  local status=0 file
  for file in start.sh scripts/*.sh maintenance/*.sh; do
    [[ -f "$file" ]] || continue
    if grep -qU $'\r' "$file"; then
      printf 'CRLF line endings: %s\n' "$file" >&2
      status=1
    fi
  done
  return "$status"
}

version_is_semantic() {
  local version
  version="$(tr -d '[:space:]' < VERSION)"
  [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ || "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+-rc\.[0-9]+$ ]]
}

requirements_are_pinned() {
  local status=0 line value
  while read -r line; do
    line="${line%%#*}"
    line="$(printf '%s' "$line" | tr -d '[:space:]')"
    [[ -z "$line" ]] && continue
    if [[ "$line" != *"=="* ]]; then
      printf 'not an exact pin: %s\n' "$line" >&2
      status=1
      continue
    fi
    value="${line##*==}"
    if [[ ! "$value" =~ ^[0-9]+(\.[0-9]+){1,2}$ ]]; then
      printf 'unstable pin: %s\n' "$line" >&2
      status=1
    fi
  done < requirements.txt
  return "$status"
}

step "no debug markers" no_debug_markers
step "no committed secrets or bot tokens" no_secrets
step "no merge conflict residue" no_conflict_markers
step "shell scripts are LF" no_crlf_in_scripts
step "VERSION is a semantic version" version_is_semantic
step "runtime requirements are exactly pinned" requirements_are_pinned

# --------------------------------------------------------------------- result

printf '\n'
if [[ $failures -eq 0 ]]; then
  echo "quality=PASS"
else
  echo "quality=FAIL"
  for name in "${FAILED[@]}"; do
    printf '  failed: %s\n' "$name"
  done
fi
exit $failures
