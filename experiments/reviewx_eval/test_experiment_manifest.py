from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from experiments.reviewx_eval.freeze_experiment import build_manifest, validate_methods, verify_manifest
from experiments.reviewx_eval.run_eval import load_completed_runs, validate_record_config


class ExperimentManifestTests(unittest.TestCase):
    def test_manifest_detects_paper_artifact_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temp_value:
            temp = Path(temp_value)
            backend_data = temp / "data"
            paper_dir = backend_data / "papers" / "paper_1"
            (paper_dir / "latex").mkdir(parents=True)
            (paper_dir / "meta.json").write_text(
                json.dumps({"id": "paper_1", "experimentIds": []}), encoding="utf-8",
            )
            manuscript = paper_dir / "latex" / "main.tex"
            manuscript.write_text("Original manuscript.\n", encoding="utf-8")
            samples = temp / "samples.jsonl"
            samples.write_text(
                json.dumps({"sampleId": "sample_1", "paperId": "paper_1"}) + "\n",
                encoding="utf-8",
            )
            gold = temp / "gold.jsonl"
            gold.write_text("", encoding="utf-8")
            matrix = temp / "matrix.json"
            matrix.write_text(json.dumps({"methods": [{
                "id": "reviewx_local_full",
                "budgetMode": "local_only",
                "ablationMode": "full",
            }]}), encoding="utf-8")

            manifest = build_manifest(
                samples_path=samples,
                gold_path=gold,
                matrix_path=matrix,
                backend_data=backend_data,
                api_base="http://localhost:8005",
                run_timeout=240,
                fetch_timeout=120,
            )
            self.assertEqual(verify_manifest(manifest), [])

            manuscript.write_text("Changed manuscript.\n", encoding="utf-8")
            errors = verify_manifest(manifest)
            self.assertTrue(any("changed artifact root" in error for error in errors))

    def test_method_matrix_validates_repetitions_and_ids(self) -> None:
        methods = validate_methods({"methods": [{
            "id": "reviewx_balanced_qwen",
            "kind": "reviewx",
            "budgetMode": "balanced",
            "ablationMode": "full",
            "providerName": "qwen",
            "model": "qwen-max",
            "repetitions": 3,
            "maxEstimatedTokens": 5000,
        }]})
        self.assertEqual(methods[0]["repetitions"], 3)
        with self.assertRaises(ValueError):
            validate_methods({"methods": [{"id": "bad id", "budgetMode": "local_only"}]})

    def test_resume_key_includes_repetition(self) -> None:
        with tempfile.TemporaryDirectory() as temp_value:
            output = Path(temp_value) / "runs.jsonl"
            output.write_text("".join([
                json.dumps({"sampleId": "sample_1", "method": "method_1", "runnerRepetition": 0}) + "\n",
                json.dumps({"sampleId": "sample_1", "method": "method_1", "runnerRepetition": 1}) + "\n",
            ]), encoding="utf-8")
            self.assertEqual(load_completed_runs(output), {
                ("sample_1", "method_1", 0),
                ("sample_1", "method_1", 1),
            })
            with self.assertRaises(ValueError):
                load_completed_runs(output, "different_fingerprint")

    def test_eval_record_must_match_method_config(self) -> None:
        config = {
            "budgetMode": "balanced",
            "ablationMode": "full",
            "providerName": "qwen",
            "model": "qwen-max",
        }
        validate_record_config({
            "budgetMode": "balanced",
            "ablationMode": "full",
            "providerName": "qwen",
            "model": "qwen-max",
        }, config)
        with self.assertRaises(ValueError):
            validate_record_config({
                "budgetMode": "local_only",
                "ablationMode": "full",
                "providerName": "qwen",
                "model": "qwen-max",
            }, config)


if __name__ == "__main__":
    unittest.main()
