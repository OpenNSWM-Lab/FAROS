from __future__ import annotations

import unittest

from experiments.reviewx_eval.export_peerqa_human_eval import blind_rows
from experiments.reviewx_eval.export_peerqa_method_comparison import build_comparison_rows


class PeerQAMethodComparisonTests(unittest.TestCase):
    def test_selects_same_repetition_and_builds_unique_pairs(self) -> None:
        records = []
        for method in ("method_a", "method_b"):
            for repetition in (0, 1):
                records.append({
                    "sampleId": "sample_1", "method": method, "runnerRepetition": repetition,
                    "claimScores": [{"claimId": "c1", "text": "The baseline is faster."}],
                    "findings": [{
                        "id": f"{method}_{repetition}", "claimId": "c1",
                        "title": "Baseline concern", "description": "Runtime evidence is missing.",
                    }],
                })
        references = [{
            "referenceId": "ref_1", "sampleId": "sample_1", "paperId": "paper_1",
            "reviewerQuestion": "How fast is the baseline?", "evidenceSentences": [],
        }]
        rows, choices = build_comparison_rows(records, references, threshold=0.01, seed=9)
        self.assertEqual(len(rows), 2)
        self.assertEqual(len({row["annotationId"] for row in rows}), 2)
        self.assertEqual(len({row["comparisonPairId"] for row in rows}), 1)
        self.assertEqual({row["selectedRepetition"] for row in rows}, {choices["sample_1"]})
        blinded = blind_rows(rows, seed=10)
        self.assertEqual(len({row["method"] for row in blinded}), 2)
        self.assertTrue(all(not row["sourcePaperId"] for row in blinded))

    def test_rejects_references_without_complete_method_pairs(self) -> None:
        records = [{
            "sampleId": "sample_1", "method": method, "runnerRepetition": 0,
            "claimScores": [], "findings": [],
        } for method in ("method_a", "method_b")]
        references = [
            {"referenceId": "ref_1", "sampleId": "sample_1", "paperId": "paper_1"},
            {"referenceId": "ref_missing", "sampleId": "sample_2", "paperId": "paper_2"},
        ]
        with self.assertRaisesRegex(ValueError, "pair count"):
            build_comparison_rows(records, references, threshold=0.01, seed=9)


if __name__ == "__main__":
    unittest.main()
