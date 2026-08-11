from __future__ import annotations

import unittest

from experiments.reviewx_eval.export_peerqa_human_eval import blind_rows, build_rows


class PeerQAHumanEvalTests(unittest.TestCase):
    def test_reference_aligns_with_evidence_claim(self) -> None:
        reference = {
            "referenceId": "ref_1",
            "sampleId": "sample_clean",
            "paperId": "paper_public",
            "sourcePaperId": "source/public",
            "reviewerQuestion": "How does the method compare with the BFGS baseline?",
            "authorAnswerable": True,
            "authorAnswer": "It is faster than BFGS.",
            "evidenceSentences": ["Our method is faster than the BFGS baseline."],
        }
        record = {
            "sampleId": "sample_clean",
            "method": "reviewx_local_full",
            "claimScores": [{
                "claimId": "claim_1",
                "text": "Our method is faster than the BFGS baseline.",
                "sourceSpan": {"section": "Results"},
            }],
            "findings": [{
                "id": "finding_1",
                "claimId": "claim_1",
                "riskType": "unsupported_claim",
                "supportStatus": "artifact_absent",
                "title": "Baseline evidence needed",
                "description": "No structured baseline artifact is available.",
            }],
        }
        rows = build_rows([record], [reference], threshold=0.12)
        self.assertEqual(rows[0]["findingId"], "finding_1")
        self.assertTrue(rows[0]["automaticCoverageCandidate"])
        self.assertEqual(rows[0]["reviewerAssessment"], "No structured baseline artifact is available.")
        self.assertEqual(rows[0]["expertReviewerQuestion"], reference["reviewerQuestion"])

    def test_blind_export_masks_ids_and_match_fields(self) -> None:
        row = {
            "annotationId": "original",
            "method": "reviewx_local_full",
            "sampleId": "sample_clean",
            "paperId": "paper_public",
            "sourcePaperId": "source/public",
            "automaticMatchScore": 0.8,
            "automaticCoverageCandidate": True,
        }
        blind = blind_rows([row], seed=1)[0]
        self.assertNotIn("clean", blind["sampleId"])
        self.assertNotIn("public", blind["paperId"])
        self.assertEqual(blind["sourcePaperId"], "")
        self.assertEqual(blind["automaticMatchScore"], "")


if __name__ == "__main__":
    unittest.main()
