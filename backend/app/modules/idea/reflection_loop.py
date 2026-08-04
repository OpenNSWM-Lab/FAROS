"""
Reflection Loop - Per-node iterative LLM reasoning with literature search.

Each BFTS node runs this loop:
  LLM → (ACTION: SearchLiterature | FinalizeIdea) → execute → LLM → ...
  Up to N rounds (default 2, max 5).

Reference: AI-Scientist-v2 ai_scientist/perform_ideation_temp_free.py
Format: ACTION: / ARGUMENTS: blocks (same as AI Scientist v2)
"""

import logging
import re
import json
from typing import Optional, Tuple, Any, List, Dict

from app.models.idea import IdeaNode
from app.services.search_service import get_search_service, SearchResult
from app.llm.provider_client import get_provider_client, ChatMessage, ProviderError

logger = logging.getLogger(__name__)

# --- Tool definitions (matching AI Scientist v2 format) ---

SEARCH_LITERATURE_DESC = """Search for relevant literature using the FAROS search service.

This searches across Semantic Scholar, arXiv, and local corpus.
Provide a specific, targeted query (3-6 words) for best results.
Returns paper titles, authors, years, and abstracts."""

FINALIZE_IDEA_DESC = """Finalize your research idea and provide all details.

The IDEA JSON must include:
- "title": A catchy, informative title (string)
- "problem": Clear problem statement (string)
- "hypothesis": The core hypothesis / key insight (string)
- "abstract": A 150-200 word abstract (string)
- "approach": High-level methodology (string)
- "expectedOutcomes": List of expected outcomes (list of strings)
- "requiredExperiments": List of experiment objects, each with:
    "name", "description", "metrics" (list), "datasets" (list)
- "risks": List of risk objects, each with:
    "risk" (string), "mitigation" (string)
"""


_TOOL_DESCRIPTIONS = f"""- **SearchLiterature**: {SEARCH_LITERATURE_DESC}
- **FinalizeIdea**: {FINALIZE_IDEA_DESC}"""

_TOOL_NAMES_STR = '"SearchLiterature", "FinalizeIdea"'


# --- System prompt for reflection loop ---

_REFLECTION_SYSTEM = f"""You are an experienced AI researcher iteratively refining a research idea.

You have access to ONE tool:
{_TOOL_DESCRIPTIONS}

You MUST respond in EXACTLY this format (two fields, separate lines):

ACTION:
<exactly one of {_TOOL_NAMES_STR}>

ARGUMENTS:
<If ACTION is "SearchLiterature", provide {{"query": "your search query"}}>
<If ACTION is "FinalizeIdea", provide the IDEA JSON as {{"idea": {{...}}}}>

NOTES:
- ACTION and ARGUMENTS must be on separate lines.
- For SearchLiterature: query should be 3-6 words, specific and targeted.
- After 1-2 searches, you should finalize.
- Do NOT repeat the same search query.
- The IDEA JSON in FinalizeIdea must be valid JSON.
- Only finalize when you are confident the idea is novel and distinct from literature."""


def _build_user_prompt(
    seed_query: str,
    paper_type: str,
    round_num: int,
    max_rounds: int,
    current_title: str,
    current_hypothesis: str,
    current_abstract: str,
    literature_context: str,
    tool_results: str,
    reflection_history: List[str],
) -> str:
    """Build the user prompt for a reflection round."""
    history_str = ""
    if reflection_history:
        history_str = "\\n".join(
            f"[Round {i+1}] {h}" for i, h in enumerate(reflection_history)
        )
    else:
        history_str = "(No previous rounds)"

    return f"""Research Topic: {seed_query}
Paper Type: {paper_type}

## Current Idea (Round {round_num}/{max_rounds})
Title: {current_title}
Hypothesis: {current_hypothesis}
Abstract: {current_abstract}

## Known Literature Context
{literature_context if literature_context else "(None yet)"}

## Previous Search Results
{tool_results if tool_results else "(No tool results yet)"}

## Reflection History
{history_str}

Based on the above, what should you do next?
- Call SearchLiterature with a NEW query to gather more information
- Call FinalizeIdea when the idea is ready and well-formed

Respond in the exact ACTION / ARGUMENTS format."""


