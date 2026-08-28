from __future__ import annotations

import unittest

try:
    from experiments.reviewx_eval.replay_llm_gap_findings import replay_record
except ModuleNotFoundError:
    from replay_llm_gap_findings import replay_record


class ReplayLlmGapFindingsTests(unittest.TestCase):
    def test_replays_specific_gap_into_generic_finding(self) -> None:
        record = {
            "paperId": "paper_1",
            "method": "reviewx_original",
            "model": "qwen-test",
            "claimScores": [{
                "claimId": "claim_1",
                "text": "The method improves accuracy by 10%.",
                "claimType": "result",
                "importance": "high",
                "requiresEvidence": True,
                "sourceSpan": {"file": "main.tex", "section": "Abstract", "line": 1},
                "findingIds": ["finding_1"],
            }],
            "findings": [{
                "id": "finding_1",
                "claimId": "claim_1",
                "severity": "info",
                "riskType": "citation_uncertainty",
                "supportStatus": "artifact_absent",
                "title": "Claim needs verification",
                "description": "No local artifact is available.",
                "targetModule": "papers",
                "suggestedFix": "Inspect the paper.",
                "confidence": 0.3,
            }],
            "modelTrace": {"llmRouting": {
                "requestedModel": "qwen-test",
                "budgetAllocations": [{"findingId": "finding_1", "priority": 0.3}],
                "llmAdditionalFindings": [{
                    "claimId": "claim_1",
                    "severity": "major",
                    "riskType": "metric_mismatch",
                    "supportStatus": "needs_human_verification",
                    "title": "Relative improvement is ambiguous",
                    "description": "The claim does not state whether 10% is relative or absolute.",
                    "targetModule": "experiments",
                    "suggestedFix": "Report the baseline and absolute scores.",
                    "confidence": 0.9,
                }],
            }},
            "summary": {},
        }

        updated, applications = replay_record(record, "reviewx_replay")

        self.assertEqual(applications[0]["outcome"], "merged")
        self.assertEqual(updated["method"], "reviewx_replay")
        self.assertEqual(updated["findings"][0]["title"], "Relative improvement is ambiguous")
        self.assertTrue(updated["modelTrace"]["postHocReplay"]["enabled"])
        self.assertFalse(updated["modelTrace"]["postHocReplay"]["independentValidation"])
        self.assertEqual(updated["summary"]["severityCounts"], {"major": 1})


if __name__ == "__main__":
    unittest.main()
