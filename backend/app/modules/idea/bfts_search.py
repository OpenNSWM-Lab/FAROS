"""
BFTS Search Tree - Core engine for Step 5 idea generation.

Replaces the single-shot LLM brainstorm with a tree search:
  - Initialize N seed nodes from ReasoningPathSeed[]
  - For each leaf node: run ReflectionLoop (iterative LLM + literature search)
  - Expand top-scoring nodes (beam search)
  - Prune duplicates, check convergence
  - Return all terminal nodes as IdeaCandidate[]

Reference: AI-Scientist-v2 ai_scientist/treesearch/bfts_utils.py
"""

import heapq
import logging
import re
from collections import defaultdict
from typing import List, Optional, Set, Tuple, Dict, Any
from datetime import UTC, datetime

from app.models.idea import (
    IdeaNode,
    BFTSConfig,
    ReasoningPathSeed,
    PathSeedStep,
    StructuredPaper,
    IdeaCandidate,
    RiskItem,
    ExperimentSpec,
    DraftPlan,
    generate_candidate_id,
)
from app.modules.idea.reflection_loop import ReflectionLoop, FINALIZE_IDEA_DESC
from app.services.search_service import get_search_service
from app.services import prompts as global_prompts
from app.llm.provider_client import get_provider_client, ChatMessage

logger = logging.getLogger(__name__)

# --- Default scoring weights (overridden by BFTSConfig.scoreWeights) ---
DEFAULT_WEIGHTS = {
    "novelty": 0.35,
    "feasibility": 0.20,
    "impact": 0.15,
    "specificity": 0.10,
    "evidenceSupport": 0.10,
    "graphGrounding": 0.10,
}


def _build_literature_context(structured_papers: List[StructuredPaper], limit: int = 8) -> str:
    """Format selected papers as context string for LLM prompts."""
    if not structured_papers:
        return "(No literature context available yet)"
    lines = []
    for i, sp in enumerate(structured_papers[:limit]):
        title = sp.title or "(untitled)"
        year = sp.year or "N/A"
        claims = sp.claims[:2] if sp.claims else []
        claims_str = ". ".join(c.text[:150] for c in claims) if claims else "N/A"
        lines.append(
            f"[{i+1}] {title} ({year})\\n"
            f"    Key claims: {claims_str}"
        )
    return "\\n\\n".join(lines)


def _path_seed_to_idea_node(
    seed: ReasoningPathSeed,
    session_id: str,
    literature_context: str,
    provider_name: str,
    model: str,
    max_reflection_rounds: int = 2,
) -> Optional[IdeaNode]:
    """Convert a ReasoningPathSeed into an initialized IdeaNode via LLM.

    Uses a one-shot LLM call to generate an initial idea
    from the path seed skeleton.
    Returns None if LLM call fails.
    """
    from app.modules.idea.treesearch_prompts import BFTS_SEED_SYSTEM, BFTS_SEED_USER

    try:
        client = get_provider_client(provider_name)

        # Build path steps text
        steps_text = ""
        for i, step in enumerate(seed.steps[:5]):
            steps_text += f"  Step {i+1}: {step.description} (type: {step.stepType})\\n"

        user_prompt = BFTS_SEED_USER.format(
            seed_query=seed.sessionId or "research",  # fallback
            paper_type="algorithm",  # default; overridden by caller context
            template_type=seed.templateType or "generic",
            anchor_entities=", ".join(seed.anchorEntityIds[:5]) or "(none)",
            path_steps=steps_text or "  (no steps defined)",
            rationale=seed.rationale or "(not specified)",
            literature_context=literature_context or "(none)",
        )

        messages = [
            ChatMessage(role="system", content=BFTS_SEED_SYSTEM),
            ChatMessage(role="user", content=user_prompt),
        ]
        response = client.chat(messages=messages, model=model, max_tokens=1500)

        # Parse the idea JSON from response
        import json, re
        idea_data = None
        try:
            # Try to find JSON block
            json_match = re.search(r'\{[\s\S]*"title"[\s\S]*\}', response.text)
            if json_match:
                idea_data = json.loads(json_match.group())
            else:
                idea_data = json.loads(response.text)
        except (json.JSONDecodeError, AttributeError):
            # Fallback: extract fields manually
            logger.warning(f"Could not parse JSON from seed LLM for {seed.seedId}")
            return _create_fallback_node(seed, session_id)

        if not isinstance(idea_data, dict):
            return _create_fallback_node(seed, session_id)

        node = IdeaNode(
            nodeId=generate_idea_node_id(),
            sessionId=session_id,
            depth=0,
            sourceSeedId=seed.seedId,
            title=idea_data.get("title", f"Idea from {seed.templateType}"),
            hypothesis=idea_data.get("hypothesis", idea_data.get("problem", "")),
            abstract=idea_data.get("abstract", ""),
            experiments=idea_data.get("requiredExperiments", idea_data.get("experiments", [])),
            risks=idea_data.get("risks", []),
        )
        return node

    except Exception as e:
        logger.error(f"Failed to initialize node from seed {seed.seedId}: {e}")
        return _create_fallback_node(seed, session_id)


