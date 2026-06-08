"""
Reasoning Knowledge Graph Builder (Graph 2)

Builds concept-level reasoning knowledge graphs from StructuredPaper[] + LiteratureMap.
Performs entity extraction, normalization, and relation extraction.

Inspirations:
- LECTOR: deduction/abduction/induction relation types
- SMAR+NIE: node importance estimation
"""

import json
import logging
import re as _re
import uuid
from collections import defaultdict
from typing import List, Dict, Any, Optional, Set, Tuple

from app.models.idea import (
    IdeaSession,
    StructuredPaper,
    Claim,
    LiteratureMap,
    ReasoningKG,
    KGEntity,
    KGRelation,
)
from app.llm.provider_client import get_provider_client, ChatMessage
from app.services import prompts

logger = logging.getLogger(__name__)


def _extract_json_from_text(text: str) -> dict:
    """Robust JSON extraction from LLM text output.

    Tries multiple strategies in order:
    1. Direct json.loads (if the whole text is clean JSON)
    2. Extract from ```json ... ``` code block
    3. Bracket-balanced extraction (handles nested braces, avoids greedy regex issues)
    """
    # Strategy 1: direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Strategy 2: markdown code block
    code_match = _re.search(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', text)
    if code_match:
        try:
            return json.loads(code_match.group(1))
        except json.JSONDecodeError:
            pass

    # Strategy 3: bracket-balanced extraction
    start = text.find('{')
    if start == -1:
        return {}

    depth = 0
    end = start
    for i in range(start, len(text)):
        if text[i] == '{':
            depth += 1
        elif text[i] == '}':
            depth -= 1
            if depth == 0:
                end = i + 1
                break

    if end > start:
        try:
            return json.loads(text[start:end])
        except json.JSONDecodeError:
            pass

    return {}


class ReasoningKGBuilder:
    """Stateless builder for concept-level reasoning knowledge graphs."""

    def build_reasoning_kg(
        self,
        session: IdeaSession,
        structured_papers: List[StructuredPaper],
        literature_map: LiteratureMap,
    ) -> ReasoningKG:
        """Build ReasoningKG from structured papers and literature map.

        Contract: only consumes Step 3 outputs (StructuredPaper[] + LiteratureMap).
        Does NOT read RawPaper[] or LiteratureGraph directly.
        """
        kg_id = f"rkg_{uuid.uuid4().hex[:12]}"

        # 1. Entity extraction (LLM primary, rule fallback)
        entities = self._extract_entities(session, structured_papers)

        # 2. Entity normalization (rule-based dedup + importance scoring)
        entities = self._normalize_entities(entities, structured_papers)

        # 3. Relation extraction (LLM primary, rule fallback)
        relations = self._extract_relations(session, entities, structured_papers)

        # 4. Connectivity post-processing: ensure high-importance entities
        #    are reachable via co-occurrence bridge edges when the LLM
        #    relation graph has disconnected components
        relations = self._ensure_connectivity(
            session, entities, relations, structured_papers,
        )

        kg = ReasoningKG(
            id=kg_id,
            sessionId=session.id,
            literatureGraphId="",  # Could be linked in future
            literatureMapId=literature_map.id,
            entities=entities,
            relations=relations,
        )

        logger.info(
            "Built ReasoningKG %s: %d entities, %d relations",
            kg_id, len(entities), len(relations),
        )
        return kg

    # ------------------------------------------------------------------
    # Entity Extraction
    # ------------------------------------------------------------------

    def _extract_entities(
        self, session: IdeaSession, structured_papers: List[StructuredPaper]
    ) -> List[KGEntity]:
        """Extract entities from structured papers via LLM or heuristic fallback."""
        if not structured_papers:
            return []

        # Build input texts from claims, findings, methods
        claims_lines = []
        findings_lines = []
        methods_lines = []

        for sp in structured_papers:
            for c in sp.claims:
                claims_lines.append(f"[{sp.rawPaperId}/{c.claimId}] {c.text}")
            for f in sp.findings:
                findings_lines.append(f"[{sp.rawPaperId}] {f.description}")
            for m in sp.methods:
                methods_lines.append(f"[{sp.rawPaperId}] {m.name}: {m.description}")

        claims_text = "\n".join(claims_lines[:30]) or "No claims available"
        findings_text = "\n".join(findings_lines[:15]) or "No findings available"
        methods_text = "\n".join(methods_lines[:20]) or "No methods available"

        try:
            client = get_provider_client(session.config.providerName)

            user_prompt = prompts.EXTRACT_KG_ENTITIES_USER.format(
                claims_text=claims_text,
                findings_text=findings_text,
                methods_text=methods_text,
                seed_query=session.config.seedQuery,
            )

            messages = [
                ChatMessage(role="system", content=prompts.EXTRACT_KG_ENTITIES_SYSTEM),
                ChatMessage(role="user", content=user_prompt),
            ]

            response = client.chat(messages, model=session.config.model, max_tokens=2000)

            data = _extract_json_from_text(response.text)

            # Collect valid paper IDs from structured papers
            valid_paper_ids = {sp.rawPaperId for sp in structured_papers}
            valid_claim_ids = {c.claimId for sp in structured_papers for c in sp.claims}

            entities = []
            for e in data.get("entities", [])[:40]:  # Cap at 40 entities
                try:
                    # Enforce source paper subset constraint
                    raw_src_papers = e.get("sourcePaperIds", [])
                    raw_src_claims = e.get("sourceClaimIds", [])
                    filtered_papers = [pid for pid in raw_src_papers if pid in valid_paper_ids][:10]
                    filtered_claims = [cid for cid in raw_src_claims if cid in valid_claim_ids][:10]
                    # If LLM gave bad paper IDs, fall back to empty (entity still valid, just less grounded)
                    if not filtered_papers and raw_src_papers:
                        logger.debug("Entity '%s' had no valid source papers; LLM returned: %s", e.get("name"), raw_src_papers)

                    entity = KGEntity(
                        entityId=f"ke_{uuid.uuid4().hex[:8]}",
                        name=e.get("name", ""),
                        entityType=e.get("type", "concept"),
                        normalizedName=e.get("name", "").lower().strip(),
                        sourcePaperIds=filtered_papers,
                        sourceClaimIds=filtered_claims,
                    )
                    entities.append(entity)
                except Exception as ve:
                    logger.debug("Skipping invalid entity: %s", ve)

            if entities:
                return entities

        except Exception as e:
            logger.warning("LLM entity extraction failed: %s, using fallback", e)

        # Heuristic fallback
        return self._extract_entities_heuristic(structured_papers)

    def _extract_entities_heuristic(
        self, structured_papers: List[StructuredPaper]
    ) -> List[KGEntity]:
        """Heuristic entity extraction from methods and claims."""
        entities: Dict[str, KGEntity] = {}

        # Methods → method-type entities
        for sp in structured_papers:
            for m in sp.methods:
                key = m.name.lower().strip()
                if key in entities:
                    entities[key].sourcePaperIds.append(sp.rawPaperId)
                    if m.methodId:
                        entities[key].sourceClaimIds.append(m.methodId)
                else:
                    entities[key] = KGEntity(
                        entityId=f"ke_{uuid.uuid4().hex[:8]}",
                        name=m.name,
                        entityType="method",
                        normalizedName=key,
                        sourcePaperIds=[sp.rawPaperId],
                        sourceClaimIds=[m.methodId] if m.methodId else [],
                    )

        # Claims → concept/gap entities (noun phrases)
        for sp in structured_papers:
            for c in sp.claims:
                if c.claimType in ("gap", "limitation"):
                    key = f"gap: {c.text[:80]}"
                    entities[key] = KGEntity(
                        entityId=f"ke_{uuid.uuid4().hex[:8]}",
                        name=c.text[:100],
                        entityType="gap",
                        normalizedName=key.lower(),
                        sourcePaperIds=[sp.rawPaperId],
                        sourceClaimIds=[c.claimId],
                    )
                elif c.claimType in ("finding", "hypothesis"):
                    # Extract key noun phrases from claim text
                    phrases = self._extract_noun_phrases(c.text)
                    for phrase in phrases[:2]:
                        key = phrase.lower()
                        if key not in entities:
                            entities[key] = KGEntity(
                                entityId=f"ke_{uuid.uuid4().hex[:8]}",
                                name=phrase,
                                entityType="concept",
                                normalizedName=key,
                                sourcePaperIds=[sp.rawPaperId],
                                sourceClaimIds=[c.claimId],
                            )
                        else:
                            entities[key].sourcePaperIds.append(sp.rawPaperId)
                            entities[key].sourceClaimIds.append(c.claimId)

        return list(entities.values())[:30]

    @staticmethod
    def _extract_noun_phrases(text: str) -> List[str]:
        """Extract simple noun phrases (Adj* Noun+ patterns)."""
        # Simplified: find capitalized or known technical terms
        words = text.split()
        phrases = []
        i = 0
        while i < len(words):
            w = words[i].strip(".,;:()\"'")
            if w and w[0].isupper() and len(w) > 2:
                # Collect multi-word capitalized phrase
                j = i + 1
                while j < len(words) and words[j].strip(".,;:()\"'") and (
                    words[j][0].isupper() if words[j][0].isalpha() else False
                ):
                    j += 1
                phrase = " ".join(words[k].strip(".,;:()\"'") for k in range(i, j))
                if len(phrase.split()) >= 1:
                    phrases.append(phrase)
                i = j
            else:
                i += 1
        return phrases[:5]

    # ------------------------------------------------------------------
    # Entity Normalization
    # ------------------------------------------------------------------

    def _normalize_entities(
        self, entities: List[KGEntity], structured_papers: List[StructuredPaper]
    ) -> List[KGEntity]:
        """Normalize entities: dedup by name similarity, compute importance scores."""
        if not entities:
            return []

        # Build co-occurrence graph for importance scoring
        paper_entities: Dict[str, Set[str]] = defaultdict(set)
        for e in entities:
            for pid in e.sourcePaperIds:
                paper_entities[pid].add(e.entityId)

        # Compute raw importance: entities that appear in many papers and co-occur with many others
        entity_paper_count: Dict[str, int] = defaultdict(int)
        entity_cooccur: Dict[str, int] = defaultdict(int)
        for e in entities:
            entity_paper_count[e.entityId] = len(set(e.sourcePaperIds))
            # Co-occurrence: count other entities that share at least one paper
            co_entities: Set[str] = set()
            for pid in e.sourcePaperIds:
                co_entities |= paper_entities.get(pid, set())
            co_entities.discard(e.entityId)
            entity_cooccur[e.entityId] = len(co_entities)

        max_papers = max(entity_paper_count.values(), default=1)
        max_cooccur = max(entity_cooccur.values(), default=1)

        # Assign importance scores
        result = []
        for e in entities:
            paper_score = entity_paper_count.get(e.entityId, 0) / max_papers
            cooccur_score = entity_cooccur.get(e.entityId, 0) / max_cooccur
            importance = 0.4 * paper_score + 0.4 * cooccur_score + 0.2 * min(1.0, len(e.sourceClaimIds) / 5.0)
            result.append(KGEntity(
                entityId=e.entityId,
                name=e.name,
                entityType=e.entityType,
                normalizedName=e.normalizedName,
                sourcePaperIds=e.sourcePaperIds,
                sourceClaimIds=e.sourceClaimIds,
                importanceScore=round(min(1.0, importance), 3),
                metadata=e.metadata,
            ))

        return sorted(result, key=lambda e: e.importanceScore, reverse=True)

    # ------------------------------------------------------------------
    # Relation Extraction
    # ------------------------------------------------------------------

    def _extract_relations(
        self,
        session: IdeaSession,
        entities: List[KGEntity],
        structured_papers: List[StructuredPaper],
    ) -> List[KGRelation]:
        """Extract relations between entities via LLM or co-occurrence fallback."""
        if len(entities) < 2:
            return []

        # Build entity index
        entity_index: Dict[str, KGEntity] = {}
        for e in entities:
            entity_index[e.normalizedName] = e
            entity_index[e.name.lower()] = e

        # Build input texts
        entities_text = "\n".join(
            f"- {e.name} [{e.entityId}] ({e.entityType})"
            for e in entities[:30]
        )
        claims_lines = []
        for sp in structured_papers:
            for c in sp.claims:
                claims_lines.append(f"[{sp.rawPaperId}/{c.claimId}] {c.text}")
        claims_text = "\n".join(claims_lines[:20])

        findings_lines = []
        for sp in structured_papers:
            for f in sp.findings:
                findings_lines.append(f"[{sp.rawPaperId}] {f.description}")
        findings_text = "\n".join(findings_lines[:10])

        try:
            client = get_provider_client(session.config.providerName)

            user_prompt = prompts.EXTRACT_KG_RELATIONS_USER.format(
                entities_text=entities_text,
                claims_text=claims_text or "No claims available",
                findings_text=findings_text or "No findings available",
                seed_query=session.config.seedQuery,
            )

            messages = [
                ChatMessage(role="system", content=prompts.EXTRACT_KG_RELATIONS_SYSTEM),
                ChatMessage(role="user", content=user_prompt),
            ]

            response = client.chat(messages, model=session.config.model, max_tokens=2000)

            data = _extract_json_from_text(response.text)

            # Collect valid IDs for source paper/claim constraint enforcement
            valid_paper_ids = {sp.rawPaperId for sp in structured_papers}
            valid_claim_ids = {c.claimId for sp in structured_papers for c in sp.claims}

            relations = []
            for r in data.get("relations", [])[:30]:
                src_name = r.get("sourceEntityId", "").lower().strip()
                tgt_name = r.get("targetEntityId", "").lower().strip()
                src = entity_index.get(src_name)
                tgt = entity_index.get(tgt_name)

                if not src or not tgt:
                    continue

                # Enforce source paper subset constraint
                raw_papers = r.get("sourcePaperIds", [])
                raw_claims = r.get("sourceClaimIds", [])
                filtered_papers = [pid for pid in raw_papers if pid in valid_paper_ids][:10]
                filtered_claims = [cid for cid in raw_claims if cid in valid_claim_ids][:10]

                try:
                    rel = KGRelation(
                        relationId=f"kr_{uuid.uuid4().hex[:8]}",
                        sourceEntityId=src.entityId,
                        targetEntityId=tgt.entityId,
                        relationType=r.get("relationType", "supports"),
                        weight=float(r.get("weight", 0.5)),
                        sourcePaperIds=filtered_papers,
                        sourceClaimIds=filtered_claims,
                    )
                    relations.append(rel)
                except Exception as ve:
                    logger.debug("Skipping invalid relation: %s", ve)

            if relations:
                return relations

        except Exception as e:
            logger.warning("LLM relation extraction failed: %s, using fallback", e)

        # Co-occurrence fallback
        return self._extract_relations_fallback(entities, structured_papers)

    def _extract_relations_fallback(
        self,
        entities: List[KGEntity],
        structured_papers: List[StructuredPaper],
    ) -> List[KGRelation]:
        """Fallback: entities co-occurring in the same paper get 'supports' relations."""
        # Build paper → entity mapping
        paper_entities: Dict[str, List[KGEntity]] = defaultdict(list)
        for e in entities[:20]:  # Cap for O(n^2)
            for pid in set(e.sourcePaperIds):
                paper_entities[pid].append(e)

        relations: List[KGRelation] = []
        seen_pairs: Set[Tuple[str, str]] = set()

        for pid, paper_ents in paper_entities.items():
            for i in range(len(paper_ents)):
                for j in range(i + 1, len(paper_ents)):
                    pair = tuple(sorted([paper_ents[i].entityId, paper_ents[j].entityId]))
                    if pair in seen_pairs:
                        continue
                    seen_pairs.add(pair)

                    # Determine relation type from claim types
                    rel_type = "supports"
                    claims_i = set(paper_ents[i].sourceClaimIds)
                    claims_j = set(paper_ents[j].sourceClaimIds)

                    relations.append(KGRelation(
                        relationId=f"kr_{uuid.uuid4().hex[:8]}",
                        sourceEntityId=paper_ents[i].entityId,
                        targetEntityId=paper_ents[j].entityId,
                        relationType=rel_type,
                        weight=0.3,
                        sourcePaperIds=[pid],
                        sourceClaimIds=list(claims_i | claims_j)[:5],
                        metadata={"method": "cooccurrence_fallback"},
                    ))

        return relations[:30]

    # ------------------------------------------------------------------
    # Connectivity Post-Processing (Graph Component Analysis)
    # ------------------------------------------------------------------

    def _ensure_connectivity(
        self,
        session: IdeaSession,
        entities: List[KGEntity],
        relations: List[KGRelation],
        structured_papers: List[StructuredPaper],
    ) -> List[KGRelation]:
        """Post-process relation graph to ensure BFS can traverse from root entities.

        Strategy:
        1. Build adjacency list from existing relations
        2. Find connected components via BFS
        3. Identify "bridge gaps": high-importance entities in different components
        4. Add co-occurrence edges to bridge disconnected components
        5. Optionally ask LLM to label bridge edges (falls back to weight=0.4)
        """
        if len(entities) < 2:
            return relations

        # --- Step 1: build adjacency list ---
        adj: Dict[str, Set[str]] = defaultdict(set)
        entity_ids = {e.entityId for e in entities}
        for rel in relations:
            if rel.sourceEntityId in entity_ids and rel.targetEntityId in entity_ids:
                adj[rel.sourceEntityId].add(rel.targetEntityId)
                adj[rel.targetEntityId].add(rel.sourceEntityId)

        # --- Step 2: find connected components ---
        visited: Set[str] = set()
        components: List[Set[str]] = []

        for eid in entity_ids:
            if eid in visited:
                continue
            comp: Set[str] = set()
            queue = [eid]
            while queue:
                cur = queue.pop(0)
                if cur in comp:
                    continue
                comp.add(cur)
                for nb in adj.get(cur, set()):
                    if nb not in comp:
                        queue.append(nb)
            visited |= comp
            components.append(comp)

        # Build entity lookup
        entity_by_id: Dict[str, KGEntity] = {e.entityId: e for e in entities}

        # --- Step 3: identify bridge gaps ---
        # Sort entities by importance; root = top entities by importanceScore
        sorted_entities = sorted(entities, key=lambda e: e.importanceScore, reverse=True)
        root_entity_ids = {e.entityId for e in sorted_entities[:5]}

        # Find which component contains the majority of root entities
        comp_root_counts: Dict[int, int] = {}
        for i, comp in enumerate(components):
            comp_root_counts[i] = len(comp & root_entity_ids)

        if not comp_root_counts:
            return relations

        main_comp_idx = max(comp_root_counts, key=comp_root_counts.get)
        main_comp = components[main_comp_idx]
        root_in_main = comp_root_counts[main_comp_idx]

        # If all root entities already in the same component, no bridging needed
        # UNLESS the component is just the root entities themselves (no edges to non-root
        # entities), which would starve BFS after depth 1.
        root_only_clique = (
            root_in_main >= len(root_entity_ids)
            and root_in_main >= 3
            and len(main_comp) <= len(root_entity_ids) + 2  # ≤ roots + 2 others
        )
        if root_in_main >= len(root_entity_ids) and root_in_main >= 3 and not root_only_clique:
            logger.info(
                "Connectivity check: %d components, all %d root entities in main "
                "component (size=%d) — no bridging needed",
                len(components), root_in_main, len(main_comp),
            )
            return relations

        logger.info(
            "Connectivity check: %d components, only %d/%d root entities in main "
            "component — adding bridge edges",
            len(components), root_in_main, len(root_entity_ids),
        )

        # --- Step 4: add co-occurrence bridge edges ---
        # Strategy: add co-occurrence edges for pairs NOT already connected by LLM.
        # This guarantees BFS can traverse from any root entity to any co-occurring
        # entity, regardless of LLM relation quality.
        #
        # Two cases:
        #   (a) Cross-component bridging: entities in different components
        #   (b) Root out-edge expansion: root entities that only connect to other
        #       roots (root-only clique) need edges to non-root entities in the
        #       same paper to allow BFS depth > 1

        existing_pairs: Set[Tuple[str, str]] = set()
        for rel in relations:
            pair = tuple(sorted([rel.sourceEntityId, rel.targetEntityId]))
            existing_pairs.add(pair)

        # Build paper → entity mapping
        paper_entities: Dict[str, List[KGEntity]] = defaultdict(list)
        for e in entities:
            for pid in set(e.sourcePaperIds):
                paper_entities[pid].append(e)

        bridge_relations: List[KGRelation] = []
        new_pairs: List[Tuple[KGEntity, KGEntity, str, int]] = []

        # Find root entity IDs
        root_set = {e.entityId for e in sorted_entities[:5]}

        # Check if we're in root-only clique situation
        root_adj: Dict[str, Set[str]] = defaultdict(set)
        for rel in relations:
            if rel.sourceEntityId in root_set and rel.targetEntityId in root_set:
                root_adj[rel.sourceEntityId].add(rel.targetEntityId)
                root_adj[rel.targetEntityId].add(rel.sourceEntityId)
            elif rel.sourceEntityId in root_set:
                root_adj[rel.sourceEntityId].add(rel.targetEntityId)
            elif rel.targetEntityId in root_set:
                root_adj[rel.targetEntityId].add(rel.sourceEntityId)

        roots_with_no_external = [
            rid for rid in root_set
            if all(nb in root_set for nb in root_adj.get(rid, set()))
        ]
        need_out_expansion = len(roots_with_no_external) >= 3

        if need_out_expansion:
            logger.info(
                "Root-out expansion: %d/%d root entities have no edges to non-root "
                "entities — adding co-occurrence expansion edges",
                len(roots_with_no_external), len(root_set),
            )

        for pid, paper_ents in paper_entities.items():
            for i in range(len(paper_ents)):
                for j in range(i + 1, len(paper_ents)):
                    ea, eb = paper_ents[i], paper_ents[j]
                    pair = tuple(sorted([ea.entityId, eb.entityId]))
                    if pair in existing_pairs:
                        continue

                    is_root_a = ea.entityId in root_set
                    is_root_b = eb.entityId in root_set

                    # Skip root↔root pairs (already handled by LLM if present)
                    if is_root_a and is_root_b:
                        continue

                    comp_a = next((k for k, c in enumerate(components) if ea.entityId in c), None)
                    comp_b = next((k for k, c in enumerate(components) if eb.entityId in c), None)

                    priority = 0

                    # Case (a): cross-component bridging
                    if comp_a is not None and comp_b is not None and comp_a != comp_b:
                        in_main_a = ea.entityId in main_comp
                        in_main_b = eb.entityId in main_comp
                        if (is_root_a and in_main_b) or (is_root_b and in_main_a):
                            priority = 3
                        elif is_root_a or is_root_b:
                            priority = 2
                        else:
                            priority = 1

                    # Case (b): root out-edge expansion (same paper, different entities)
                    elif need_out_expansion and (
                        (is_root_a and not is_root_b) or (is_root_b and not is_root_a)
                    ):
                        priority = 2  # High priority for root→non-root expansion

                    if priority == 0:
                        continue

                    new_pairs.append((ea, eb, pid, priority))
                    existing_pairs.add(pair)

        # Sort by priority (high first), then take top candidates
        new_pairs.sort(key=lambda x: x[3], reverse=True)

        # --- Step 5: LLM label bridge edges (or use default) ---
        bridge_pairs_to_label = new_pairs[:20]  # Cap at 20 for LLM batch
        if bridge_pairs_to_label:
            llm_labels = self._label_bridge_edges(
                session, bridge_pairs_to_label,
            )

            for (ea, eb, pid, priority), (rel_type, weight) in zip(
                bridge_pairs_to_label, llm_labels,
            ):
                claims_i = set(ea.sourceClaimIds)
                claims_j = set(eb.sourceClaimIds)
                bridge_relations.append(KGRelation(
                    relationId=f"kr_{uuid.uuid4().hex[:8]}",
                    sourceEntityId=ea.entityId,
                    targetEntityId=eb.entityId,
                    relationType=rel_type,
                    weight=weight,
                    sourcePaperIds=[pid],
                    sourceClaimIds=list(claims_i | claims_j)[:5],
                    metadata={
                        "method": "connectivity_bridge",
                        "priority": priority,
                    },
                ))

        logger.info(
            "Added %d bridge edges (%d pairs considered, %d components)",
            len(bridge_relations), len(new_pairs), len(components),
        )

        return relations + bridge_relations

    def _label_bridge_edges(
        self,
        session: IdeaSession,
        pairs: List[Tuple[KGEntity, KGEntity, str, int]],
    ) -> List[Tuple[str, float]]:
        """Ask LLM to label co-occurrence bridge edges with relation types.

        Returns list of (relationType, weight) aligned with input pairs.
        Falls back to ("supports", 0.4) for all pairs on any failure.
        """
        if not pairs:
            return []

        # Build prompt input
        pair_lines = []
        for i, (ea, eb, pid, priority) in enumerate(pairs):
            pair_lines.append(
                f"{i}: \"{ea.name}\" [{ea.entityType}] ↔ \"{eb.name}\" [{eb.entityType}] "
                f"(co-occur in paper {pid})"
            )

        pairs_text = "\n".join(pair_lines)

        try:
            client = get_provider_client(session.config.providerName)
            messages = [
                ChatMessage(role="system", content=(
                    "You label co-occurrence relations between scientific entities. "
                    "For each pair, choose the best relation type from: "
                    "supports, uses, produces, implies, hypothesizes, generalizes, contradicts. "
                    "Assign weight 0.3-0.7 reflecting how strongly the co-occurrence implies "
                    "a real relationship. "
                    "Respond ONLY with a JSON array of {\"type\": \"...\", \"weight\": 0.X} objects, "
                    "one per pair in the same order."
                )),
                ChatMessage(role="user", content=(
                    f"Label these entity pairs that co-occur in the same papers:\n\n{pairs_text}\n\n"
                    "Respond with JSON array:\n"
                    "[{\"type\": \"supports\", \"weight\": 0.5}, ...]"
                )),
            ]
            response = client.chat(messages, model=session.config.model, max_tokens=500)
            data = _extract_json_from_text(response.text)

            labels = data if isinstance(data, list) else data.get("labels", [])
            result = []
            for i, label in enumerate(labels):
                if i >= len(pairs):
                    break
                rel_type = label.get("type", "supports") if isinstance(label, dict) else "supports"
                weight = float(label.get("weight", 0.4)) if isinstance(label, dict) else 0.4
                weight = max(0.1, min(0.9, weight))  # Clamp
                result.append((rel_type, weight))

            # Pad with defaults if LLM returned fewer labels
            while len(result) < len(pairs):
                result.append(("supports", 0.4))

            logger.debug("LLM labeled %d/%d bridge edges", len(result), len(pairs))
            return result

        except Exception as e:
            logger.warning("LLM bridge edge labeling failed: %s, using defaults", e)
            return [("supports", 0.4)] * len(pairs)