def _parse_action(text: str) -> Tuple[Optional[str], Optional[Any]]:
    """Parse ACTION / ARGUMENTS from LLM response.

    Supports two formats:
    1. AI Scientist v2 format: ACTION:\\n<name>\\nARGUMENTS:\\n<json>
    2. JSON block: {{"action": "...", "arguments": ...}}

    Returns:
        (action_name, arguments) or (None, None) if parse fails.
    """
    # Try format 1: ACTION: / ARGUMENTS: blocks
    action_match = re.search(
        r'ACTION:\s*\n?\s*(\w+)', text, re.IGNORECASE
    )
    if action_match:
        action_name = action_match.group(1).strip()
        # Extract ARGUMENTS block
        args_match = re.search(
            r'ARGUMENTS:\s*\n?\s*(\{[\s\S]*\})', text, re.IGNORECASE
        )
        if args_match:
            try:
                arguments = json.loads(args_match.group(1))
                return action_name, arguments
            except json.JSONDecodeError:
                pass
        # ACTION found but no valid ARGUMENTS — return action only
        return action_name, None

    # Try format 2: JSON block
    json_match = re.search(r'\{[\s\S]*"(action|ACTION)"[\s\S]*\}', text)
    if json_match:
        try:
            data = json.loads(json_match.group())
            action_name = data.get("action") or data.get("ACTION")
            arguments = data.get("arguments") or data.get("ARGUMENTS")
            if action_name:
                return action_name, arguments
        except (json.JSONDecodeError, AttributeError):
            pass

    return None, None


def _execute_search(query: str, limit: int = 5) -> str:
    """Execute a literature search via FAROS search_service.

    Returns:
        Formatted string of search results for LLM consumption.
    """
    try:
        search_service = get_search_service()
        results = search_service.search(query, limit=limit)
        if not results:
            return f"(No results found for query: '{query}')"

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
                f"    Abstract: {snippet}"
            )
        return "\\n\\n".join(lines)
    except Exception as e:
        logger.warning(f"Search execution failed for query '{query}': {e}")
        return f"(Search failed: {e})"


def _execute_finalize(
    arguments: Any,
    node: IdeaNode,
    session_id: str,
) -> IdeaNode:
    """Execute FinalizeIdea action: populate node with finalized idea data.

    Args:
        arguments: The IDEA JSON from LLM (dict with "idea" key, or direct dict)
        node: The IdeaNode to populate
        session_id: For generating candidate IDs

    Returns:
        Updated IdeaNode with isTerminal=True
    """
    # Handle nested "idea" key (AI Scientist v2 format)
    idea_data = arguments
    if isinstance(arguments, dict):
        if "idea" in arguments:
            idea_data = arguments["idea"]
        elif "IDEA" in arguments:
            idea_data = arguments["IDEA"]

    if not isinstance(idea_data, dict):
        logger.warning(f"FinalizeIdea: arguments is not a dict: {type(idea_data)}")
        return node

    # Populate node fields
    node.title = idea_data.get("title", node.title or "Untitled Idea")
    node.hypothesis = idea_data.get(
        "hypothesis", idea_data.get("problem", node.hypothesis or "")
    )
    node.abstract = idea_data.get("abstract", node.abstract or "")
    node.experiments = idea_data.get(
        "requiredExperiments", idea_data.get("experiments", node.experiments)
    )
    node.risks = idea_data.get(
        "risks", idea_data.get("riskFactors", node.risks)
    )
    if "expectedOutcomes" in idea_data:
        # Store as part of abstract or a separate field if needed
        pass

    node.isTerminal = True
    from datetime import UTC, datetime
    node.finalizedAt = datetime.now(UTC)

    logger.info(f"Node {node.nodeId} finalized: '{node.title}'")
    return node


