#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"

run_py() {
  if command -v conda >/dev/null 2>&1 && { [ -d "$HOME/anaconda3/envs/aist" ] || [ -d "$HOME/miniconda3/envs/aist" ]; }; then
    conda run --no-capture-output -n aist "$@"
  else
    "$@"
  fi
}

cd "$BACKEND_DIR"

run_py python -m pytest tests/test_faros_smoke.py -k "routes_are_mounted or metadata_endpoints_return_assets or preflight_returns_passed_result or run_detail_returns_structured_runtime_view or state_store_rejects_invalid or retry_endpoint or replay_endpoint or dependency_aware_resume_and_skip_runtime_flow or run_memory_query_endpoint_filters_payload or run_memory_recall_endpoint_restores_archived_scope or run_memory_endpoint_returns_envelope or plan_run_can_be_created_without_provider_execution or hybrid_profile_preflight_returns_passed_result" -q
