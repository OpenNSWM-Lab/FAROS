#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="${FAROS_CODEGEN_TEST_IMAGE:-faros/codegen-test:3.12}"

docker build \
  --file "$ROOT_DIR/backend/docker/codegen-test.Dockerfile" \
  --tag "$IMAGE" \
  "$ROOT_DIR/backend/docker"

printf 'Built isolated code-generation test image: %s\n' "$IMAGE"
