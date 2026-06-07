"""
Path Seed Generator

Generates ReasoningPathSeed[] from ReasoningKG by traversing high-importance
entity paths. SMAR+NIE-inspired: node importance → root selection → BFS traversal.

Each seed is a chain of (entity → relation → entity) steps with scores.
"""

import logging
import uuid
from collections import defaultdict
from typing import List, Dict, Set, Tuple, Optional, Any

from app.models.idea import (
    ReasoningKG,
    GraphEvidenceLink,
    StructuredPaper,
    LiteratureMap,
    ReasoningPathSeed,
    PathSeedStep,
    PathSeedScores,
    KGEntity,
    KGRelation,
    GapEvidence,
    FrontierSignal,
)

logger = logging.getLogger(__name__)

# Paper type taxonomy mapping
PAPER_TYPE_KEYWORDS: Dict[str, List[str]] = {
    "algorithm": ["algorithm", "method", "architecture", "model", "training", "optimization"],
    "system": ["system", "framework", "pipeline", "infrastructure", "deployment", "scaling"],
    "application": ["application", "domain", "task", "dataset", "benchmark", "real-world"],
    "benchmark": ["benchmark", "evaluation", "comparison", "metric", "performance", "baseline"],
    "survey": ["survey", "review", "taxonomy", "overview", "systematic", "literature"],
    "position": ["position", "vision", "future", "challenge", "opportunity", "direction"],
    "theory": ["theory", "theorem", "proof", "mathematical", "formal", "bound"],
    "evaluation": ["evaluation", "analysis", "study", "empirical", "experiment", "ablation"],
    "safety": ["safety", "alignment", "fairness", "bias", "robustness", "security"],
}


