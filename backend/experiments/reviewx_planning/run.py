"""Compare Qwen planning with frozen policy checks on real SciFact diagnostics."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.llm.provider_client import ChatMessage, get_provider_client


MODEL = "qwen3.7-plus-2026-05-26"
SCHEMA = "reviewx-planning-benchmark/v1"


SCENARIOS = [
    {"id": "balanced", "objective": "F1-Score", "direction": "max", "constraints": {"Precision": [">=", 0.66], "Brier Score": ["<=", 0.2191], "AUROC": [">=", 0.685]}, "nearBest": 0.005, "tieBreak": ["Expected Calibration Error (ECE)"]},
    {"id": "precision_first", "objective": "Precision", "direction": "max", "constraints": {"Recall": [">=", 0.70], "AUROC": [">=", 0.685]}, "nearBest": 0.0, "tieBreak": ["Brier Score"]},
    {"id": "recall_guarded", "objective": "Recall", "direction": "max", "constraints": {"Precision": [">=", 0.66], "Brier Score": ["<=", 0.2191], "AUROC": [">=", 0.685]}, "nearBest": 0.0, "tieBreak": ["Expected Calibration Error (ECE)"]},
    {"id": "calibration_first", "objective": "Expected Calibration Error (ECE)", "direction": "min", "constraints": {"F1-Score": [">=", 0.75], "Brier Score": ["<=", 0.2191], "AUROC": [">=", 0.685]}, "nearBest": 0.0, "tieBreak": ["F1-Score"]},
    {"id": "ranking_strict", "objective": "F1-Score", "direction": "max", "constraints": {"Precision": [">=", 0.66], "AUROC": [">=", 0.6872]}, "nearBest": 0.0, "tieBreak": ["Expected Calibration Error (ECE)"]},
    {"id": "low_brier", "objective": "Brier Score", "direction": "min", "constraints": {"F1-Score": [">=", 0.69], "Precision": [">=", 0.66]}, "nearBest": 0.0, "tieBreak": ["Expected Calibration Error (ECE)"]},
]


def _hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def candidate_satisfies(metrics: dict[str, float], scenario: dict[str, Any]) -> bool:
    for metric, (operator, threshold) in scenario["constraints"].items():
        value = metrics[metric]
        if operator == ">=" and value < threshold:
            return False
        if operator == "<=" and value > threshold:
            return False
    return True


def select_by_policy(candidates: dict[str, Any], scenario: dict[str, Any]) -> str:
    feasible = [
        candidate_id for candidate_id, item in candidates.items()
        if candidate_satisfies(item["metrics"], scenario)
    ]
    if not feasible:
        raise ValueError(f"Scenario {scenario['id']} has no feasible candidate")
    objective = scenario["objective"]
    reverse = scenario["direction"] == "max"
    optimum = (max if reverse else min)(candidates[item]["metrics"][objective] for item in feasible)
    tolerance = float(scenario.get("nearBest") or 0.0)
    near_best = [
        item for item in feasible
        if abs(candidates[item]["metrics"][objective] - optimum) <= tolerance
    ]
    tie_break = list(scenario.get("tieBreak") or [])
    return min(near_best, key=lambda item: tuple(
        candidates[item]["metrics"][metric] for metric in tie_break
    ) + (item,))


def parse_qwen_selections(text: str) -> dict[str, str]:
    payload = json.loads(text)
    values = payload.get("selections") or []
    return {
        str(item.get("scenarioId")): str(item.get("candidateId"))
        for item in values if item.get("scenarioId") and item.get("candidateId")
    }


def score_method(
    selections: dict[str, str],
    candidates: dict[str, Any],
    expected: dict[str, str],
) -> dict[str, Any]:
    rows = []
    for scenario in SCENARIOS:
        scenario_id = scenario["id"]
        selected = selections.get(scenario_id)
        exists = selected in candidates
        satisfies = bool(exists and candidate_satisfies(candidates[selected]["metrics"], scenario))
        rows.append({
            "scenarioId": scenario_id,
            "selectedCandidateId": selected,
            "expectedCandidateId": expected[scenario_id],
            "executable": exists,
            "constraintsSatisfied": satisfies,
            "policyAgreement": selected == expected[scenario_id],
        })
    total = len(rows)
    return {
        "cases": total,
        "planExecutabilityRate": sum(item["executable"] for item in rows) / total,
        "constraintSatisfactionRate": sum(item["constraintsSatisfied"] for item in rows) / total,
        "policyAgreementRate": sum(item["policyAgreement"] for item in rows) / total,
        "rows": rows,
    }


def run_benchmark(
    candidate_path: Path,
    output_dir: Path,
    *,
    model: str = MODEL,
    seed: int = 20260826,
) -> dict[str, Any]:
    candidates = json.loads(candidate_path.read_text(encoding="utf-8"))
    expected = {scenario["id"]: select_by_policy(candidates, scenario) for scenario in SCENARIOS}
    prompt_payload = {"candidates": candidates, "scenarios": SCENARIOS}
    prompt = (
        "Select exactly one candidate for each scenario. Obey every numeric constraint and objective. "
        "Return JSON only as {\"selections\":[{\"scenarioId\":...,\"candidateId\":...,\"rationale\":...}]}.\n"
        + json.dumps(prompt_payload, ensure_ascii=False, sort_keys=True)
    )
    response = get_provider_client("qwen").chat(
        [ChatMessage(role="system", content="You are a scientific experiment planner."), ChatMessage(role="user", content=prompt)],
        model=model,
        temperature=0.0,
        max_tokens=1800,
        structured_output=True,
        seed=seed,
    )
    qwen = parse_qwen_selections(response.text)
    repaired = {
        scenario["id"]: (
            qwen.get(scenario["id"])
            if qwen.get(scenario["id"]) == expected[scenario["id"]]
            else expected[scenario["id"]]
        )
        for scenario in SCENARIOS
    }
    qwen_score = score_method(qwen, candidates, expected)
    rules_score = score_method(expected, candidates, expected)
    reviewed_score = score_method(repaired, candidates, expected)
    repair_count = sum(qwen.get(key) != value for key, value in expected.items())
    summary = {
        "schemaVersion": SCHEMA,
        "runId": f"reviewx_planning_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}",
        "createdAt": datetime.now(UTC).isoformat(),
        "dataSource": str(candidate_path),
        "dataHash": _hash(candidates),
        "scenarioHash": _hash(SCENARIOS),
        "scenarioCount": len(SCENARIOS),
        "seed": seed,
        "methods": {
            "qwen_one_shot": qwen_score,
            "frozen_rules": rules_score,
            "qwen_reviewx": {**reviewed_score, "repairs": repair_count, "repairRate": repair_count / len(SCENARIOS)},
            "qwen_reviewx_human_gate": {
                **reviewed_score,
                "humanConditionCompletionRate": None,
                "officialPublicationRate": None,
                "status": "pending_real_human_review",
            },
        },
        "qwenTrace": {
            "provider": response.raw_provider,
            "model": response.model,
            "usage": response.usage,
            "latencyMs": response.latency_ms,
            "promptHash": _hash(prompt_payload),
            "responseHash": _hash(response.text),
        },
        "limitations": [
            "Controlled policy-decision benchmark derived from one real SciFact candidate set.",
            "ReviewX repair enforces a frozen policy and should not be interpreted as independent scientific discovery.",
            "No human performance value is imputed before real reviewers complete the conditions.",
        ],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "qwen_response.json").write_text(response.text, encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--seed", type=int, default=20260826)
    args = parser.parse_args()
    summary = run_benchmark(args.candidates, args.output_dir, model=args.model, seed=args.seed)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
