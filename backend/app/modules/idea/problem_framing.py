"""
Problem Framing Module

Takes a raw scientific question and produces a structured ProblemFrame:
- scopedQuestion: refined, researchable version of the original question
- definitions: key terms and their definitions
- observableVariables: measurable variables relevant to the question
- assumptions: explicit assumptions made when scoping
- outOfScope: explicitly excluded aspects
- subQuestions: decomposed sub-questions

This module implements P0 task #2 from the Idea module task book.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any, Dict, List, Optional

from app.contracts import ProblemFrame, ScientificQuestion
from app.llm.provider_client import ChatMessage, ProviderError, get_provider_client

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

PROBLEM_FRAME_SYSTEM = """\
You are a research methodology expert. Given a scientific question, produce a \
structured problem frame that makes the question researchable and falsifiable.

Return ONLY valid JSON with these fields:
{
  "scopedQuestion": "A refined, specific, researchable version of the question (min 10 words)",
  "definitions": {"key_term": "definition", ...},
  "observableVariables": ["variable1", "variable2", ...],
  "assumptions": ["explicit assumption 1", ...],
  "outOfScope": ["excluded aspect 1", ...],
  "subQuestions": ["decomposed sub-question 1", ...]
}

Rules:
- scopedQuestion must be narrower than the original and testable.
- definitions must cover all non-obvious terms.
- observableVariables must be measurable (data, metric, or observable quantity).
- assumptions must be explicit, not hidden.
- outOfScope must list what is deliberately excluded and why.
- subQuestions should decompose the scoped question into 2-5 parts.
- If the original question is in Chinese, keep Chinese in the output.
"""

PROBLEM_FRAME_USER = """\
Original question: {question}

Domain hint: {domain}
Constraints: {constraints}

Produce the structured problem frame.
"""

# ---------------------------------------------------------------------------
# Fallback (no-LLM) problem framing
# ---------------------------------------------------------------------------

def _fallback_problem_frame(question: ScientificQuestion) -> ProblemFrame:
    """Build a minimal ProblemFrame without LLM, used on API failure or coverage mode."""
    text = question.text.strip()
    domain = question.domainHint or "general science"

    # Simple scoping: prefix with "To what extent" if not already specific
    if len(text) < 30:
        scoped = f"To what extent does the following hold: {text}"
    else:
        scoped = text

    return ProblemFrame(
        originalQuestion=text,
        scopedQuestion=scoped,
        definitions={},
        observableVariables=[],
        assumptions=[f"The question is within the domain of {domain}."],
        outOfScope=["Wet-lab validation", "Longitudinal studies beyond scope"],
        subQuestions=[text],
    )


# ---------------------------------------------------------------------------
# LLM-based problem framing
# ---------------------------------------------------------------------------

def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    """Robust JSON extraction from LLM output."""
    # Direct parse
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Markdown code block
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass
    # Balanced braces
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    pass
    return None


def frame_problem(
    question: ScientificQuestion,
    *,
    provider_name: Optional[str] = None,
    model: Optional[str] = None,
    use_llm: bool = True,
) -> ProblemFrame:
    """
    Produce a ProblemFrame from a ScientificQuestion.

    Args:
        question: The input scientific question.
        provider_name: LLM provider name. If None, uses active provider.
        model: Model name. If None, uses provider's active model.
        use_llm: If False, skip LLM and use fallback (coverage mode).

    Returns:
        A validated ProblemFrame.
    """
    if not use_llm:
        return _fallback_problem_frame(question)

    try:
        client = get_provider_client(provider_name)
        user_msg = PROBLEM_FRAME_USER.format(
            question=question.text,
            domain=question.domainHint or "not specified",
            constraints=", ".join(question.constraints) if question.constraints else "none",
        )
        resp = client.chat(
            messages=[
                ChatMessage(role="system", content=PROBLEM_FRAME_SYSTEM),
                ChatMessage(role="user", content=user_msg),
            ],
            model=model,
            temperature=0.3,
            max_tokens=1024,
        )
        parsed = _extract_json(resp.text)
        if not parsed:
            logger.warning("Problem framing LLM returned unparseable JSON, using fallback")
            return _fallback_problem_frame(question)

        scoped = parsed.get("scopedQuestion", "").strip()
        if len(scoped) < 5:
            logger.warning("Problem framing LLM returned empty scopedQuestion, using fallback")
            return _fallback_problem_frame(question)

        return ProblemFrame(
            originalQuestion=question.text,
            scopedQuestion=scoped,
            definitions={str(k): str(v) for k, v in parsed.get("definitions", {}).items()},
            observableVariables=[str(v) for v in parsed.get("observableVariables", [])],
            assumptions=[str(a) for a in parsed.get("assumptions", [])],
            outOfScope=[str(o) for o in parsed.get("outOfScope", [])],
            subQuestions=[str(q) for q in parsed.get("subQuestions", [])],
        )
    except (ProviderError, Exception) as e:
        logger.warning("Problem framing LLM failed: %s, using fallback", e)
        return _fallback_problem_frame(question)
