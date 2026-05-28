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

run_py python -m pytest tests/test_faros_smoke.py -k "providers_endpoint_returns_metadata or artifact_schema_endpoint_returns_contracts or agent_executor_uses_tool_provider_owned_execution_for_paper or agent_executor_uses_execution_file_backend_for_experiment or agent_executor_uses_tool_command_backend_for_paper or agent_executor_uses_human_file_backend_for_review or agent_executor_uses_execution_workspace_file_backend_for_experiment or agent_executor_uses_human_approval_file_backend_for_review or agent_executor_uses_execution_queue_file_backend_for_experiment or agent_executor_uses_human_approval_queue_backend_for_review or agent_executor_uses_human_provider_owned_execution_for_review or agent_executor_wraps_capability_execution_metadata or verifier_dispatch_fails_invalid_artifact_schema or verifier_dispatch_fails_missing_artifact_contract or verifier_dispatch_supports_policy_packs_and_review_plugin" -q
