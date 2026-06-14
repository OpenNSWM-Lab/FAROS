"""
Graph Linker — maps Graph 1 signals to Graph 2 entities/relations.

Inspiration: DualGraphRAG's TKG↔SKG evidence linking pattern.
Each signal (cluster, frontier, gap, contradiction, novelty) gets an evidence link
pointing to the relevant ReasoningKG entities and relations.
"""

import logging
import uuid
from typing import List, Dict, Any, Set

from app.models.idea import (
    LiteratureMap,
    ReasoningKG,
    GraphEvidenceLink,
    KGEntity,
    KGRelation,
)

logger = logging.getLogger(__name__)


class GraphLinker:
    """Stateless linker between Graph 1 signals and Graph 2 entities/relations."""

    def link_graphs(
        self,
        literature_map: LiteratureMap,
        reasoning_kg: ReasoningKG,
    ) -> List[GraphEvidenceLink]:
        """Create evidence links from LiteratureMap signals to ReasoningKG.

        Returns a list of GraphEvidenceLink, one per detected signal.
        """
        links: List[GraphEvidenceLink] = []

        # Build lookup indices
        entity_by_name: Dict[str, KGEntity] = {}
        for e in reasoning_kg.entities:
            entity_by_name[e.normalizedName] = e
            entity_by_name[e.name.lower()] = e

        entity_by_paper: Dict[str, List[KGEntity]] = {}
        for e in reasoning_kg.entities:
            for pid in e.sourcePaperIds:
                entity_by_paper.setdefault(pid, []).append(e)

        relation_by_type: Dict[str, List[KGRelation]] = {}
        for r in reasoning_kg.relations:
            relation_by_type.setdefault(r.relationType, []).append(r)

        # 1. Cluster signals: match theme tokens to entity names
        for cluster in literature_map.clusters:
            matching_entities = self._match_tokens_to_entities(
                cluster.themeTokens, entity_by_name
            )
            if matching_entities:
                links.append(GraphEvidenceLink(
                    linkId=f"gel_{uuid.uuid4().hex[:8]}",
                    signalType="cluster",
                    signalId=cluster.clusterId,
                    targetEntityIds=[e.entityId for e in matching_entities],
                    targetRelationIds=[],
                    evidenceType="semantic",
                    rationale=f"Cluster theme '{', '.join(cluster.themeTokens[:3])}' matches {len(matching_entities)} KG entities",
                ))

        # 2. Frontier signals: entities from frontier papers
        for frontier in literature_map.frontiers:
            frontier_pid = frontier.paperId if hasattr(frontier, 'paperId') else str(frontier)
            frontier_entities = entity_by_paper.get(frontier_pid, [])
            if frontier_entities:
                links.append(GraphEvidenceLink(
                    linkId=f"gel_{uuid.uuid4().hex[:8]}",
                    signalType="frontier",
                    signalId=frontier_pid,
                    targetEntityIds=[e.entityId for e in frontier_entities[:5]],
                    targetRelationIds=[],
                    evidenceType="semantic",
                    rationale=f"Frontier paper {frontier_pid} contributes {len(frontier_entities)} entities",
                ))

        # 3. Gap signals: match gap text to entities + hypothesizes relations
        for i, gap in enumerate(literature_map.gaps):
            direction = gap.direction
            gap_entities = self._match_text_to_entities(direction, entity_by_name)
            # Also find "hypothesizes" relations
            hyp_rels = relation_by_type.get("hypothesizes", [])
            gap_rel_ids = [
                r.relationId for r in hyp_rels
                if any(e.entityId in (r.sourceEntityId, r.targetEntityId)
                       for e in gap_entities)
            ][:5]

            links.append(GraphEvidenceLink(
                linkId=f"gel_{uuid.uuid4().hex[:8]}",
                signalType="gap",
                signalId=f"gap_{i}",
                targetEntityIds=[e.entityId for e in gap_entities[:5]],
                targetRelationIds=gap_rel_ids,
                evidenceType="symbolic",
                rationale=f"Gap '{direction[:80]}' linked to {len(gap_entities)} entities and {len(gap_rel_ids)} hypothesizes relations",
            ))

        # 4. Novelty evidence signals
        for i, ne in enumerate(literature_map.noveltyEvidence):
            direction = ne.direction
            assessment = ne.assessment
            ne_entities = self._match_text_to_entities(direction, entity_by_name)

            # Find supports/contradicts relations
            rel_type = "supports" if assessment == "supports" else "contradicts"
            related_rels = relation_by_type.get(rel_type, [])
            ne_rel_ids = [
                r.relationId for r in related_rels
                if any(e.entityId in (r.sourceEntityId, r.targetEntityId)
                       for e in ne_entities)
            ][:5]

            links.append(GraphEvidenceLink(
                linkId=f"gel_{uuid.uuid4().hex[:8]}",
                signalType="novelty",
                signalId=f"novelty_{i}",
                targetEntityIds=[e.entityId for e in ne_entities[:5]],
                targetRelationIds=ne_rel_ids,
                evidenceType="symbolic",
                rationale=f"Novelty assessment '{assessment}' for '{direction[:60]}'",
            ))

        # 5. Contradiction signals: from novelty evidence with "contradicts" assessment
        contradiction_entities: Set[str] = set()
        for ne in literature_map.noveltyEvidence:
            assessment = ne.assessment if hasattr(ne, 'assessment') else ne.get("assessment", "")
            direction = ne.direction if hasattr(ne, 'direction') else ne.get("direction", "")
            if assessment == "contradicts":
                for e in self._match_text_to_entities(direction, entity_by_name):
                    contradiction_entities.add(e.entityId)

        if contradiction_entities:
            contra_rels = relation_by_type.get("contradicts", [])
            links.append(GraphEvidenceLink(
                linkId=f"gel_{uuid.uuid4().hex[:8]}",
                signalType="contradiction",
                signalId="contradiction_signal",
                targetEntityIds=list(contradiction_entities)[:10],
                targetRelationIds=[r.relationId for r in contra_rels[:5]],
                evidenceType="symbolic",
                rationale=f"Contradiction signal links {len(contradiction_entities)} conflicting entities",
            ))

        logger.info(
            "Created %d GraphEvidenceLinks: %s",
            len(links),
            {t: len([l for l in links if l.signalType == t])
             for t in set(l.signalType for l in links)},
        )
        return links

    # ------------------------------------------------------------------
    # Matching Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _match_tokens_to_entities(
        tokens: List[str],
        entity_by_name: Dict[str, KGEntity],
    ) -> List[KGEntity]:
        """Match theme tokens to entity names via substring matching."""
        matched: List[KGEntity] = []
        seen_ids: Set[str] = set()
        for token in tokens:
            token_lower = token.lower()
            for name, entity in entity_by_name.items():
                if entity.entityId in seen_ids:
                    continue
                if token_lower in name or name in token_lower:
                    matched.append(entity)
                    seen_ids.add(entity.entityId)
                if len(matched) >= 10:
                    return matched
        return matched

    @staticmethod
    def _match_text_to_entities(
        text: str,
        entity_by_name: Dict[str, KGEntity],
    ) -> List[KGEntity]:
        """Match text content to entity names."""
        if not text:
            return []
        text_lower = text.lower()
        matched: List[KGEntity] = []
        seen_ids: Set[str] = set()
        for name, entity in entity_by_name.items():
            if entity.entityId in seen_ids:
                continue
            # Check if significant words from the entity name appear in the text
            name_words = set(name.lower().split())
            if len(name_words) >= 2 and sum(1 for w in name_words if w in text_lower) >= 2:
                matched.append(entity)
                seen_ids.add(entity.entityId)
            elif len(name_words) == 1 and name.lower() in text_lower:
                matched.append(entity)
                seen_ids.add(entity.entityId)
            if len(matched) >= 15:
                break
        return matched
