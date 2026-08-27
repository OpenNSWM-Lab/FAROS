from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from experiments.reviewx_eval.analyze_peerqa_alignment_proxy import (
    analyze,
    attach_protocol_audit,
    exact_mcnemar_p,
    sha256_file,
)


class PeerQAAlignmentProxyTests(unittest.TestCase):
    def test_reports_paired_effects_and_passes_complete_quality_gate(self) -> None:
        comparison = []
        predictions = []
        coverage = {
            "method_a": [False, True],
            "method_b": [True, True],
        }
        scores = {
            "method_a": [0.10, 0.20],
            "method_b": [0.20, 0.30],
        }
        for method in ("method_a", "method_b"):
            for index, sample_id in enumerate(("paper_1", "paper_2")):
                comparison.append({
                    "annotationId": f"{method}_{index}",
                    "comparisonPairId": f"pair_{index}",
                    "method": method,
                    "sampleId": sample_id,
                    "selectedRepetition": 0,
                    "findingId": "finding" if coverage[method][index] else "NO_MATCH",
                    "automaticCoverageCandidate": coverage[method][index],
                    "automaticMatchScore": scores[method][index],
                })
                predictions.append({
                    "method": method,
                    "sampleId": sample_id,
                    "runnerRepetition": 0,
                    "runnerElapsedMs": 100,
                    "status": "completed",
                    "findings": [{"id": "finding"}],
                    "modelTrace": {
                        "estimatedTokenCost": 100,
                        "budgetExceeded": False,
                        "llmCalls": [] if method == "method_a" and index == 0 else [{"model": "qwen"}],
                    },
                })
        summary = analyze(
            comparison,
            predictions,
            split="held_out",
            threshold=0.12,
            seed=7,
            iterations=200,
            expected_repetitions=1,
            max_total_tokens=4000,
        )
        self.assertEqual(summary["qualityGate"]["status"], "passed")
        self.assertEqual(summary["paperCount"], 2)
        self.assertEqual(summary["questionCount"], 2)
        pair = summary["pairwise"][0]
        self.assertEqual(pair["candidateRateDelta"], 0.5)
        self.assertEqual(pair["meanBestMatchScoreDelta"], 0.1)
        efficiency = summary["efficiencyPairwise"][0]
        self.assertTrue(efficiency["complete"])
        self.assertEqual(efficiency["llmCallDelta"]["mean"], 0.5)
        first_quality = next(item for item in summary["methods"] if item["method"] == "method_a")["runQuality"]
        self.assertEqual(first_quality["llmEscalationRate"], 0.5)
        self.assertEqual(first_quality["localOnlyRunCount"], 1)
        self.assertFalse(summary["reportingBoundary"]["headlineEligibleAsExpertRecall"])

    def test_gate_fails_when_runs_are_missing_or_over_budget(self) -> None:
        comparison = [
            {
                "annotationId": method,
                "comparisonPairId": "pair_1",
                "method": method,
                "sampleId": "paper_1",
                "selectedRepetition": 0,
                "findingId": "finding",
                "automaticCoverageCandidate": True,
                "automaticMatchScore": 0.2,
            }
            for method in ("method_a", "method_b")
        ]
        predictions = [{
            "method": "method_a",
            "sampleId": "paper_1",
            "runnerRepetition": 0,
            "status": "completed",
            "modelTrace": {"estimatedTokenCost": 5000},
        }]
        summary = analyze(
            comparison,
            predictions,
            split="held_out",
            threshold=0.12,
            seed=7,
            iterations=20,
            expected_repetitions=1,
            max_total_tokens=4000,
        )
        self.assertEqual(summary["qualityGate"]["status"], "failed")
        self.assertFalse(summary["qualityGate"]["checks"]["allExpectedRunsPresent"])
        self.assertFalse(summary["qualityGate"]["checks"]["tokenBudgetRespected"])

    def test_exact_mcnemar_is_two_sided(self) -> None:
        self.assertEqual(exact_mcnemar_p(0, 0), 1.0)
        self.assertAlmostEqual(exact_mcnemar_p(0, 5), 0.0625)

    def test_frozen_protocol_audit_verifies_counts_methods_and_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            locked = Path(directory) / "samples.jsonl"
            locked.write_text("{}\n", encoding="utf-8")
            protocol_path = Path(directory) / "protocol.json"
            protocol_path.write_text(json.dumps({
                "schemaVersion": "protocol_v1",
                "providerName": "qwen",
                "model": "qwen-test",
                "temperature": 0.2,
                "paperCount": 2,
                "reviewerQuestionCount": 3,
                "methods": ["method_a", "method_b"],
                "automaticAlignmentThreshold": 0.12,
                "repetitions": 3,
                "maxTotalTokensPerPaper": 4000,
                "selection": {"developmentPapersInThisSplit": 0},
                "lockedFiles": {
                    "samples": {"path": str(locked), "sha256": sha256_file(locked)},
                },
            }), encoding="utf-8")
            summary = {
                "paperCount": 2,
                "questionCount": 3,
                "automaticAlignmentThreshold": 0.12,
                "methods": [{"method": "method_a"}, {"method": "method_b"}],
                "qualityGate": {"status": "passed", "checks": {"base": True}},
            }

            attach_protocol_audit(
                summary, protocol_path, expected_repetitions=3, max_total_tokens=4000,
            )

            self.assertTrue(summary["protocolAudit"]["passed"])
            self.assertEqual(summary["protocolAudit"]["providerName"], "qwen")
            self.assertEqual(summary["protocolAudit"]["model"], "qwen-test")
            self.assertEqual(summary["protocolAudit"]["temperature"], 0.2)
            self.assertEqual(summary["protocolAudit"]["repetitions"], 3)
            self.assertEqual(summary["qualityGate"]["status"], "passed")


if __name__ == "__main__":
    unittest.main()
