from __future__ import annotations

import unittest

from experiments.reviewx_eval.import_peerqa_pilot import manuscript, select_papers


class PeerQAPilotTests(unittest.TestCase):
    def test_selection_is_source_capped_and_requires_text(self) -> None:
        paragraphs = [
            {"paper_id": "source/a/1", "idx": 0, "type": "title", "content": "A"},
            {"paper_id": "source/a/2", "idx": 0, "type": "title", "content": "B"},
            {"paper_id": "source/b/3", "idx": 0, "type": "title", "content": "C"},
        ]
        questions = [
            {"paper_id": "source/a/1", "answerable_mapped": True, "answer_free_form": "x"},
            {"paper_id": "source/a/1", "answerable_mapped": True, "answer_free_form": "y"},
            {"paper_id": "source/a/2", "answerable_mapped": True, "answer_free_form": "z"},
            {"paper_id": "source/b/3", "answerable_mapped": True, "answer_free_form": "q"},
            {"paper_id": "source/c/missing", "answerable_mapped": True, "answer_free_form": "q"},
        ]
        selected = select_papers(paragraphs, questions, max_papers=3, max_per_source=1)
        self.assertEqual(len(selected), 2)
        self.assertIn("source/a/1", selected)
        self.assertIn("source/b/3", selected)

    def test_manuscript_preserves_sections_and_escapes_percent(self) -> None:
        content = manuscript([
            {"idx": 0, "type": "title", "content": "Title"},
            {"idx": 1, "type": "heading", "content": "Results"},
            {"idx": 2, "type": "paragraph", "content": "We improve by 10% on the task.", "last_heading": "Results"},
        ])
        self.assertIn("\\section{Results}", content)
        self.assertIn("10 percent", content)
        self.assertNotIn("10%", content)

    def test_selection_excludes_previous_source_papers(self) -> None:
        paragraphs = [
            {"paper_id": "source/a/1", "idx": 0, "type": "title", "content": "A"},
            {"paper_id": "source/b/2", "idx": 0, "type": "title", "content": "B"},
        ]
        questions = [
            {"paper_id": "source/a/1", "answerable_mapped": True, "answer_free_form": "x"},
            {"paper_id": "source/b/2", "answerable_mapped": True, "answer_free_form": "y"},
        ]

        selected = select_papers(
            paragraphs,
            questions,
            max_papers=2,
            max_per_source=2,
            excluded_papers={"source/a/1"},
        )

        self.assertEqual(selected, ["source/b/2"])


if __name__ == "__main__":
    unittest.main()
