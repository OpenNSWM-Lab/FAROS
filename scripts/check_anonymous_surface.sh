#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SEARCH_PATHS=("$ROOT_DIR/frontend/src" "$ROOT_DIR/backend/app")
if [[ -d "$ROOT_DIR/frontend/dist" ]]; then
  SEARCH_PATHS+=("$ROOT_DIR/frontend/dist")
fi

mapfile -t PUBLIC_DOCUMENTS < <(
  find "$ROOT_DIR/frontend/public" -type f \
    \( -iname '*.pdf' -o -iname '*.ppt' -o -iname '*.pptx' -o -iname '*.doc' -o -iname '*.docx' \) \
    -print | sort
)
if [[ ${#PUBLIC_DOCUMENTS[@]} -gt 0 ]]; then
  printf 'Anonymous surface check failed. Public office documents require explicit redaction review:\n' >&2
  printf '  %s\n' "${PUBLIC_DOCUMENTS[@]}" >&2
  exit 1
fi

PATTERNS=(
  '华中科技大学|网络空间安全学院'
  '(^|[^A-Za-z0-9_.-])/(home|Users|data)/[A-Za-z0-9._-]+|C:\\Users\\[A-Za-z0-9._-]+'
  '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.(com|cn|org|net|edu)'
  'gh[pousr]_[A-Za-z0-9]{12,}|sk-[A-Za-z0-9]{12,}'
  'https?://[^[:space:]/]+:[^[:space:]@]+@'
)
if [[ -n "${FAROS_ANON_EXTRA_PATTERN:-}" ]]; then
  PATTERNS+=("$FAROS_ANON_EXTRA_PATTERN")
fi

ARGS=(-n -i --glob '!**/*.map')
for pattern in "${PATTERNS[@]}"; do
  ARGS+=(-e "$pattern")
done

set +e
MATCHES="$(rg "${ARGS[@]}" "${SEARCH_PATHS[@]}" 2>&1)"
STATUS=$?
set -e
if [[ $STATUS -gt 1 ]]; then
  printf '%s\n' "$MATCHES" >&2
  exit "$STATUS"
fi
if [[ $STATUS -eq 0 ]]; then
  printf 'Anonymous surface check failed. Remove or explicitly review these public artifacts:\n%s\n' "$MATCHES" >&2
  exit 1
fi

printf 'Anonymous surface check passed.\n'
