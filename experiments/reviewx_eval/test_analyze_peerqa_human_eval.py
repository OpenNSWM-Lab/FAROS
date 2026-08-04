from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from experiments.reviewx_eval.analyze_peerqa_human_eval import analyze, cluster_bootstrap_mean_ci
from experiments.reviewx_eval.export_peerqa_human_eval import blind_rows


FIELDS = [
    "humanCorrectness", "humanActionability", "humanSpecificity",
    "humanGrounding", "humanSeverityAgreement",
]


def row(task: str, rater: str, score: int, coverage: str, status: str = "completed") -> dict[str, str]:
    return {
        "annotationId": task, "annotatorId": rater, "annotationStatus": status,
        "humanCoverageLabel": coverage, "sampleId": f"sample_{task}",
        "method": "method_a",
        **{field: str(score) for field in FIELDS},
    }


class AnalyzePeerQATests(unittest.TestCase):
    def test_cluster_bootstrap_resamples_papers_not_tasks(self) -> None:
        interval = cluster_bootstrap_mean_ci(
            [("paper_a", 1.0), ("paper_a", 1.0), ("paper_b", 5.0)],
            seed=2,
            iterations=200,
        )
        self.assertEqual(interval, (1.0, 5.0))

    def test_multi_rater_summary_and_disagreement(self) -> None:
        rows = [
            row("t1", "a", 5, "covered"), row("t1", "b", 4, "covered"),
            row("t2", "a", 1, "not_covered"), row("t2", "b", 4, "partial"),
            row("t3", "a", 3, "partial", status="draft"),
        ]
        report, aggregates, disagreements, curve = analyze(
            rows, answer_key=None, seed=7, iterations=100,
        )
        self.assertEqual(report["completedRows"], 4)
        self.assertEqual(report["incompleteRows"], 1)
        self.assertEqual(report["annotatorCount"], 2)
        self.assertEqual(report["coverage"]["consensusTaskCount"], 1)
        self.assertEqual(report["coverage"]["tieOrMissingCount"], 1)
        self.assertEqual(len(aggregates), 2)
        self.assertEqual(len(disagreements), 1)
        self.assertEqual(curve, [])

    def test_duplicate_rater_task_is_rejected(self) -> None:
        duplicate = row("t1", "a", 5, "covered")
        with self.assertRaisesRegex(ValueError, "duplicate"):
            analyze([duplicate, dict(duplicate)], answer_key=None, seed=1, iterations=10)

    def test_answer_key_is_mapped_after_blind_shuffle(self) -> None:
        originals = [
            {"annotationId": "source_a", "sampleId": "a", "paperId": "pa", "method": "m",
             "automaticMatchScore": "0.30", "automaticCoverageCandidate": "True"},
            {"annotationId": "source_b", "sampleId": "b", "paperId": "pb", "method": "m",
             "automaticMatchScore": "0.01", "automaticCoverageCandidate": "False"},
        ]
        labels = {"source_a": "covered", "source_b": "not_covered"}
        annotated = []
        indexed_blind = blind_rows([{**item, "source": item["annotationId"]} for item in originals], seed=11)
        for blind in indexed_blind:
            annotated.append({
                **row(blind["annotationId"], "rater", 5, labels[blind["source"]]),
                "sampleId": blind["sampleId"],
            })
        with tempfile.TemporaryDirectory() as tempdir:
            answer_key = Path(tempdir) / "answer.csv"
            with answer_key.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(originals[0]))
                writer.writeheader()
                writer.writerows(originals)
            report, _, _, curve = analyze(
                annotated, answer_key=answer_key, seed=11, iterations=20,
            )
        self.assertEqual(report["automaticAlignment"]["usableTasks"], 2)
        self.assertEqual(report["automaticAlignment"]["bestDevelopmentThreshold"]["f1"], 1.0)
        self.assertEqual(len(curve), 36)

    def test_paired_method_effect_is_reported(self) -> None:
        left = row("left_task", "rater", 2, "not_covered")
        left.update({"method": "left", "comparisonPairId": "pair_1"})
        right = row("right_task", "rater", 4, "covered")
        right.update({"method": "right", "comparisonPairId": "pair_1"})
        report, _, _, _ = analyze(
            [left, right], answer_key=None, seed=3, iterations=20,
        )
        paired = report["pairedComparison"]
        self.assertEqual(paired["completePairCount"], 1)
        self.assertEqual(paired["metrics"]["humanCorrectness"]["meanDifference"], 2.0)
        self.assertEqual(
            paired["metrics"]["humanCorrectness"]["paperClusterBootstrap95CI"],
            [2.0, 2.0],
        )
        self.assertEqual(paired["broadCoverage"]["meanDifference"], 1.0)

    def test_three_methods_report_all_pairwise_effects(self) -> None:
        rows = []
        for task, method, score, coverage in (
            ("task_a", "method_a", 1, "not_covered"),
            ("task_b", "method_b", 3, "partial"),
            ("task_c", "method_c", 5, "covered"),
        ):
            item = row(task, "rater", score, coverage)
            item.update({"method": method, "comparisonPairId": "pair_1"})
            rows.append(item)
        report, _, _, _ = analyze(rows, answer_key=None, seed=4, iterations=20)
        paired = report["pairedComparison"]
        self.assertEqual(paired["comparisonMode"], "all_pairwise")
        self.assertEqual(paired["comparisonCount"], 3)
        effects = {
            (item["leftMethod"], item["rightMethod"]): item
            for item in paired["comparisons"]
        }
        self.assertEqual(
            effects[("method_a", "method_c")]["metrics"]["humanCorrectness"]["meanDifference"],
            4.0,
        )
        self.assertEqual(
            effects[("method_a", "method_b")]["broadCoverage"]["meanDifference"],
            1.0,
        )


if __name__ == "__main__":
    unittest.main()
