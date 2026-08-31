#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="${FAROS_CODEGEN_GPU_IMAGE:-faros/codegen-gpu:cuda12.4}"

docker build \
  --file "$ROOT_DIR/backend/docker/codegen-gpu.Dockerfile" \
  --tag "$IMAGE" \
  "$ROOT_DIR/backend/docker"

printf 'Built isolated GPU experiment image: %s\n' "$IMAGE"
