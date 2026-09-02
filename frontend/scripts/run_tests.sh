#!/usr/bin/env bash
set -euo pipefail

# Vitest 1.x can leave its worker pool open on Node 22 when a larger UI suite
# completes in one process. Keep watch-mode behavior unchanged, but run bounded
# batches for deterministic one-shot CI/release runs.
if [[ " ${*:-} " == *" --run "* ]]; then
  shopt -s globstar nullglob
  test_files=(src/**/*.test.ts src/**/*.test.tsx)
  run_args=()
  for arg in "$@"; do
    [[ "$arg" == "--run" ]] || run_args+=("$arg")
  done
  batch_size=8
  for ((offset = 0; offset < ${#test_files[@]}; offset += batch_size)); do
    ./node_modules/.bin/vitest --run "${run_args[@]}" "${test_files[@]:offset:batch_size}"
  done
else
  exec ./node_modules/.bin/vitest "$@"
fi
