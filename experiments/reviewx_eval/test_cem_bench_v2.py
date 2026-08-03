from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from experiments.reviewx_eval.export_human_eval import export_rows


ROOT = Path(__file__).resolve().parents[2]
EVAL_DIR = ROOT / "experiments" / "reviewx_eval"


class ClaimExtractionRegressionTests(unittest.TestCase):
    def test_escaped_percent_is_preserved_while_latex_comment_is_removed(self) -> None:
        backend = ROOT / "backend"
        if str(backend) not in sys.path:
            sys.path.insert(0, str(backend))
        from app.modules.review.claim_extractor import extract_claims

        claims = extract_claims({
            "paper": {"id": "paper_percent", "briefJson": {}},
            "latexFiles": [{
                "path": "main.tex",
                "content": (
                    "\\section{Results}\n"
                    "We improve accuracy by 42\\% over the baseline. % internal note\n"
                ),
            }],
        })

        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0].text, "We improve accuracy by 42% over the baseline.")
        self.assertIn("numeric_claim", claims[0].riskHints)

    def test_unescaped_percent_comment_drops_only_the_truncated_sentence(self) -> None:
        backend = ROOT / "backend"
        if str(backend) not in sys.path:
            sys.path.insert(0, str(backend))
        from app.modules.review.claim_extractor import extract_claims

        claims = extract_claims({
            "paper": {"id": "paper_comment", "briefJson": {}},
            "latexFiles": [{
                "path": "main.tex",
                "content": (
                    "\\section{Results}\n"
                    "We propose a traceable evidence validation method for scientific review. "
                    "We improve accuracy by 42% hidden comment\n"
                ),
            }],
        })

        self.assertEqual(
            [claim.text for claim in claims],
            ["We propose a traceable evidence validation method for scientific review."],
        )

    def test_academic_abbreviation_does_not_truncate_claim(self) -> None:
        backend = ROOT / "backend"
        if str(backend) not in sys.path:
            sys.path.insert(0, str(backend))
        from app.modules.review.claim_extractor import extract_claims

        latex_sentence = (
            "We improve accuracy by 4.7\\% where semantic nuance, e.g. statistical "
            "significance vs. practical significance, changes the final label."
        )
        expected = latex_sentence.replace("\\%", "%")
        claims = extract_claims({
            "paper": {"id": "paper_abbreviation", "briefJson": {}},
            "latexFiles": [{
                "path": "main.tex",
                "content": f"\\section{{Results}}\n{latex_sentence}\n",
            }],
        })

        self.assertEqual([claim.text for claim in claims], [expected])
        self.assertIn("numeric_claim", claims[0].riskHints)


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