def _create_fallback_node(seed: ReasoningPathSeed, session_id: str) -> IdeaNode:
    """Create a minimal fallback node from a path seed (no LLM needed)."""
    template = seed.templateType or "generic"
    return IdeaNode(
        nodeId=generate_idea_node_id(),
        sessionId=session_id,
        depth=0,
        sourceSeedId=seed.seedId,
        title=f"{template.title()} idea from path seed",
        hypothesis=seed.rationale or f"Exploring {template} direction",
        abstract=f"Research idea generated from {template} path seed. "
                f"Anchor entities: {', '.join(seed.anchorEntityIds[:3])}.",
    )


def _nodes_to_candidates(
    nodes: List[IdeaNode], session_id: str
) -> List[IdeaCandidate]:
    """Convert terminal IdeaNode[] to IdeaCandidate[] for downstream ranking."""
    candidates = []
    for node in nodes:
        if not node.isTerminal and node.title:
            # Non-terminal nodes: still include if they have content
            pass
        candidate = IdeaCandidate(
            id=generate_candidate_id(),
            sessionId=session_id,
            title=node.title or "Untitled Idea",
            problem=node.hypothesis or "Problem statement pending.",
            keyInsight=node.hypothesis or node.abstract[:200] if node.abstract else "Key insight pending.",
            novelty=node.noveltyScore,
            noveltyRationale=f"From BFTS node {node.nodeId}, "
                         f"reflection rounds: {node.reflectionRounds}",
            feasibility=node.feasibilityScore,
            feasibilityRationale=f"Graph grounding: {node.graphGroundingScore:.2f}, "
                                f"Evidence: {node.evidenceSupportScore:.2f}",
            impact=node.impactScore,
            impactRationale=f"Combined score: {node.combinedScore:.2f}",
            scoringMethod="bfts_tree_search",
            risks=[
                RiskItem(
                    risk=r.get("risk", str(r)) if isinstance(r, dict) else str(r),
                    mitigation=r.get("mitigation", "") if isinstance(r, dict) else "",
                )
                for r in (node.risks or [])
            ],
            requiredExperiments=[
                ExperimentSpec(
                    name=e.get("name", "Experiment") if isinstance(e, dict) else str(e),
                    description=e.get("description", "") if isinstance(e, dict) else "",
                    metrics=e.get("metrics", []) if isinstance(e, dict) else [],
                    datasets=e.get("datasets", []) if isinstance(e, dict) else [],
                )
                for e in (node.experiments or [])
            ],
            expectedMetrics=[],
            draftPlan=DraftPlan(
                researchQuestion=node.hypothesis or "",
                hypothesis=node.hypothesis or "",
                methodology=node.abstract[:300] if node.abstract else "To be defined",
                expectedOutcomes=[
                    m for m in (node.experiments or [])
                    if isinstance(m, str)
                ][:5],
            ),
        )
        candidates.append(candidate)
    return candidates


