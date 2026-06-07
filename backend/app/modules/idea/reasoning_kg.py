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

            try:
                data = json.loads(response.text)
            except json.JSONDecodeError:
                json_match = _re.search(r'\{[\s\S]*\}', response.text)
                data = json.loads(json_match.group()) if json_match else {}

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

            try:
                data = json.loads(response.text)
            except json.JSONDecodeError:
                json_match = _re.search(r'\{[\s\S]*\}', response.text)
                data = json.loads(json_match.group()) if json_match else {}

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