class ReflectionLoop:
    """Runs the iterative reflection loop for one BFTS node.

    Flow per round:
      1. Build prompt (system + user with current idea state)
      2. Call LLM
      3. Parse ACTION / ARGUMENTS
      4. Execute action:
         - SearchLiterature → update tool_results, continue loop
         - FinalizeIdea → populate node, return
      5. If max rounds reached without finalize → return None (node abandoned)
    """

    def __init__(
        self,
        provider_name: str,
        model: str,
        seed_query: str,
        paper_type: str = "algorithm",
        max_rounds: int = 2,
        literature_context: str = "",
    ):
        self.provider_name = provider_name
        self.model = model
        self.seed_query = seed_query
        self.paper_type = paper_type
        self.max_rounds = max_rounds
        self.literature_context = literature_context
        self._client = None

    @property
    def client(self):
        if self._client is None:
            self._client = get_provider_client(self.provider_name)
        return self._client

    def run(self, node: IdeaNode) -> Optional[IdeaNode]:
        """Run reflection loop on a node. Returns finalized node or None.

        Args:
            node: The IdeaNode to refine. Must have title and hypothesis set.

        Returns:
            The finalized IdeaNode (isTerminal=True), or None if abandoned.
        """
        if not node.title and not node.hypothesis:
            logger.warning(f"Node {node.nodeId} has no title or hypothesis, skipping reflection")
            return None

        tool_results = ""
        reflection_history: List[str] = []

        for round_num in range(1, self.max_rounds + 1):
            node.reflectionRounds = round_num
            current_title = node.title or "(not yet titled)"
            current_hypothesis = node.hypothesis or "(hypothesis pending)"
            current_abstract = node.abstract or "(abstract pending)"

            user_prompt = _build_user_prompt(
                seed_query=self.seed_query,
                paper_type=self.paper_type,
                round_num=round_num,
                max_rounds=self.max_rounds,
                current_title=current_title,
                current_hypothesis=current_hypothesis,
                current_abstract=current_abstract,
                literature_context=self.literature_context,
                tool_results=tool_results,
                reflection_history=reflection_history,
            )

            messages = [
                ChatMessage(role="system", content=_REFLECTION_SYSTEM),
                ChatMessage(role="user", content=user_prompt),
            ]

            try:
                response = self.client.chat(
                    messages=messages,
                    model=self.model,
                    max_tokens=2000,
                )
                response_text = response.text
            except ProviderError as e:
                logger.error(f"LLM call failed in reflection round {round_num}: {e}")
                break

            # Record reflection history
            reflection_history.append(response_text[:500])
            node.reflectionHistory.append(response_text)

            # Parse action
            action_name, arguments = _parse_action(response_text)

            if action_name is None:
                logger.warning(
                    f"Round {round_num}: could not parse ACTION from response: {response_text[:200]}"
                )
                # Try to extract idea JSON as fallback
                try:
                    json_match = re.search(r'\{[\s\S]*"title"[\s\S]*\}', response_text)
                    if json_match:
                        arguments = json.loads(json_match.group())
                        node = _execute_finalize({"idea": arguments}, node, node.sessionId)
                        return node
                except Exception:
                    pass
                # If still no action, continue to next round
                continue

            action_name = action_name.strip()

            # Execute action
            if action_name == "SearchLiterature":
                query = ""
                if isinstance(arguments, dict):
                    query = arguments.get("query", arguments.get("query", ""))
                if not query:
                    logger.warning(f"Round {round_num}: SearchLiterature with no query")
                    continue
                logger.info(f"Round {round_num}: SearchLiterature query='{query}'")
                tool_results = _execute_search(query, limit=5)

            elif action_name in ("FinalizeIdea", "FinalizeIdea"):
                logger.info(f"Round {round_num}: FinalizeIdea for node {node.nodeId}")
                node = _execute_finalize(arguments, node, node.sessionId)
                return node

            else:
                logger.warning(f"Round {round_num}: unknown action '{action_name}'")
                continue

        # Max rounds reached without finalize
        logger.info(
            f"Node {node.nodeId} abandoned after {self.max_rounds} rounds without finalize"
        )
        return None
