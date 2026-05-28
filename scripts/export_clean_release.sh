#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${1:-$ROOT_DIR/../FAROS-release}"

rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR"

rsync -a   --exclude '.git'   --exclude '.pytest_cache'   --exclude '__pycache__'   --exclude '*.pyc'   --exclude '.DS_Store'   --exclude 'frontend/node_modules'   --exclude 'frontend/dist'   --exclude 'backend/.pytest_cache'   --exclude 'backend/data/faros/runs'   --exclude 'backend/data/*.db'   --exclude 'backend/data/**/*.db'   --exclude 'backend/data/faros/packages/trust_keyring.json'   "$ROOT_DIR/" "$OUT_DIR/"

printf 'Exported clean FAROS release to: %s
' "$OUT_DIR"