class PathSeedGenerator:
    """Generates reasoning path seeds from Graph 2."""

    def generate_seeds(
        self,
        session_id: str,
        reasoning_kg: ReasoningKG,
        evidence_links: List[GraphEvidenceLink],
        structured_papers: List[StructuredPaper],
        literature_map: LiteratureMap,
        max_seeds: int = 10,
    ) -> List[ReasoningPathSeed]:
        """Generate reasoning path seeds.

        1. Select root entities by importance score
        2. BFS traversal up to depth 3
        3. Score paths (novelty, feasibility, impact, evidenceStrength)
        4. Filter and return top seeds
        """
        if not reasoning_kg.entities or not reasoning_kg.relations:
            return []

        # Build adjacency for traversal
        adj_out: Dict[str, List[Tuple[str, KGRelation]]] = defaultdict(list)
        for rel in reasoning_kg.relations:
            adj_out[rel.sourceEntityId].append((rel.targetEntityId, rel))
            # Also traverse reverse direction for bi-directional BFS
            adj_out[rel.targetEntityId].append((rel.sourceEntityId, rel))

        # Build claim/paper index from structured papers
        claim_paper_map: Dict[str, str] = {}
        claim_confidence: Dict[str, float] = {}
        for sp in structured_papers:
            for c in sp.claims:
                claim_paper_map[c.claimId] = sp.rawPaperId
                claim_confidence[c.claimId] = c.confidence

        # Build evidence link index
        evidence_by_entity: Dict[str, List[GraphEvidenceLink]] = defaultdict(list)
        for link in evidence_links:
            for eid in link.targetEntityIds:
                evidence_by_entity[eid].append(link)

        # Novelty evidence index from LiteratureMap
        novelty_directions: Set[str] = set()
        for ne in literature_map.noveltyEvidence:
            if ne.get("assessment") == "supports":
                novelty_directions.add(ne.get("direction", "").lower())

        # 1. Select root entities
        entities_by_importance = sorted(
            reasoning_kg.entities,
            key=lambda e: e.importanceScore,
            reverse=True,
        )
        root_entities = entities_by_importance[:5]

        # 2. BFS path traversal from each root
        all_seeds: List[Tuple[List[PathSeedStep], List[str], List[str]]] = []

        for root in root_entities:
            # BFS: (entity_id, path_steps, depth)
            queue: List[Tuple[str, List[PathSeedStep]]] = [
                (root.entityId, [PathSeedStep(
                    stepIndex=0,
                    stepType="observation",
                    entityId=root.entityId,
                    relationId=None,
                    text=root.name,
                    description=f"Start: {root.name}",
                    required=True,
                )])
            ]
            visited: Set[str] = {root.entityId}

            while queue:
                current_id, steps = queue.pop(0)
                if len(steps) >= 4:  # Depth limit (root + 3 steps)
                    continue

                for next_id, rel in adj_out.get(current_id, []):
                    if next_id in visited:
                        continue
                    visited.add(next_id)

                    next_entity = next(
                        (e for e in reasoning_kg.entities if e.entityId == next_id),
                        None,
                    )
                    desc = f"{rel.relationType}: {next_entity.name if next_entity else next_id}"
                    step_type = self._relation_to_step_type(rel.relationType)

                    new_steps = steps + [PathSeedStep(
                        stepIndex=len(steps),
                        stepType=step_type,
                        entityId=next_id,
                        relationId=rel.relationId,
                        text=desc,
                        description=desc,
                        required=True,
                        evidencePaperIds=list(dict.fromkeys(rel.sourcePaperIds))[:5],
                    )]
                    queue.append((next_id, new_steps))

                    # Collect all source paper/claim IDs along path
                    source_papers: List[str] = []
                    source_claims: List[str] = []
                    for step in new_steps:
                        if step.relationId:
                            matching_rel = next(
                                (r for r in reasoning_kg.relations
                                 if r.relationId == step.relationId),
                                None,
                            )
                            if matching_rel:
                                source_papers.extend(matching_rel.sourcePaperIds)
                                source_claims.extend(matching_rel.sourceClaimIds)
                        # Also collect from entity
                        matching_ent = next(
                            (e for e in reasoning_kg.entities
                             if e.entityId == step.entityId),
                            None,
                        )
                        if matching_ent:
                            source_papers.extend(matching_ent.sourcePaperIds)
                            source_claims.extend(matching_ent.sourceClaimIds)

                    # Dedup
                    source_papers = list(dict.fromkeys(source_papers))[:10]
                    source_claims = list(dict.fromkeys(source_claims))[:10]

                    if len(new_steps) >= 2:  # At least one relation step
                        all_seeds.append((new_steps, source_papers, source_claims))

        # 3. Score paths
        scored_seeds = []
        for steps, papers, claims in all_seeds:
            scores = self._score_path(
                steps, papers, claims,
                evidence_by_entity, claim_confidence, novelty_directions,
                reasoning_kg,
            )
            scored_seeds.append((steps, papers, claims, scores))

        # 4. Filter and sort
        scored_seeds = [
            s for s in scored_seeds
            if s[3].evidencePrior >= 0.2  # Minimum grounding
        ]
        scored_seeds.sort(
            key=lambda s: s[3].noveltyPrior + s[3].feasibilityPrior + s[3].evidencePrior,
            reverse=True,
        )

        # 5. Build ReasoningPathSeed objects
        result = []
        evidence_link_ids = [l.linkId for l in evidence_links]
        gap_ids = [g.direction for g in getattr(literature_map, 'gaps', [])][:5]

        for steps, papers, claims, scores in scored_seeds[:max_seeds]:
            entity_names = []
            for step in steps:
                entity = next(
                    (e for e in reasoning_kg.entities if e.entityId == step.entityId),
                    None,
                )
                if entity:
                    entity_names.append(entity.name.lower())

            paper_types = self._classify_paper_types(entity_names)
            template_type = paper_types[0] if paper_types else "generic"

            result.append(ReasoningPathSeed(
                seedId=f"rps_{uuid.uuid4().hex[:12]}",
                sessionId=session_id,
                reasoningKgId=reasoning_kg.id,
                templateType=template_type,
                anchorEntityIds=[steps[0].entityId] if steps else [],
                steps=steps,
                skeleton=steps[:3],  # First 3 steps as skeleton
                sourcePaperIds=papers,
                sourceClaimIds=claims,
                evidenceLinkIds=evidence_link_ids[:5],
                linkedGapIds=gap_ids,
                linkedFrontierIds=[],
                linkedNoveltyEvidenceIds=[],
                paperTypes=paper_types,
                initialScores=scores,
                scores=scores,
                rationale=f"Path from {entity_names[0] if entity_names else 'root'} via {len(steps)-1} relations",
            ))

        logger.info(
            "Generated %d path seeds from %d entities (scored %d paths, kept %d)",
            len(result), len(root_entities), len(scored_seeds), len(result),
        )
        return result

    def _score_path(
        self,
        steps: List[PathSeedStep],
        papers: List[str],
        claims: List[str],
        evidence_by_entity: Dict[str, List[GraphEvidenceLink]],
        claim_confidence: Dict[str, float],
        novelty_directions: Set[str],
        reasoning_kg: ReasoningKG,
    ) -> PathSeedScores:
        """Score a reasoning path on 4 dimensions (PDF naming)."""
        # Novelty prior: fraction of entities linked to novelty evidence
        novelty_linked = 0
        for step in steps:
            elinks = evidence_by_entity.get(step.entityId, [])
            if any(l.signalType == "novelty" for l in elinks):
                novelty_linked += 1
        novelty = novelty_linked / max(len(steps), 1)

        # Feasibility prior: fraction of entities that are method-type
        method_count = 0
        for step in steps:
            entity = next(
                (e for e in reasoning_kg.entities if e.entityId == step.entityId),
                None,
            )
            if entity and entity.entityType == "method":
                method_count += 1
        feasibility = min(1.0, (method_count / max(len(steps), 1)) + 0.3)

        # Evidence prior: fraction of steps backed by claims
        total_relations = sum(1 for s in steps if s.relationId is not None)
        if total_relations == 0:
            evidence = 0.0
        else:
            backed_relations = 0
            for step in steps:
                if step.relationId:
                    rel = next(
                        (r for r in reasoning_kg.relations
                         if r.relationId == step.relationId),
                        None,
                    )
                    if rel and rel.sourceClaimIds:
                        confs = [
                            claim_confidence.get(cid, 0.5)
                            for cid in rel.sourceClaimIds
                        ]
                        if confs and max(confs) >= 0.5:
                            backed_relations += 1
            evidence = backed_relations / total_relations

        # Graph alignment prior: entity importance along path
        impact_scores = []
        for step in steps:
            entity = next(
                (e for e in reasoning_kg.entities if e.entityId == step.entityId),
                None,
            )
            if entity:
                impact_scores.append(entity.importanceScore)
        alignment = sum(impact_scores) / max(len(impact_scores), 1) if impact_scores else 0.3

        return PathSeedScores(
            noveltyPrior=round(min(1.0, novelty + 0.2), 3),
            feasibilityPrior=round(feasibility, 3),
            evidencePrior=round(evidence, 3),
            graphAlignmentPrior=round(alignment, 3),
        )

    @staticmethod
    def _relation_to_step_type(relation_type: str) -> str:
        """Map KGRelation type to PathSeedStep stepType."""
        mapping = {
            "implies": "mechanism",
            "hypothesizes": "gap",
            "generalizes": "observation",
            "supports": "validation",
            "contradicts": "gap",
            "uses": "method",
            "produces": "prediction",
        }
        return mapping.get(relation_type, "observation")

    @staticmethod
    def _classify_paper_types(entity_names: List[str]) -> List[str]:
        """Classify paper types based on entity name keywords."""
        type_scores: Dict[str, int] = defaultdict(int)
        all_text = " ".join(entity_names)

        for ptype, keywords in PAPER_TYPE_KEYWORDS.items():
            for kw in keywords:
                if kw in all_text:
                    type_scores[ptype] += 1

        if not type_scores:
            return ["algorithm"]

        # Return top-2 matching types
        sorted_types = sorted(type_scores, key=type_scores.get, reverse=True)
        return sorted_types[:2]
