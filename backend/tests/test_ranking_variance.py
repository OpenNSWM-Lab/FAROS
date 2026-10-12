"""
Test: Ranking produces non-degenerate scores.

Verifies that the heuristic ranking service produces varied scores
across multiple candidates — i.e., not all candidates get the same score.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.models.idea import IdeaCandidate, DraftPlan, RiskItem, ExperimentSpec
from app.services.ranking_service import PAPER_TYPE_WEIGHTS, RankingService
from datetime import datetime


def _make_candidate(idx: int, session_id: str = "test_session") -> IdeaCandidate:
    """Create a distinct candidate for testing."""
    templates = [
        {
            "title": "Scalable Graph Neural Networks with Sparse Attention",
            "problem": "Current GNN methods fail to scale to billion-node graphs due to quadratic attention.",
            "keyInsight": "Sparse locality-sensitive hashing can reduce attention complexity to O(n log n).",
            "risks": [RiskItem(risk="May lose long-range edges", mitigation="Hybrid attention for top-k global")],
            "experiments": [ExperimentSpec(name="OGB-Products", description="Benchmark on OGB", metrics=["accuracy", "throughput"], datasets=["ogbn-products"])],
            "refs": ["ref_a1", "ref_a2", "ref_a3"],
        },
        {
            "title": "Self-Supervised Pre-training for Low-Resource NLP",
            "problem": "Low-resource languages lack labeled data for fine-tuning.",
            "keyInsight": "Cross-lingual masked language modeling transfers representations.",
            "risks": [],
            "experiments": [],
            "refs": ["ref_b1"],
        },
        {
            "title": "Interpretable Reinforcement Learning via Concept Bottlenecks",
            "problem": "RL policies are opaque black boxes, preventing deployment in safety-critical domains.",
            "keyInsight": "Concept bottleneck layers inserted into the policy network provide human-understandable explanations.",
            "risks": [RiskItem(risk="Concept annotations expensive", mitigation="Use weak supervision")],
            "experiments": [ExperimentSpec(name="Atari benchmark", description="Evaluate on Atari games", metrics=["reward", "interpretability_score"], datasets=["atari"])],
            "refs": ["ref_c1", "ref_c2"],
        },
        {
            "title": "Federated Learning with Differential Privacy Guarantees",
            "problem": "Federated learning is vulnerable to gradient inversion attacks.",
            "keyInsight": "Combining local differential privacy with secure aggregation provides formal privacy guarantees.",
            "risks": [RiskItem(risk="Utility-privacy tradeoff", mitigation="Adaptive noise calibration")],
            "experiments": [
                ExperimentSpec(name="CIFAR-10 FL", description="Federated CIFAR-10", metrics=["accuracy", "privacy_budget"], datasets=["cifar10"]),
                ExperimentSpec(name="Medical FL", description="Federated medical imaging", metrics=["AUC", "privacy_budget"], datasets=["chestxray"]),
            ],
            "refs": ["ref_d1", "ref_d2", "ref_d3", "ref_d4"],
        },
        {
            "title": "Neural Architecture Search for Edge Devices",
            "problem": "Deploying large neural networks on edge devices is infeasible.",
            "keyInsight": "Hardware-aware NAS with latency predictor finds Pareto-optimal architectures.",
            "risks": [],
            "experiments": [ExperimentSpec(name="ImageNet-Mobile", description="Mobile classification", metrics=["accuracy", "latency_ms"], datasets=["imagenet"])],
            "refs": ["ref_e1", "ref_e2"],
        },
        {
            "title": "Causal Discovery in Time Series Data",
            "problem": "Correlation-based methods produce spurious relationships in temporal data.",
            "keyInsight": "Granger causality combined with attention-based temporal models identifies true causal links.",
            "risks": [RiskItem(risk="Confounders not fully controlled", mitigation="Include latent variable modeling")],
            "experiments": [],
            "refs": ["ref_f1"],
        },
    ]

    t = templates[idx % len(templates)]
    return IdeaCandidate(
        id=f"cand_test_{idx:04d}",
        sessionId=session_id,
        title=t["title"],
        problem=t["problem"],
        keyInsight=t["keyInsight"],
        novelty=5.0,
        feasibility=5.0,
        impact=5.0,
        scoringMethod="pending",
        risks=t["risks"],
        requiredExperiments=t["experiments"],
        references=t["refs"],
        draftPlan=DraftPlan(
            researchQuestion=t["problem"],
            hypothesis=t["keyInsight"],
            methodology="To be defined",
        ),
        createdAt=datetime.utcnow(),
    )


def test_parse_embedded_nested_json_response():
    """LLM ranking JSON may be wrapped in explanatory prose."""
    service = RankingService()
    response = """
    Here is the ranking result:
    {
      "novelty": {"score": 9.0, "rationale": "Fresh direction"},
      "feasibility": {"score": 8.0, "rationale": "Buildable"},
      "impact": {"score": 7.0, "rationale": "Useful"},
      "clarity": {"score": 6.0, "rationale": "Clear enough"},
      "risk": {"score": 5.0, "rationale": "Manageable"},
      "alignment": {"score": 4.0, "rationale": "Partial fit"},
      "referenceSupport": {"score": 3.0, "rationale": "Sparse refs"},
      "experimentSpecificity": {"score": 2.0, "rationale": "Needs detail"},
      "overallRationale": "Good candidate",
      "confidence": 0.9
    }
    """

    result = service._parse_llm_response("cand_embedded", response, PAPER_TYPE_WEIGHTS["default"])

    assert result.criteria["novelty"].value == 9.0
    assert result.criteria["experimentSpecificity"].value == 2.0
    assert result.overallRationale == "Good candidate"
    assert result.confidence == 0.9
    print("PASS: test_parse_embedded_nested_json_response")


def test_heuristic_scores_are_not_all_equal():
    """Scores must vary across 6 distinct candidates (heuristic fallback)."""
    service = RankingService()
    candidates = [_make_candidate(i) for i in range(6)]

    updated, results = service.rank_candidates(
        candidates=candidates,
        seed_query="graph neural networks",
        paper_type="algorithm",
        domain="machine learning",
        provider_name="__test_skip__",  # Will fail LLM, fall back to heuristic
        model="test",
        session_id="test_session",
    )

    scores = [c.overallScore for c in updated]
    unique_scores = set(round(s, 2) for s in scores)

    print(f"Scores: {[round(s, 2) for s in scores]}")
    print(f"Unique: {len(unique_scores)}, Min: {min(scores):.2f}, Max: {max(scores):.2f}")
    print(f"Std dev: {(sum((s - sum(scores)/len(scores))**2 for s in scores) / len(scores)) ** 0.5:.3f}")

    assert len(unique_scores) >= 3, f"Expected ≥3 distinct scores among 6 candidates, got {len(unique_scores)}: {scores}"

    # Verify breakdown is populated
    for c in updated:
        bd = c.scoreBreakdown
        assert len(bd) == 8, f"Expected 8 criteria in breakdown, got {len(bd)}"
        for key, entry in bd.items():
            assert "value" in entry, f"Missing 'value' in breakdown[{key}]"
            assert 0 <= entry["value"] <= 10, f"Score out of range: {key}={entry['value']}"

    # Verify scoring method is set
    for c in updated:
        assert c.scoringMethod != "pending", f"Candidate {c.id} still has pending scoring"

    print("PASS: test_heuristic_scores_are_not_all_equal")


def test_overall_score_uses_all_8_criteria():
    """overallScore must reflect all 8 criteria, not just 3."""
    c = IdeaCandidate(
        id="cand_test_weight",
        sessionId="test",
        title="Test",
        problem="Test problem",
        keyInsight="Test insight",
        novelty=10.0,
        feasibility=10.0,
        impact=10.0,
        clarity=0.0,
        risk=0.0,
        alignment=0.0,
        referenceSupport=0.0,
        experimentSpecificity=0.0,
        scoringMethod="test",
    )

    # If only novelty/feasibility/impact mattered, score would be 10.0
    # With all 8 criteria (some at 0), score must be < 10.0
    assert c.overallScore < 10.0, f"overallScore={c.overallScore} should be < 10 when 5 criteria are 0"
    # Expected: 10*0.20 + 10*0.20 + 10*0.20 + 0*0.10 + 0*0.10 + 0*0.10 + 0*0.05 + 0*0.05 = 6.0
    assert abs(c.overallScore - 6.0) < 0.01, f"overallScore={c.overallScore}, expected 6.0"
    print("PASS: test_overall_score_uses_all_8_criteria")


def test_score_breakdown_structure():
    """scoreBreakdown must contain all 8 criteria with value and rationale."""
    c = IdeaCandidate(
        id="cand_test_bd",
        sessionId="test",
        title="Test",
        problem="Test problem",
        keyInsight="Test insight",
        novelty=7.5,
        noveltyRationale="Good novelty",
        feasibility=8.0,
        feasibilityRationale="Very feasible",
        impact=6.5,
        impactRationale="Moderate impact",
        clarity=7.0,
        clarityRationale="Clear",
        risk=6.0,
        riskRationale="Some risk",
        alignment=8.5,
        alignmentRationale="Well aligned",
        referenceSupport=5.5,
        referenceSupportRationale="Few refs",
        experimentSpecificity=7.0,
        experimentSpecificityRationale="Decent experiments",
    )

    bd = c.scoreBreakdown
    expected_keys = {"novelty", "feasibility", "impact", "clarity", "risk",
                     "alignment", "referenceSupport", "experimentSpecificity"}
    assert set(bd.keys()) == expected_keys, f"Unexpected keys: {set(bd.keys())}"

    assert bd["novelty"]["value"] == 7.5
    assert bd["novelty"]["rationale"] == "Good novelty"
    assert bd["alignment"]["value"] == 8.5
    print("PASS: test_score_breakdown_structure")


if __name__ == "__main__":
    test_parse_embedded_nested_json_response()
    test_overall_score_uses_all_8_criteria()
    test_score_breakdown_structure()
    test_heuristic_scores_are_not_all_equal()
    print("\n=== ALL RANKING TESTS PASSED ===")
