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

run_py python -m pytest tests/test_faros_smoke.py -k "package_trust_validation_endpoint or install_skill_endpoint_rejects_untrusted_package_with_structured_error or package_dependencies_endpoint_returns_graph_summary or package_dependency_resolution_endpoint_returns_resolution_summary or skill_registry_loads_disk_manifests or verifier_registry_can_validate_install_and_refresh_package or skill_registry_can_install_and_refresh_local_package or skill_registry_rejects_signature_mismatch_when_trust_is_enforced or skill_registry_rejects_untrusted_package_when_policy_is_enforced or skill_registry_install_rejects_duplicate_without_overwrite or agent_registry_can_install_and_refresh_local_package or agent_registry_install_rejects_duplicate_without_overwrite or blueprint_registry_can_install_and_refresh_local_package or blueprint_registry_install_rejects_duplicate_without_overwrite or skill_registry_downgrade_guard_and_audit_log or agent_registry_uninstall_and_audit_log or blueprint_registry_upgrade_and_audit_log or package_audit_api_returns_events or skill_registry_rejects_incompatible_profile_version or agent_registry_accepts_compatible_profile_and_skill_versions or blueprint_registry_rejects_incompatible_profile_version or dependency_report_returns_structured_issue_details or dependency_report_exposes_unsatisfiable_constraint_set or skill_registry_upgrade_rolls_back_when_dependency_graph_breaks or skill_registry_can_rollback_to_previous_backup" -q
