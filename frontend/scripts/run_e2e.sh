#!/usr/bin/env bash
set -euo pipefail

export NO_PROXY="127.0.0.1,localhost"
export no_proxy="$NO_PROXY"

./node_modules/.bin/vite --host 127.0.0.1 --port 4173 --strictPort &
vite_pid=$!
cleanup() {
  kill "$vite_pid" 2>/dev/null || true
  wait "$vite_pid" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

for _ in {1..40}; do
  if curl --noproxy '*' --fail --silent --max-time 2 http://127.0.0.1:4173/ >/dev/null; then
    ./node_modules/.bin/playwright test "$@"
    exit $?
  fi
  if ! kill -0 "$vite_pid" 2>/dev/null; then
    printf 'Vite E2E server exited before becoming ready.\n' >&2
    exit 1
  fi
  sleep 0.25
done

printf 'Vite E2E server did not become ready within 10 seconds.\n' >&2
exit 1