class BFTSSearchTree:
    """Best-First Tree Search for idea generation.

    Algorithm:
      1. Initialize seed nodes from path seeds
      2. Main loop:
         a. Score all unexpanded leaf nodes
         b. Select top-K by combined score (beam)
         c. Run ReflectionLoop on each
         d. If expanded nodes produce children, add to tree
         e. Mark parents as expanded
         f. Check convergence
      3. Return all terminal nodes as candidates
    """

    def __init__(
        self,
        session_id: str,
        bfts_config: BFTSConfig,
        provider_name: str,
        model: str,
        path_seeds: List[ReasoningPathSeed],
        structured_papers: List[StructuredPaper],
        literature_context: str = "",
        seed_query: str = "",
        paper_type: str = "algorithm",
    ):
        self.session_id = session_id
        self.config = bfts_config
        self.provider_name = provider_name
        self.model = model
        self.literature_context = literature_context
        self.seed_query = seed_query
        self.paper_type = paper_type

        # Tree storage
        self.nodes: List[IdeaNode] = []
        self._parent_map: Dict[str, str] = {}  # nodeId -> parentNodeId
        self._children_map: Dict[str, Any] = defaultdict(set)

        # Beam: min-heap of (-combinedScore, nodeId, depth)
        self._beam: List[Any] = []

        # Initialization
        self._init_seeds(path_seeds, structured_papers)

    def _init_seeds(
        self,
        path_seeds: List[ReasoningPathSeed],
        structured_papers: List[StructuredPaper],
    ) -> None:
        """Initialize seed nodes from ReasoningPathSeed[]."""
        if not path_seeds:
            logger.warning("No path seeds provided, creating default seed")
            default_seed = ReasoningPathSeed(
                seedId="default_seed",
                sessionId=self.session_id,
                templateType="generic",
                anchorEntityIds=[],
                steps=[],
                rationale="Default seed (no path seeds available)",
            )
            path_seeds = [default_seed]

        literature_context = _build_literature_context(structured_papers)

        # Limit initial seeds to beam_width
        init_seeds = path_seeds[:self.config.beamWidth]

        for seed in init_seeds:
            node = _path_seed_to_idea_node(
                seed=seed,
                session_id=self.session_id,
                literature_context=literature_context,
                provider_name=self.provider_name,
                model=self.model,
                max_reflection_rounds=1,  # Just initialize, no deep reflection yet
            )
            if node:
                self.nodes.append(node)
                heapq.heappush(self._beam, (-node.combinedScore, node.nodeId, node.depth))
                logger.info(f"Initialized seed node: {node.nodeId} '{node.title[:50]}...'")

        if not self.nodes:
            raise ValueError("Failed to initialize any seed nodes from path seeds")

    def run(self) -> List[IdeaCandidate]:
        """Execute the BFTS main loop. Returns IdeaCandidate[]."""
        max_nodes = min(self.config.maxNodes, 60)  # Safety cap
        beam_width = max(1, self.config.beamWidth)
        max_reflection = self.config.maxReflectionRounds

        logger.info(
            f"BFTS: starting with {len(self.nodes)} seeds, "
            f"max_nodes={max_nodes}, beam_width={beam_width}, "
            f"max_reflection={max_reflection}"
        )

        # Main expansion loop
        expansion_count = 0
        while len(self.nodes) < max_nodes:
            # Get leaf nodes (not expanded, not terminal)
            uneexpanded_leaves = [
                n for n in self.nodes
                if not n.isExpanded and not n.isTerminal
            ]
            if not uneexpanded_leaves:
                logger.info("BFTS: no more uneexpanded leaves, stopping")
                break

            # Score and sort leaves
            for n in uneexpanded_leaves:
                self._score_node(n)
            uneexpanded_leaves.sort(key=lambda n: n.combinedScore, reverse=True)

            # Select top-K for expansion
            top_k = uneexpanded_leaves[:beam_width]
            if not top_k:
                break

            logger.info(
                f"BFTS: expanding {len(top_k)} nodes "
                f"(total nodes: {len(self.nodes)}/{max_nodes})"
            )

            # Run reflection loop on each top-K node
            for parent in top_k:
                if len(self.nodes) >= max_nodes:
                    break
                if parent.isExpanded or parent.isTerminal:
                    continue

                child = self._expand_node(parent, max_reflection)
                if child:
                    self._add_child(parent, child)
                    expansion_count += 1

                parent.isExpanded = True

            # Check convergence
            if self._has_converged():
                logger.info("BFTS: converged, stopping")
                break

        # Collect results: all terminal nodes + top uneexpanded leaves
        terminal_nodes = [n for n in self.nodes if n.isTerminal]
        if not terminal_nodes:
            # Fallback: take top-scored nodes even if not terminal
            for n in self.nodes:
                self._score_node(n)
            terminal_nodes = sorted(
                [n for n in self.nodes if n.title],
                key=lambda n: n.combinedScore,
                reverse=True,
            )[:self.config.beamWidth * 2]

        logger.info(
            f"BFTS: completed with {len(self.nodes)} nodes, "
            f"{len(terminal_nodes)} terminal ideas"
        )

        return _nodes_to_candidates(terminal_nodes, self.session_id)

    def _expand_node(self, parent: IdeaNode, max_reflection: int) -> Optional[IdeaNode]:
        """Run ReflectionLoop on a parent node. Returns child node or None."""
        literature_context = self._get_literature_context_for_node(parent)

        loop = ReflectionLoop(
            provider_name=self.provider_name,
            model=self.model,
            seed_query=self.session_id,  # Will be overridden by service.py
            paper_type="algorithm",  # Overridden by service.py
            max_rounds=max_reflection,
            literature_context=literature_context,
        )

        result_node = loop.run(parent)

        if result_node and result_node.isTerminal:
            # result_node is the refined version of parent
            # Create a child node with the refined content
            child = IdeaNode(
                nodeId=generate_idea_node_id(),
                sessionId=self.session_id,
                parentNodeId=parent.nodeId,
                depth=parent.depth + 1,
                sourceSeedId=parent.sourceSeedId,
                title=result_node.title,
                hypothesis=result_node.hypothesis,
                abstract=result_node.abstract,
                experiments=result_node.experiments,
                risks=result_node.risks,
                noveltyScore=result_node.noveltyScore,
                feasibilityScore=result_node.feasibilityScore,
                impactScore=result_node.impactScore,
                combinedScore=result_node.combinedScore,
                reflectionRounds=result_node.reflectionRounds,
                isTerminal=True,
                finalizedAt=result_node.finalizedAt,
            )
            return child

        return None

    def _add_child(self, parent: IdeaNode, child: IdeaNode) -> None:
        """Add a child node to the tree."""
        child.parentNodeId = parent.nodeId
        child.depth = parent.depth + 1
        self.nodes.append(child)
        self._parent_map[child.nodeId] = parent.nodeId
        self._children_map[parent.nodeId].add(child.nodeId)

        # Push to beam
        heapq.heappush(
            self._beam, (-child.combinedScore, child.nodeId, child.depth)
        )

    def _score_node(self, node: IdeaNode) -> None:
        """Score a node using BFTSConfig.scoreWeights + PathSeed priors."""
        weights = self.config.scoreWeights or DEFAULT_WEIGHTS

        # Get prior scores from source seed if available
        prior_novelty = 0.5
        prior_feasibility = 0.5
        if node.sourceSeedId:
            prior = self._get_seed_prior(node.sourceSeedId)
            if prior:
                prior_novelty = prior.noveltyPrior
                prior_feasibility = prior.feasibilityPrior

        # Compute scores (LLM-generated ideas may have text fields; use heuristics)
        # Novelty: based on hypothesis uniqueness (heuristic)
        if node.noveltyScore == 0.0:
            node.noveltyScore = self._estimate_novelty(node, prior_novelty)

        # Feasibility: based on experiment concreteness
        if node.feasibilityScore == 0.0:
            node.feasibilityScore = self._estimate_feasibility(node, prior_feasibility)

        # Impact: based on hypothesis boldness
        if node.impactScore == 0.0:
            node.impactScore = self._estimate_impact(node)

        # Specificity: based on title/concept concreteness
        node.specificityScore = self._estimate_specificity(node)

        # Evidence support: based on literature_context grounding
        node.evidenceSupportScore = self._estimate_evidence_support(node)

        # Graph grounding: based on source seed graph alignment
        node.graphGroundingScore = self._estimate_graph_grounding(node)

        # Combined score
        node.combinedScore = (
            weights.get("novelty", 0.35) * node.noveltyScore / 10.0
            + weights.get("feasibility", 0.20) * node.feasibilityScore / 10.0
            + weights.get("impact", 0.15) * node.impactScore / 10.0
            + weights.get("specificity", 0.10) * node.specificityScore
            + weights.get("evidenceSupport", 0.10) * node.evidenceSupportScore
            + weights.get("graphGrounding", 0.10) * node.graphGroundingScore
        ) * 10.0  # Scale back to 0-10

    def _estimate_novelty(self, node: IdeaNode, prior: float) -> float:
        """Estimate novelty: heuristic based on hypothesis text."""
        import re
        # Check for novel-sounding keywords
        novel_kw = ["novel", "new", "unexplored", "first", "towards", "beyond"]
        hyp = (node.hypothesis or "").lower()
        title = (node.title or "").lower()
        kw_score = sum(1 for kw in novel_kw if kw in hyp or kw in title)
        text_score = min(1.0, kw_score / 3.0)
        return min(10.0, max(0.0, (prior + text_score) * 5.0))

    def _estimate_feasibility(self, node: IdeaNode, prior: float) -> float:
        """Estimate feasibility: based on experiment count and concreteness."""
        exp_count = len(node.experiments or [])
        has_concrete_experiments = exp_count >= 1
        score = prior * 10.0
        if has_concrete_experiments:
            score += min(2.0, exp_count * 0.5)
        return min(10.0, max(0.0, score))

    def _estimate_impact(self, node: IdeaNode) -> float:
        """Estimate impact: based on hypothesis ambition."""
        hyp = (node.hypothesis or "").lower()
        impact_kw = ["improving", "outperforms", "state-of-the-art", "SOTA", "significant"]
        kw_score = sum(1 for kw in impact_kw if kw in hyp)
        return min(10.0, 5.0 + kw_score * 1.5)

    def _estimate_specificity(self, node: IdeaNode) -> float:
        """Estimate specificity: 0-1, based on title concreteness."""
        title = node.title or ""
        # Count specific nouns / named entities (heuristic)
        words = re.findall(r'\b[A-Z][a-z]{2,}\b', title)
        return min(1.0, len(words) / 5.0)

    def _estimate_evidence_support(self, node: IdeaNode) -> float:
        """Estimate evidence support: 0-1, based on reflection rounds and search results."""
        # More reflection rounds = more literature grounding
        return min(1.0, node.reflectionRounds / max(1, self.config.maxReflectionRounds))

    def _estimate_graph_grounding(self, node: IdeaNode) -> float:
        """Estimate graph grounding: 0-1, based on source seed."""
        if node.sourceSeedId:
            return 0.7  # Has a reasoning path seed backing
        return 0.3

    def _get_seed_prior(self, seed_id: str) -> Optional[Any]:
        """Get PathSeedScores prior for a seed."""
        # This requires access to path_seed storage
        # For now, return None (use defaults)
        return None

    def _get_literature_context_for_node(self, node: IdeaNode) -> str:
        """Get literature context string for a node's reflection loop."""
        # Use the top selected papers from storage
        # Simplified: just return a generic context
        return "(Literature context available from Step 3 selected papers)"

    def _has_converged(self) -> bool:
        """Check convergence: - Scores have low variance, or
        - All high-scoring nodes are terminal."""
        terminal = [n for n in self.nodes if n.isTerminal]
        if len(terminal) >= self.config.beamWidth:
            return True

        # Check score variance
        scores = [n.combinedScore for n in self.nodes if n.combinedScore > 0]
        if len(scores) >= 3:
            mean = sum(scores) / len(scores)
            variance = sum((s - mean) ** 2 for s in scores) / len(scores)
            if variance < 0.5:  # Low variance = converged
                return True

        return False


def generate_idea_node_id() -> str:
    """Generate a unique IdeaNode ID."""
    import uuid
    return "in_" + uuid.uuid4().hex[:12]
