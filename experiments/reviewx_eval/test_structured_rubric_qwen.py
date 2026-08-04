from __future__ import annotations

import json
import unittest

from experiments.reviewx_eval.baselines.structured_rubric_qwen import (
    DIMENSIONS,
    build_prompt,
    parse_structured_review,
)
from experiments.reviewx_eval.export_peerqa_human_eval import build_rows


class StructuredRubricBaselineTests(unittest.TestCase):
    def test_prompt_checks_all_dimensions_without_expert_reference(self) -> None:
        prompt = build_prompt("Paper", "A manuscript.", max_findings=6)
        for dimension in DIMENSIONS:
            self.assertIn(dimension, prompt)
        self.assertIn("Do not force one finding per", prompt)
        self.assertNotIn("expertReviewerQuestion", prompt)
        self.assertNotIn("authorAnswer", prompt)

    def test_parse_preserves_rubric_and_exact_quote_grounding(self) -> None:
        response = json.dumps({
            "overallAssessment": "The evaluation is incomplete.",
            "rubricCoverage": {dimension: "checked" for dimension in DIMENSIONS},
            "findings": [{
                "title": "Missing uncertainty",
                "claimText": "The method improves accuracy by 20 percent.",
                "section": "Results",
                "reviewDimension": "experimental_design",
                "riskType": "methodological_gap",
                "severity": "major",
                "description": "No uncertainty is reported.",
                "suggestedFix": "Report a confidence interval from repeated runs.",
                "confidence": 0.9,
            }],
        })
        parsed = parse_structured_review(
            response, "The method improves accuracy by 20 percent.", max_findings=6
        )
        finding = parsed["findings"][0]
        self.assertEqual(finding["reviewDimension"], "experimental_design")
        self.assertEqual(finding["id"], "structured_rubric_finding_001")
        self.assertEqual(finding["claimId"], "structured_rubric_claim_001")
        self.assertTrue(finding["claimQuoteFound"])
        self.assertEqual(set(parsed["rubricCoverage"]), set(DIMENSIONS))

    def test_unknown_dimension_falls_back_to_claim_evidence(self) -> None:
        response = json.dumps({"findings": [{
            "claimText": "A result.", "reviewDimension": "invented",
        }]})
        parsed = parse_structured_review(response, "A result.", max_findings=1)
        self.assertEqual(parsed["findings"][0]["reviewDimension"], "claim_evidence")

    def test_fenced_json_preserves_coverage(self) -> None:
        payload = {
            "rubricCoverage": {dimension: "checked" for dimension in DIMENSIONS},
            "findings": [],
        }
        parsed = parse_structured_review(
            "```json\n" + json.dumps(payload) + "\n```", "Paper", max_findings=1
        )
        self.assertTrue(all(value == "checked" for value in parsed["rubricCoverage"].values()))

    def test_output_is_compatible_with_peerqa_alignment(self) -> None:
        response = json.dumps({"findings": [{
            "claimText": "The BFGS baseline is faster.",
            "title": "Unsupported speed claim",
            "description": "Runtime evidence is missing.",
            "reviewDimension": "experimental_design",
        }]})
        parsed = parse_structured_review(response, "The BFGS baseline is faster.", 2)
        record = {
            "sampleId": "sample_1",
            "method": "qwen_structured_rubric_matched_budget",
            "findings": parsed["findings"],
            "claimScores": [{
                "claimId": finding["claimId"],
                "text": finding["claimText"],
                "sourceSpan": {"section": finding["claimSection"]},
            } for finding in parsed["findings"]],
        }
        rows = build_rows([record], [{
            "referenceId": "ref_1", "sampleId": "sample_1", "paperId": "paper_1",
            "reviewerQuestion": "How fast is the BFGS baseline?", "evidenceSentences": [],
        }], threshold=0.01)
        self.assertEqual(rows[0]["findingId"], "structured_rubric_finding_001")


if __name__ == "__main__":
    unittest.main()
