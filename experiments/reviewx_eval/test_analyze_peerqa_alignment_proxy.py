from __future__ import annotations

import unittest

from experiments.reviewx_eval.analyze_peerqa_alignment_proxy import analyze, exact_mcnemar_p


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
                    "modelTrace": {"estimatedTokenCost": 100, "budgetExceeded": False},
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


if __name__ == "__main__":
    unittest.main()
