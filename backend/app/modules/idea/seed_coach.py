"""Qwen-backed research seed coaching helpers.

The coach turns a novice's short interest into search-ready research questions.
It deliberately keeps provider calls outside this module so parsing and prompt
quality can be tested without network access.
"""

from __future__ import annotations

import json
import re
from typing import Any, Mapping


PAPER_TYPE_HINTS = {
    "algorithm": "an algorithmic contribution with measurable baselines",
    "system": "a system contribution with latency, cost, reliability, or scalability evaluation",
    "application": "an applied contribution with a real task, dataset, and practical outcome",
    "benchmark": "a benchmark contribution with tasks, metrics, baselines, and reproducible data",
    "survey": "a scoped survey with an explicit taxonomy and comparison dimensions",
    "position": "a falsifiable position supported by evidence and counterarguments",
    "theory": "a theoretical contribution with assumptions, claims, and validation conditions",
    "evaluation": "an evaluation study with systems, datasets, metrics, and controlled factors",
    "reproducibility": "a reproducibility study with target artifacts and agreement criteria",
    "safety": "a safety study with threat model, intervention, and risk measurements",
}

EVALUATION_MARKERS = {
    "accuracy",
    "agreement",
    "attribution",
    "benchmark",
    "calibration",
    "correctness",
    "cost",
    "dataset",
    "error",
    "evaluated",
    "evaluation",
    "expert",
    "f1",
    "faithfulness",
    "latency",
    "metric",
    "novelty",
    "precision",
    "preference",
    "recall",
    "reliability",
    "robustness",
    "success rate",
}


def build_seed_coach_prompt(
    *,
    user_idea: str,
    paper_type: str,
    count: int,
    diagnosis: str | None = None,
) -> str:
    """Build a constrained prompt for practical, literature-searchable seeds."""

    cleaned_idea = user_idea.strip() or "No topic supplied; propose accessible frontier topics for an AI Scientist workflow."
    type_hint = PAPER_TYPE_HINTS.get(paper_type, PAPER_TYPE_HINTS["algorithm"])
    diagnosis_line = diagnosis or "initial_topic_coaching"
    return (
        "You are Qwen acting as a research-topic coach for first-time researchers.\n"
        "Turn the user's rough interest into distinct, literature-searchable research questions.\n"
        "The output will be inserted directly into an academic literature pipeline.\n\n"
        f"User interest: {cleaned_idea}\n"
        f"Paper type: {paper_type} ({type_hint})\n"
        f"Current diagnosis: {diagnosis_line}\n"
        f"Return exactly {count} suggestions.\n\n"
        "Requirements for every suggestion:\n"
        "1. Preserve the user's intent when one is supplied.\n"
        "2. Name a concrete target task, a method or intervention, a research domain, and an evaluation target.\n"
        "3. Make query an English academic search query of 12-32 words with at least three discriminative concepts.\n"
        "4. The query must literally include 'evaluated by' followed by at least two standard metrics, evidence criteria, or public benchmarks.\n"
        "5. Prefer public datasets or standard metrics, but never invent a dataset, result, citation, or claimed improvement.\n"
        "6. Keep the scope feasible for a student team and make each option materially different.\n"
        "7. Explain fit in one short sentence without promising results.\n\n"
        "Bad query: AI scientist credibility methods.\n"
        "Good query: Evidence-grounded LLM agents for scientific hypothesis generation evaluated by citation faithfulness, novelty agreement, and expert preference.\n\n"
        "Return only one JSON object using this schema:\n"
        '{"suggestions":[{"titleZh":"...","titleEn":"...","query":"...",'
        '"rationaleZh":"...","rationaleEn":"..."}]}\n'
    )


def _extract_json_object(text: str) -> Mapping[str, Any] | None:
    cleaned = (text or "").strip()
    if not cleaned:
        return None
    candidates = [cleaned]
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL | re.IGNORECASE)
    if fenced:
        candidates.append(fenced.group(1))
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        candidates.append(cleaned[start : end + 1])

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(parsed, Mapping):
            return parsed
    return None


def parse_seed_suggestions(text: str, *, limit: int = 3) -> list[dict[str, str]]:
    """Validate and normalize Qwen's structured seed suggestions."""

    payload = _extract_json_object(text)
    raw_items = payload.get("suggestions") if payload else None
    if not isinstance(raw_items, list):
        return []

    suggestions: list[dict[str, str]] = []
    seen_queries: set[str] = set()
    for raw_item in raw_items:
        if not isinstance(raw_item, Mapping):
            continue
        query = str(raw_item.get("query") or "").strip().strip('"')
        normalized_query = " ".join(query.split())
        query_key = normalized_query.casefold()
        query_words = re.findall(r"[a-zA-Z0-9][a-zA-Z0-9-]*", normalized_query.lower())
        marker_found = any(marker in normalized_query.lower() for marker in EVALUATION_MARKERS)
        if len(query_words) < 10 or not marker_found or query_key in seen_queries:
            continue

        title_en = str(raw_item.get("titleEn") or raw_item.get("title") or normalized_query).strip()
        title_zh = str(raw_item.get("titleZh") or title_en).strip()
        rationale_en = str(raw_item.get("rationaleEn") or raw_item.get("rationale") or "").strip()
        rationale_zh = str(raw_item.get("rationaleZh") or rationale_en).strip()
        if not title_en or not title_zh:
            continue

        seen_queries.add(query_key)
        suggestions.append(
            {
                "titleZh": title_zh[:120],
                "titleEn": title_en[:120],
                "query": normalized_query[:500],
                "rationaleZh": rationale_zh[:300],
                "rationaleEn": rationale_en[:300],
            }
        )
        if len(suggestions) >= max(1, limit):
            break
    return suggestions
