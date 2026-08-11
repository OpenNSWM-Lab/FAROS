"""
Test: Problem Framing module.

Tests that frame_problem produces valid ProblemFrame output for both
LLM and fallback modes, including CJK questions.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.contracts import ProblemFrame, ScientificQuestion
from app.modules.idea.problem_framing import frame_problem, _fallback_problem_frame


class TestProblemFraming:
    def test_fallback_produces_valid_frame(self):
        """Fallback (no LLM) should produce a valid ProblemFrame."""
        q = ScientificQuestion(
            id="q_001",
            text="How can we improve the efficiency of solar cells?",
            domainHint="materials science",
        )
        frame = _fallback_problem_frame(q)
        assert isinstance(frame, ProblemFrame)
        assert frame.originalQuestion == q.text
        assert len(frame.scopedQuestion) >= 5
        assert len(frame.assumptions) >= 1

    def test_frame_with_llm_disabled(self):
        """frame_problem with use_llm=False should use fallback."""
        q = ScientificQuestion(
            id="q_002",
            text="What is the relationship between sleep and memory consolidation?",
            domainHint="neuroscience",
        )
        frame = frame_problem(q, use_llm=False)
        assert isinstance(frame, ProblemFrame)
        assert frame.originalQuestion == q.text
        assert frame.scopedQuestion  # non-empty

    def test_frame_preserves_chinese_question(self):
        """CJK question should be preserved in originalQuestion."""
        q = ScientificQuestion(
            id="q_003",
            text="如何提高太阳能电池的效率？",
            domainHint="材料科学",
        )
        frame = frame_problem(q, use_llm=False)
        assert "太阳能" in frame.originalQuestion
        assert frame.scopedQuestion  # non-empty

    def test_frame_includes_definitions(self):
        """Frame should have definitions dict (can be empty in fallback)."""
        q = ScientificQuestion(
            id="q_004",
            text="Does dark matter interact with regular matter beyond gravity?",
        )
        frame = frame_problem(q, use_llm=False)
        assert isinstance(frame.definitions, dict)

    def test_frame_includes_out_of_scope(self):
        """Frame should list out-of-scope items."""
        q = ScientificQuestion(
            id="q_005",
            text="Can CRISPR be used to treat genetic diseases?",
        )
        frame = frame_problem(q, use_llm=False)
        assert len(frame.outOfScope) >= 1

    def test_frame_includes_sub_questions(self):
        """Frame should decompose into sub-questions."""
        q = ScientificQuestion(
            id="q_006",
            text="What causes Alzheimer's disease progression?",
        )
        frame = frame_problem(q, use_llm=False)
        assert len(frame.subQuestions) >= 1

    def test_frame_scoped_question_is_narrower(self):
        """Scoped question should be different from or more specific than original."""
        short_q = ScientificQuestion(
            id="q_007",
            text="Is there life on Mars?",
        )
        frame = frame_problem(short_q, use_llm=False)
        # For very short questions, scoped should be expanded
        assert len(frame.scopedQuestion) >= len(short_q.text)

    def test_frame_with_constraints(self):
        """Frame should handle constraints in the question."""
        q = ScientificQuestion(
            id="q_008",
            text="How to optimize neural network training?",
            constraints=["must run on single GPU", "inference under 100ms"],
        )
        frame = frame_problem(q, use_llm=False)
        assert isinstance(frame, ProblemFrame)