class CEMBenchV2Tests(unittest.TestCase):
    def test_blind_human_export_masks_clean_and_paper_ids(self) -> None:
        rows = export_rows(
            predictions=[{
                "sampleId": "clean_obvious",
                "paperId": "paper_arxiv_obvious",
                "sourcePaperId": "paper_arxiv_obvious",
                "method": "ReviewX-local_only",
                "claimScores": [{
                    "claimId": "claim_1",
                    "text": "A high-stakes claim needs evidence.",
                    "sourceSpan": {"section": "Discussion"},
                }],
                "findings": [{
                    "id": "finding_1",
                    "claimId": "claim_1",
                    "riskType": "unsupported_claim",
                    "supportStatus": "unsupported",
                    "title": "Unsupported scope",
                    "description": "No direct evidence supports this scope.",
                }],
            }],
            gold_rows=[],
            selected_only=False,
            strict_only=True,
            blind=True,
        )

        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["sampleId"].startswith("sample_"))
        self.assertNotIn("clean", rows[0]["sampleId"])
        self.assertTrue(rows[0]["paperId"].startswith("paper_"))
        self.assertNotIn("arxiv", rows[0]["paperId"])
        self.assertEqual(rows[0]["findingTitle"], "Unsupported scope")
        self.assertEqual(rows[0]["findingDescription"], "No direct evidence supports this scope.")

    def test_v2_variants_pass_leakage_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_value:
            temp = Path(temp_value)
            backend_data = temp / "backend" / "data"
            paper_dir = backend_data / "papers" / "paper_source" / "latex"
            paper_dir.mkdir(parents=True)
            (paper_dir.parent / "meta.json").write_text(json.dumps({
                "id": "paper_source",
                "title": "A Normal Research Paper",
                "status": "completed",
                "notes": "ordinary source metadata",
                "externalPaper": {"arxivId": "2601.00001"},
            }), encoding="utf-8")
            (paper_dir / "main.tex").write_text(r"""
\documentclass{article}
\begin{document}
\section{Introduction}
We propose a structured method for reliable analysis \cite{alpha2024}.
\section{Experiments}
Our method improves the primary benchmark under the reported setting.
\section{Discussion}
We discuss limitations and possible extensions.
\section{Conclusion}
Our results support further investigation.
\bibliography{refs}
\end{document}
""", encoding="utf-8")
            output_dir = temp / "output"
            subprocess.run([
                sys.executable,
                str(EVAL_DIR / "make_cem_bench_v2.py"),
                "--source-paper-id", "paper_source",
                "--backend-data", str(backend_data),
                "--output-dir", str(output_dir),
                "--overwrite",
            ], cwd=ROOT, check=True, capture_output=True, text=True)
            validation = subprocess.run([
                sys.executable,
                str(EVAL_DIR / "validate_benchmark_leakage.py"),
                "--samples", str(output_dir / "samples.jsonl"),
                "--gold", str(output_dir / "gold_labels.jsonl"),
                "--backend-data", str(backend_data),
            ], cwd=ROOT, check=False, capture_output=True, text=True)
            self.assertEqual(validation.returncode, 0, validation.stdout + validation.stderr)
            samples = [json.loads(line) for line in (output_dir / "samples.jsonl").read_text().splitlines()]
            variants = [row for row in samples if row["sampleType"] == "paper_variant"]
            self.assertEqual(len(variants), 8)
            for sample in variants:
                self.assertNotIn("cembench", sample["paperId"].lower())
                variant_dir = backend_data / "papers" / sample["paperId"]
                meta = json.loads((variant_dir / "meta.json").read_text())
                self.assertEqual(meta["title"], "A Normal Research Paper")
                self.assertNotIn("cemBench", meta)
                self.assertTrue((variant_dir / ".reviewx_eval_variant").is_file())

    def test_non_exhaustive_gold_does_not_claim_precision(self) -> None:
        with tempfile.TemporaryDirectory() as temp_value:
            temp = Path(temp_value)
            predictions = temp / "predictions.jsonl"
            gold = temp / "gold.jsonl"
            target = "Our method improves factual accuracy by 25 percent on the benchmark."
            write_jsonl(predictions, [{
                "sampleId": "sample_1",
                "paperId": "paper_1",
                "method": "ReviewX-local_only",
                "claimScores": [
                    {"claimId": "claim_1", "text": target, "supportStatus": "unsupported"},
                    {"claimId": "claim_2", "text": "Our method reduces latency by 10 percent.", "supportStatus": "unsupported"},
                ],
                "findings": [
                    {"id": "finding_1", "claimId": "claim_1", "riskType": "unsupported_claim", "supportStatus": "unsupported"},
                    {"id": "finding_2", "claimId": "claim_2", "riskType": "unsupported_claim", "supportStatus": "unsupported"},
                ],
            }])
            write_jsonl(gold, [{
                "sampleId": "sample_1",
                "paperId": "paper_1",
                "corruptionType": "unsupported_overclaim",
                "targetClaimText": target,
                "expectedRiskType": "unsupported_claim",
                "expectedSupportStatus": "unsupported",
            }])

            default_output = temp / "default.json"
            exhaustive_output = temp / "exhaustive.json"
            base_command = [
                sys.executable,
                str(EVAL_DIR / "score_eval.py"),
                "--predictions", str(predictions),
                "--gold", str(gold),
                "--csv-output", str(temp / "scores.csv"),
            ]
            subprocess.run([*base_command, "--output", str(default_output)], cwd=ROOT, check=True)
            subprocess.run([
                *base_command,
                "--output", str(exhaustive_output),
                "--gold-is-exhaustive",
            ], cwd=ROOT, check=True)

            default = json.loads(default_output.read_text())["methods"]["ReviewX-local_only"]
            exhaustive = json.loads(exhaustive_output.read_text())["methods"]["ReviewX-local_only"]
            self.assertIsNone(default["unsupportedPrecision"])
            self.assertEqual(default["unsupportedTargetedPrecision"], 1.0)
            self.assertEqual(default["unmatchedIssueFindingCount"], 1)
            self.assertEqual(exhaustive["unsupportedPrecision"], 0.5)
            self.assertEqual(exhaustive["unsupportedF1"], 0.6667)

    def test_artifact_absent_gold_is_not_scored_as_strict_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_value:
            temp = Path(temp_value)
            predictions = temp / "predictions.jsonl"
            gold = temp / "gold.jsonl"
            target = "Our method improves accuracy by 25 percent on the benchmark."
            write_jsonl(predictions, [{
                "sampleId": "sample_1",
                "method": "ReviewX-local_only",
                "claimScores": [{
                    "claimId": "claim_1",
                    "text": target,
                    "supportStatus": "artifact_absent",
                }],
                "findings": [],
            }])
            write_jsonl(gold, [{
                "sampleId": "sample_1",
                "corruptionType": "numeric_mismatch",
                "targetClaimText": target,
                "expectedSupportStatus": "artifact_absent",
            }])
            output = temp / "scores.json"
            subprocess.run([
                sys.executable,
                str(EVAL_DIR / "score_eval.py"),
                "--predictions", str(predictions),
                "--gold", str(gold),
                "--output", str(output),
                "--csv-output", str(temp / "scores.csv"),
            ], cwd=ROOT, check=True)

            result = json.loads(output.read_text())
            method = result["methods"]["ReviewX-local_only"]
            self.assertEqual(method["triageRecall"], 1.0)
            self.assertEqual(method["unsupportedTP"], 0)
            self.assertEqual(method["contradictedTP"], 0)
            self.assertEqual(
                result["supportStatuses"]["ReviewX-local_only"]["artifact_absent"]["detectionRate"],
                1.0,
            )

    def test_same_section_does_not_count_as_target_detection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_value:
            temp = Path(temp_value)
            predictions = temp / "predictions.jsonl"
            gold = temp / "gold.jsonl"
            write_jsonl(predictions, [{
                "sampleId": "sample_1",
                "method": "ReviewX-local_only",
                "claimScores": [{
                    "claimId": "claim_other",
                    "text": "A different claim about retrieval accuracy.",
                    "supportStatus": "needs_human_verification",
                }],
                "findings": [{
                    "id": "finding_other",
                    "claimId": "claim_other",
                    "riskType": "traceability_gap",
                    "supportStatus": "needs_human_verification",
                    "location": {"section": "Introduction"},
                }],
            }])
            write_jsonl(gold, [{
                "sampleId": "sample_1",
                "targetSection": "Introduction",
                "targetClaimText": "Prior work proves faithful reasoning under distribution shift.",
                "expectedRiskType": "traceability_gap",
                "expectedSupportStatus": "needs_human_verification",
            }])
            output = temp / "scores.json"
            subprocess.run([
                sys.executable,
                str(EVAL_DIR / "score_eval.py"),
                "--predictions", str(predictions),
                "--gold", str(gold),
                "--output", str(output),
                "--csv-output", str(temp / "scores.csv"),
            ], cwd=ROOT, check=True)

            method = json.loads(output.read_text())["methods"]["ReviewX-local_only"]
            self.assertEqual(method["triageRecall"], 0.0)
            self.assertEqual(method["targetExtractionRecall"], 0.0)


if __name__ == "__main__":
    unittest.main()
