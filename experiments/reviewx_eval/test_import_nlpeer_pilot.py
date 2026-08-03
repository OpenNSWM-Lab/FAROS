from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from experiments.reviewx_eval.import_nlpeer_pilot import import_pilot, review_units


class ImportNLPeerPilotTests(unittest.TestCase):
    def test_review_units_exclude_summary_and_strengths(self) -> None:
        units = review_units({"report": {
            "paper_summary": "A neutral summary that should not become a weakness.",
            "summary_of_strengths": "The experiments are broad and convincing.",
            "summary_of_weaknesses": "The baseline is missing and the claim is unsupported.",
            "comments,_suggestions_and_typos": "Please add an ablation for the routing component.",
        }})
        self.assertEqual(len(units), 2)
        self.assertTrue(any("baseline" in unit["text"] for unit in units))

    def test_synthetic_official_layout_imports_without_reviewer_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            version = root / "nlpeer" / "ARR-EMNLP-2024" / "data" / "paper-1" / "v1"
            version.mkdir(parents=True)
            (version / "paper.itg.json").write_text(json.dumps({"nodes": [
                {"ix": "1", "ntype": "article-title", "content": "A Test Paper"},
                {"ix": "2", "ntype": "title", "content": "Methods"},
                {"ix": "3", "ntype": "p", "content": "We compare against one baseline."},
            ]}), encoding="utf-8")
            (version / "reviews.json").write_text(json.dumps([{
                "rid": "real-review-id", "reviewer": "Private Person",
                "report": {"summary_of_weaknesses": "The baseline comparison is incomplete."},
                "scores": {"overall": "3"},
            }]), encoding="utf-8")
            (version / "meta.json").write_text(json.dumps({"license": "CC-BY-NC-4.0"}), encoding="utf-8")
            output, backend = root / "output", root / "backend"
            manifest = import_pilot(
                root / "nlpeer", output, backend, datasets={"ARR-EMNLP-2024"},
                max_papers=1, max_per_dataset=1, dev_papers=1, overwrite=False,
            )
            references = [json.loads(line) for line in (output / "references.jsonl").read_text().splitlines()]
            self.assertEqual(manifest["selection"]["selectedPaperCount"], 1)
            self.assertEqual(len(references), 1)
            self.assertNotIn("Private Person", json.dumps(references))
            self.assertTrue(references[0]["reviewId"].startswith("review_"))


if __name__ == "__main__":
    unittest.main()
