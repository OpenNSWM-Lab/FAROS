"""
Deep Reading Module

Performs structured extraction from selected papers:
- Claims with logical connection types (LECTOR-inspired)
- Findings with categories
- Method mentions
- Novelty evidence with assessment (Agents4Science-inspired evidence linking)

Also builds LiteratureMap aggregating cluster-level insights.
"""

import logging
import json
import uuid
import re as _re
from typing import List, Dict, Any, Optional

from app.models.idea import (
    IdeaSession,
    RawPaper,
    StructuredPaper,
    Claim,
    Finding,
    MethodMention,
    NoveltyEvidence,
    LiteratureGraph,
    LiteratureMap,
    LiteratureCluster,
)
from app.llm.provider_client import get_provider_client, ChatMessage, ProviderError
from app.services import prompts

logger = logging.getLogger(__name__)


class DeepReader:
    """Stateless deep reader for structured paper extraction."""

    def extract_structured_papers(
        self,
        session: IdeaSession,
        selected_paper_ids: List[str],
        raw_papers: List[RawPaper],
    ) -> List[StructuredPaper]:
        """Extract structured information from selected papers.

        Args:
            session: The idea session (for provider/model config).
            selected_paper_ids: IDs of papers selected for deep reading.
            raw_papers: All raw papers (will filter to selected subset).

        Returns:
            List of StructuredPaper for each selected paper.
        """
        selected_papers = [
            p for p in raw_papers if p.id in selected_paper_ids
        ]
        if not selected_papers:
            logger.warning("No selected papers to deep-read")
            return []

        structured_papers: List[StructuredPaper] = []
        for paper in selected_papers:
            try:
                sp = self._extract_single(session, paper)
            except Exception as e:
                logger.warning(
                    "LLM extraction failed for %s (%s), using heuristic fallback",
                    paper.id, e,
                )
                sp = self._extract_heuristic(session.id, paper)
            structured_papers.append(sp)

        logger.info(
            "Deep-read %d papers: %d llm, %d heuristic",
            len(structured_papers),
            sum(1 for s in structured_papers if s.extractionMethod == "llm"),
            sum(1 for s in structured_papers if s.extractionMethod == "heuristic"),
        )
        return structured_papers

    def _extract_single(
        self, session: IdeaSession, paper: RawPaper
    ) -> StructuredPaper:
        """LLM-based structured extraction for a single paper."""
        client = get_provider_client(session.config.providerName)

        user_prompt = prompts.EXTRACT_STRUCTURED_PAPER_USER.format(
            title=paper.title,
            authors=", ".join(paper.authors[:5]),
            year=str(paper.year or "N/A"),
            venue=paper.venue or "Unknown",
            abstract=paper.abstract or "No abstract available",
            seed_query=session.config.seedQuery,
        )

        messages = [
            ChatMessage(role="system", content=prompts.EXTRACT_STRUCTURED_PAPER_SYSTEM),
            ChatMessage(role="user", content=user_prompt),
        ]

        response = client.chat(
            messages, model=session.config.model, max_tokens=2000
        )

        try:
            data = json.loads(response.text)
        except json.JSONDecodeError:
            # Try to extract JSON from response text
            json_match = _re.search(r'\{[\s\S]*\}', response.text)
            if json_match:
                data = json.loads(json_match.group())
            else:
                raise ValueError("Could not parse LLM response as JSON")

        # Parse claims
        claims = []
        for i, c in enumerate(data.get("claims", [])[:5]):
            claims.append(Claim(
                claimId=f"cl_{uuid.uuid4().hex[:8]}",
                paperId=paper.id,
                text=c.get("text", ""),
                claimType=c.get("claimType", "finding"),
                confidence=float(c.get("confidence", 0.5)),
                evidenceSpan=c.get("evidenceSpan", ""),
            ))

        # Parse findings
        findings = []
        for f in data.get("findings", [])[:3]:
            related = []
            for ref in f.get("relatedClaims", []):
                if isinstance(ref, int) and ref < len(claims):
                    related.append(claims[ref].claimId)
            findings.append(Finding(
                findingId=f"fn_{uuid.uuid4().hex[:8]}",
                paperId=paper.id,
                description=f.get("description", ""),
                category=f.get("category", "empirical"),
                relatedClaims=related,
            ))

        # Parse methods
        methods = []
        for m in data.get("methods", [])[:5]:
            methods.append(MethodMention(
                methodId=f"mm_{uuid.uuid4().hex[:8]}",
                paperId=paper.id,
                name=m.get("name", ""),
                description=m.get("description", ""),
                category=m.get("category", "algorithm"),
            ))

        # Parse novelty evidence
        novelty_evidence = []
        for ne in data.get("noveltyEvidence", [])[:3]:
            novelty_evidence.append(NoveltyEvidence(
                evidenceId=f"ne_{uuid.uuid4().hex[:8]}",
                paperId=paper.id,
                direction=ne.get("direction", ""),
                assessment=ne.get("assessment", "neutral"),
                rationale=ne.get("rationale", ""),
            ))

        summary = data.get("summary", "")

        return StructuredPaper(
            id=paper.id,  # Use raw paper ID as structured paper ID
            sessionId=session.id,
            rawPaperId=paper.id,
            title=paper.title,
            claims=claims,
            findings=findings,
            methods=methods,
            noveltyEvidence=novelty_evidence,
            summary=summary,
            extractionMethod="llm",
            extractionConfidence=0.7,
        )

    def _extract_heuristic(
        self, session_id: str, paper: RawPaper
    ) -> StructuredPaper:
        """Heuristic fallback extraction when LLM fails.

        Uses regex-based noun phrase chunking and keyword matching.
        """
        abstract = paper.abstract or ""
        sentences = _re.split(r'(?<=[.!?])\s+', abstract) if abstract else []

        # Extract one claim from the first sentence
        claims = []
        if sentences:
            first_sent = sentences[0].strip()
            if len(first_sent) > 20:
                claims.append(Claim(
                    claimId=f"cl_{uuid.uuid4().hex[:8]}",
                    paperId=paper.id,
                    text=first_sent[:300],
                    claimType="finding",
                    confidence=0.3,
                    evidenceSpan=first_sent[:300],
                ))

        # Extract method mentions via keyword matching
        METHOD_KEYWORDS = [
            "transformer", "attention", "neural network", "cnn", "rnn",
            "lstm", "bert", "gpt", "diffusion", "reinforcement learning",
            "gan", "vae", "graph neural", "gnn", "autoencoder",
            "regression", "classification", "clustering", "svm",
            "random forest", "gradient boosting", "xgboost",
        ]
        methods = []
        text_lower = abstract.lower()
        for kw in METHOD_KEYWORDS:
            if kw in text_lower:
                methods.append(MethodMention(
                    methodId=f"mm_{uuid.uuid4().hex[:8]}",
                    paperId=paper.id,
                    name=kw.title(),
                    description=f"Method detected via keyword matching: {kw}",
                    category="algorithm",
                ))
                if len(methods) >= 5:
                    break

        # Simple finding from key sentences
        findings = []
        for sent in sentences[1:4]:
            sent = sent.strip()
            if len(sent) > 30:
                findings.append(Finding(
                    findingId=f"fn_{uuid.uuid4().hex[:8]}",
                    paperId=paper.id,
                    description=sent[:300],
                    category="empirical",
                ))
                if len(findings) >= 2:
                    break

        return StructuredPaper(
            id=paper.id,
            sessionId=session_id,
            rawPaperId=paper.id,
            title=paper.title,
            claims=claims,
            findings=findings,
            methods=methods,
            noveltyEvidence=[],
            summary=abstract[:300] if abstract else "",
            extractionMethod="heuristic",
            extractionConfidence=0.3,
        )

    # ------------------------------------------------------------------
    # LiteratureMap Construction
    # ------------------------------------------------------------------

    def build_literature_map(
        self,
        session_id: str,
        selected_paper_ids: List[str],
        structured_papers: List[StructuredPaper],
        graph: LiteratureGraph,
    ) -> LiteratureMap:
        """Build a LiteratureMap aggregating cluster-level insights.

        Args:
            session_id: Parent session ID.
            selected_paper_ids: IDs of selected papers.
            structured_papers: Deep-read structured papers.
            graph: LiteratureGraph with clusters and roles.

        Returns:
            LiteratureMap with clusters, frontiers, gaps, and novelty evidence.
        """
        map_id = f"lm_{uuid.uuid4().hex[:12]}"

        # Build paper index
        paper_by_id = {sp.rawPaperId: sp for sp in structured_papers}

        # Cluster-level aggregation
        cluster_map: Dict[str, List[str]] = {}
        for node in graph.nodes:
            if node.clusterId and node.isSelected:
                cluster_map.setdefault(node.clusterId, []).append(node.paperId)

        # Build clusters for map (with structured paper data)
        map_clusters: List[LiteratureCluster] = []
        for cluster in graph.clusters:
            # Update label with theme tokens
            updated_cluster = LiteratureCluster(
                clusterId=cluster.clusterId,
                label=f"Cluster: {', '.join(cluster.themeTokens[:3])}" if cluster.themeTokens else cluster.label,
                paperIds=cluster.paperIds,
                centroidPaperId=cluster.centroidPaperId,
                themeTokens=cluster.themeTokens,
            )
            map_clusters.append(updated_cluster)

        # Frontier paper IDs
        frontiers: List[str] = [
            node.paperId
            for node in graph.nodes
            if node.role == "frontier" and node.isSelected
        ]

        # Gap detection
        gaps: List[Dict[str, Any]] = []
        for sp in structured_papers:
            for ne in sp.noveltyEvidence:
                if ne.assessment in ("supports", "overlaps"):
                    # Check if any paper directly addresses this direction
                    addressing_papers = [
                        sp2.rawPaperId
                        for sp2 in structured_papers
                        if any(
                            c.text and ne.direction.lower() in c.text.lower()
                            for c in sp2.claims
                        )
                    ]
                    if len(addressing_papers) <= 1:
                        gaps.append({
                            "direction": ne.direction,
                            "evidence": ne.rationale,
                            "paperIds": [sp.rawPaperId] + addressing_papers,
                            "assessment": ne.assessment,
                        })

        # Novelty evidence aggregation
        novelty_evidence: List[Dict[str, Any]] = []
        for sp in structured_papers:
            for ne in sp.noveltyEvidence:
                novelty_evidence.append({
                    "paperId": sp.rawPaperId,
                    "direction": ne.direction,
                    "assessment": ne.assessment,
                    "rationale": ne.rationale,
                })

        return LiteratureMap(
            id=map_id,
            sessionId=session_id,
            clusters=map_clusters,
            frontiers=frontiers,
            gaps=gaps,
            noveltyEvidence=novelty_evidence,
            selectedPaperIds=selected_paper_ids,
        )
