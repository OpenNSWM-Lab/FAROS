import json
import hashlib
from pathlib import Path

import numpy as np
import pytest

from experiments.reviewx_oscillator.run import (
    _candidate_payload,
    _qwen_select,
    analytic_free_decay,
    baseline_design,
    candidate_designs,
    execute_protocol,
    recompute_and_validate_output,
    simulate_trajectory,
    statistical_gate,
    validate_design,
)
from app.modules.review.competition_evidence import build_oscillator_evidence_view


CONFIG_PATH = Path(__file__).parents[1] / "experiments" / "reviewx_oscillator" / "config" / "frozen_protocol.json"


def _config():
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def test_oscillator_solver_matches_reference():
    times = np.linspace(0, 12, 121)
    numerical = simulate_trajectory(
        1.2,
        0.1,
        1.0,
        times,
        amplitude=0.0,
        initial_state=(1.0, 0.0),
    )
    analytic = analytic_free_decay(1.2, 0.1, times)
    assert np.max(np.abs(numerical - analytic)) < 1e-8


def test_oscillator_budget_is_matched():
    config = _config()
    first = baseline_design(config)
    for candidate in candidate_designs(config):
        validate_design(candidate, config)
        assert candidate.observation_budget == first.observation_budget == 80
        assert sum(candidate.sample_allocations) == 80
        assert candidate.optimizer_max_nfev == first.optimizer_max_nfev


def test_holdout_is_absent_from_qwen_prompt():
    config = _config()
    diagnostics = {
        "fisherInformationConditionNumber": 10.0,
        "diagnosis": ["poor transient coverage"],
        "finalHoldoutIncluded": False,
    }
    selected, trace, prompt = _qwen_select(
        diagnostics,
        _candidate_payload(config),
        provider="deterministic",
        model="none",
        require_real_api=False,
    )
    assert selected == "adaptive_resonance_transient"
    assert trace["finalHoldoutExposedToQwen"] is False
    assert trace["isRealApiCall"] is False
    for seed in config["finalHoldoutSeeds"]:
        assert str(seed) not in prompt


@pytest.fixture(scope="module")
def protocol_output(tmp_path_factory):
    config = _config()
    config["developmentSeeds"] = config["developmentSeeds"][:6]
    config["calibrationSeeds"] = config["calibrationSeeds"][:3]
    config["finalHoldoutSeeds"] = list(range(99001, 99007))
    config["bootstrapSamples"] = 300
    output = tmp_path_factory.mktemp("oscillator") / "run"
    result = execute_protocol(config, output, provider="deterministic")
    return output, result


def test_metrics_recompute_from_per_seed_records(protocol_output):
    output, result = protocol_output
    recomputed = recompute_and_validate_output(output)
    assert recomputed["round1Mean"] == pytest.approx(result["statistics"]["round1Mean"])
    assert (output / "final_holdout" / "per_seed_results.csv").is_file()
    assert (output / "qwen_trace.json").is_file()
    assert json.loads((output / "human_signoff.json").read_text())["status"] == "pending"
    evidence = build_oscillator_evidence_view(output)
    assert evidence["available"] is True
    assert evidence["eligibleForHeadline"] is False
    assert evidence["checks"]["realQwenCall"] is False


def test_verified_real_qwen_bundle_can_become_headline(protocol_output):
    output, _ = protocol_output
    trace_path = output / "qwen_trace.json"
    manifest_path = output / "manifest.json"
    trace_backup = trace_path.read_bytes()
    manifest_backup = manifest_path.read_bytes()
    checksums_path = output / "CHECKSUMS.sha256"
    checksums_backup = checksums_path.read_bytes()
    try:
        trace = json.loads(trace_path.read_text())
        trace.update({
            "isRealApiCall": True,
            "provider": "dashscope",
            "model": "qwen-test",
            "latencyMs": 10,
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        })
        trace_path.write_text(json.dumps(trace, indent=2) + "\n")
        manifest = json.loads(manifest_path.read_text())
        manifest["qwenRealApiCall"] = True
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
        files = sorted(
            path for path in output.rglob("*")
            if path.is_file() and path.name != "CHECKSUMS.sha256"
        )
        checksums_path.write_text("\n".join(
            f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(output).as_posix()}"
            for path in files
        ) + "\n")
        evidence = build_oscillator_evidence_view(output)
        assert evidence["eligibleForHeadline"] is True
        assert evidence["primaryMetric"]["relativeImprovement"] >= 0.15
        assert evidence["qwen"]["finalHoldoutExposed"] is False
    finally:
        trace_path.write_bytes(trace_backup)
        manifest_path.write_bytes(manifest_backup)
        checksums_path.write_bytes(checksums_backup)


def test_missing_metrics_prevents_evidence_construction(protocol_output):
    output, _ = protocol_output
    metrics_path = output / "round_2" / "metrics.json"
    backup = metrics_path.read_bytes()
    metrics_path.unlink()
    try:
        with pytest.raises(FileNotFoundError, match="missing artifacts"):
            recompute_and_validate_output(output)
    finally:
        metrics_path.write_bytes(backup)


def test_plan_delta_changes_only_declared_design_fields(protocol_output):
    output, _ = protocol_output
    delta = json.loads((output / "plan_delta.json").read_text())
    fields = {item["field"] for item in delta["parameterChanges"]}
    assert fields == {"excitationFrequencies", "samplingStrategy"}
    assert delta["unchangedHardConstraints"] == {
        "observationBudget": 80,
        "optimizerMaxNfev": 80,
    }
    assert delta["finalHoldoutExposedToQwen"] is False
    comparison = json.loads((output / "method_comparison.json").read_text())
    assert comparison["partition"] == "calibration"
    assert comparison["matchedBudget"]["passed"] is True
    assert comparison["finalHoldoutUsed"] is False
    assert (output / "calibration" / "method_comparison_per_seed.csv").is_file()
    frozen_gate = json.loads((output / "calibration" / "frozen_gate.json").read_text())
    assert frozen_gate["thresholdsChangedAfterCalibration"] is False


def test_statistical_gate_keeps_nonsignificant_result():
    result = statistical_gate(
        [0.2, 0.21, 0.19, 0.2],
        [0.2, 0.21, 0.19, 0.2],
        bootstrap_samples=500,
        bootstrap_seed=1,
    )
    assert result["ciCrossesZero"] is True
    assert result["decision"] == "BOUNDARY"
