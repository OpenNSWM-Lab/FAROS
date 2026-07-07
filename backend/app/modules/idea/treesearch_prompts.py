"""
Tree-Search Prompt Templates for BFTS + Reflection Loop.

Reference: AI-Scientist-v2 ai_scientist/perform_ideation_temp_free.py
Format: ACTION / ARGUMENTS blocks (same as AI Scientist v2)
"""

# --- Seed Idea Generation (Phase: initialize seeds from PathSeeds) ---

BFTS_SEED_SYSTEM = """You are a creative AI researcher generating seed research ideas.

You will receive a reasoning path seed (a chain of related concepts / entities)
and a set of selected literature papers.

Your task is to propose a concrete, novel research idea that:
1. Is grounded in the path seed (uses the entities / relations described)
2. Is clearly differentiated from existing literature
3. Has a specific, testable hypothesis
4. Is feasible within academic lab resources

Respond in JSON format with the following fields:
{{
  "title": "A catchy and informative title",
  "problem": "What problem does this solve?",
  "hypothesis": "The core hypothesis (one sentence)",
  "abstract": "A 150-word abstract summarizing the proposal",
  "approach": "High-level methodology",
  "expectedOutcomes": ["outcome1", "outcome2"],
  "requiredExperiments": [
    {{"name": "...", "description": "...", "metrics": ["..."], "datasets": ["..."]}}
  ],
  "risks": [
    {{"risk": "...", "mitigation": "..."}}
  ]
}}"""

BFTS_SEED_USER = """Research Topic: {seed_query}
Paper Type: {paper_type}

## Reasoning Path Seed
Template Type: {template_type}
Anchor Entities: {anchor_entities}
Path Steps:
{path_steps}

## Selected Literature (for grounding)
{literature_context}

## Path Seed Rationale
{rationale}

Generate ONE seed research idea based on this path seed.
The idea should be novel, feasible, and clearly different from the listed literature.
Respond in the JSON format described in the system prompt."""


# --- Reflection Loop Prompts (per-node) ---

REFLECTION_SYSTEM = """You are an experienced AI researcher iteratively refining a research idea.

You have access to a literature search tool to look up related work and improve your idea.

Your goal is to refine the current idea by:
1. Searching for related literature when you need more context or want to check novelty
2. Updating the idea based on new information
3. Finalizing the idea when you are confident it is novel and well-formed

IMPORTANT: You MUST respond in EXACTLY one of these two formats:

Format 1 - Call a tool:
ACTION:
SearchLiterature

ARGUMENTS:
{{"query": "your search query here"}}

Format 2 - Finalize the idea:
ACTION:
FinalizeIdea

ARGUMENTS:
{{"idea": {{
  "title": "...",
  "problem": "...",
  "hypothesis": "...",
  "abstract": "...",
  "approach": "...",
  "expectedOutcomes": ["..."],
  "requiredExperiments": [{{"name": "...", "description": "...", "metrics": ["..."], "datasets": ["..."]}}],
  "risks": [{{"risk": "...", "mitigation": "..."}}]
}}}}

NOTES:
- When you search, use specific, targeted queries (3-6 words).
- After 1-2 searches, you should have enough context to finalize.
- Do NOT repeat the same search query.
- The idea JSON in FinalizeIdea MUST be complete and valid JSON.
- Only finalize when you are confident the idea is novel and distinct from literature."""


REFLECTION_USER = """Research Topic: {seed_query}
Paper Type: {paper_type}

## Current Idea (Round {round_num}/{max_rounds})
Title: {current_title}
Hypothesis: {current_hypothesis}
Abstract: {current_abstract}

## Literature Context (already known)
{literature_context}

## Your Previous Search Results
{tool_results}

## Reflection History
{reflection_history}

What would you like to do next?
- Call SearchLiterature with a NEW query to gather more information
- Call FinalizeIdea when the idea is ready

Respond with ACTION / ARGUMENTS in the exact format specified."""


# --- Tool result formatter ---

def format_search_results(results: list, limit: int = 5) -> str:
    """Format search_service results for the reflection loop.

    Args:
        results: list of SearchResult-like objects (with .title, .authors, .year, .abstract)
        limit: max number of results to include

    Returns:
        Formatted string for LLM consumption
    """
    if not results:
        return "(No results found for this query.)"

    lines = []
    for i, r in enumerate(results[:limit]):
        title = getattr(r, 'title', 'Unknown')
        authors = getattr(r, 'authors', [])
        year = getattr(r, 'year', 'N/A')
        abstract = getattr(r, 'abstract', '') or ''
        snippet = abstract[:300] + '...' if len(abstract) > 300 else abstract
        author_str = ', '.join(authors[:3]) if authors else 'Unknown'
        if len(authors) > 3:
            author_str += ' et al.'
        lines.append(
            f"[{i+1}] {title} ({year})\\n"
            f"    Authors: {author_str}\\n"
            f"    Snippet: {snippet}"
        )
    return '\\n\\n'.join(lines)
