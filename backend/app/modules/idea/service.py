"""
Idea Generation Service

Orchestrates the idea generation pipeline with step-based tracing.
"""

import logging
from datetime import UTC, datetime
from typing import Optional, List, Dict, Any

from app.modules.idea.contracts import (
    IdeaSession,
    IdeaSessionStatus,
    IdeaSessionConfig,
    IdeaCandidate,
    LiteratureItem,
    WorkflowTrace,
    StepResult,
    DraftPlan,
    RiskItem,
    ExperimentSpec,
    # Dual-Graph models
    BFTSConfig,
    BFTSHandoff,
    QueryFamily,
    QueryPlan,
    RawPaper,
    LiteratureGraph,
    LiteratureMap,
    StructuredPaper,
    PaperNode,
    # Phase 2 models
    ReasoningKG,
    GraphEvidenceLink,
    ReasoningPathSeed,
    # Step 6 models
    CandidateGraphEvidence,
    IdeaCritique,
    PriorWorkComparison,
    RankedIdeaOutput,
)
from app.modules.idea.storage import (
    get_session_storage,
    get_literature_storage,
    get_candidate_storage,
    generate_session_id,
    generate_literature_id,
    generate_candidate_id,
    # Dual-Graph storage
    get_raw_paper_storage,
    get_literature_graph_storage,
    get_structured_paper_storage,
    get_literature_map_storage,
    get_handoff_storage,
    generate_raw_paper_id,
    generate_graph_id,
    generate_map_id,
    generate_handoff_id,
    # Phase 2 storage
    get_reasoning_kg_storage,
    get_evidence_link_storage,
    get_path_seed_storage,
    generate_reasoning_kg_id,
    generate_evidence_link_id,
    generate_path_seed_id,
    # Step 6 storage
    get_ranked_output_storage,
    generate_ranked_output_id,
)
from app.models.idea import _compute_title_hash
from app.modules.idea.literature_graph import LiteratureGraphBuilder
from app.modules.idea.deep_reading import DeepReader
from app.modules.idea.reasoning_kg import ReasoningKGBuilder
from app.modules.idea.graph_linker import GraphLinker
from app.modules.idea.path_seed import PathSeedGenerator
from app.llm.provider_client import get_provider_client, ChatMessage, ProviderError
from app.services.search_service import get_search_service, SearchResult
from app.services.ranking_service import get_ranking_service
from app.services import prompts
import json
import re

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class IdeaGenerationService:
    """Service for managing idea generation sessions."""

    def __init__(self):
        self.session_storage = get_session_storage()
        self.literature_storage = get_literature_storage()
        self.candidate_storage = get_candidate_storage()
        # Dual-Graph builders
        self.graph_builder = LiteratureGraphBuilder()
        self.deep_reader = DeepReader()
        # Dual-Graph storage
        self.raw_paper_storage = get_raw_paper_storage()
        self.graph_storage = get_literature_graph_storage()
        self.structured_storage = get_structured_paper_storage()
        self.map_storage = get_literature_map_storage()
        self.handoff_storage = get_handoff_storage()
        # Phase 2 builders
        self.reasoning_builder = ReasoningKGBuilder()
        self.graph_linker = GraphLinker()
        self.path_seed_gen = PathSeedGenerator()
        # Phase 2 storage
        self.reasoning_kg_storage = get_reasoning_kg_storage()
        self.evidence_link_storage = get_evidence_link_storage()
        self.path_seed_storage = get_path_seed_storage()
        # Step 6 storage
        self.ranked_output_storage = get_ranked_output_storage()

    def _get_step_output(self, session: IdeaSession, step_name: str, key: str, default=None):
        """Read a specific output key from a pipeline step's trace."""
        if not session.trace:
            return default
        for step in session.trace.steps:
            if step.name == step_name:
                return step.outputs.get(key, default)
        return default
    
    def create_session(self, config: IdeaSessionConfig) -> IdeaSession:
        """Create a new idea generation session."""
        session = IdeaSession(
            id=generate_session_id(),
            config=config,
            status=IdeaSessionStatus.PENDING,
            createdAt=_utcnow(),
        )
        return self.session_storage.create(session)
    
    def get_session(self, session_id: str) -> Optional[IdeaSession]:
        """Get session by ID."""
        return self.session_storage.get(session_id)
    
    def list_sessions(self, status: Optional[IdeaSessionStatus] = None) -> List[IdeaSession]:
        """List all sessions."""
        return self.session_storage.list_all(status)
    
    def start_session(self, session_id: str) -> IdeaSession:
        """Start a session (pending -> running)."""
        session = self.session_storage.get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")
        
        if session.status != IdeaSessionStatus.PENDING:
            raise ValueError(f"Cannot start session in {session.status} state")
        
        session.status = IdeaSessionStatus.RUNNING
        session.startedAt = _utcnow()
        session.trace = WorkflowTrace(
            sessionId=session_id,
            startedAt=_utcnow(),
        )
        
        return self.session_storage.update(session)
    
    def cancel_session(self, session_id: str) -> IdeaSession:
        """Cancel a running session."""
        session = self.session_storage.get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")
        
        if session.is_terminal():
            raise ValueError(f"Cannot cancel session in {session.status} state")
        
        session.status = IdeaSessionStatus.CANCELLED
        session.endedAt = _utcnow()
        if session.trace:
            session.trace.endedAt = _utcnow()
        
        return self.session_storage.update(session)
    
    def get_literature(self, session_id: str) -> List[LiteratureItem]:
        """Get literature items for a session."""
        return self.literature_storage.list_by_session(session_id)
    
    def get_candidates(self, session_id: str) -> List[IdeaCandidate]:
        """Get candidates for a session."""
        return self.candidate_storage.list_by_session(session_id)
    
    def select_candidate(self, session_id: str, candidate_id: str) -> IdeaSession:
        """Select a candidate for the session."""
        session = self.session_storage.get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")
        
        candidate = self.candidate_storage.get(candidate_id)
        if not candidate:
            raise ValueError(f"Candidate {candidate_id} not found")
        
        if candidate.sessionId != session_id:
            raise ValueError(f"Candidate {candidate_id} does not belong to session {session_id}")
        
        session.selectedCandidateId = candidate_id
        return self.session_storage.update(session)
    
    def run_pipeline(self, session_id: str) -> IdeaSession:
        """
        Run the complete idea generation pipeline.
        
        Steps:
        1. expandQuery - Expand the seed query
        2. literatureSearch - Search for relevant papers
        3. noveltyCheck - Check novelty of potential ideas
        4. gapAnalysis - Analyze research gaps
        5. ideaBrainstorm - Generate candidate ideas
        6. rankCandidates - Rank and score candidates
        7. finalizeSession - Finalize the session
        """
        session = self.session_storage.get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")
        
        if session.status != IdeaSessionStatus.RUNNING:
            raise ValueError(f"Session must be in RUNNING state, got {session.status}")
        
        try:
            # Step 1: Expand Query
            session = self._run_step(session, "expandQuery", self._step_expand_query)
            
            # Step 2: Literature Search
            session = self._run_step(session, "literatureSearch", self._step_literature_search)
            
            # Step 3: Novelty Check
            session = self._run_step(session, "noveltyCheck", self._step_novelty_check)
            
            # Step 4: Gap Analysis
            session = self._run_step(session, "gapAnalysis", self._step_gap_analysis)
            
            # Step 5: Idea Brainstorm (uses LLM)
            session = self._run_step(session, "ideaBrainstorm", self._step_idea_brainstorm)
            
            # Step 6: Rank Candidates
            session = self._run_step(session, "rankCandidates", self._step_rank_candidates)
            
            # Step 7: Finalize
            session = self._run_step(session, "finalizeSession", self._step_finalize)
            
            # Mark completed
            session.status = IdeaSessionStatus.COMPLETED
            session.endedAt = _utcnow()
            if session.trace:
                session.trace.endedAt = _utcnow()
            
            return self.session_storage.update(session)
            
        except Exception as e:
            logger.error(f"Pipeline failed for session {session_id}: {e}")
            session.status = IdeaSessionStatus.FAILED
            session.errorMessage = str(e)
            session.endedAt = _utcnow()
            if session.trace:
                session.trace.endedAt = _utcnow()
            return self.session_storage.update(session)
    
    def _run_step(
        self,
        session: IdeaSession,
        step_name: str,
        step_func,
    ) -> IdeaSession:
        """Run a single pipeline step with tracing."""
        start_time = _utcnow()
        
        try:
            inputs, outputs, artifacts = step_func(session)
            
            end_time = _utcnow()
            duration = (end_time - start_time).total_seconds()
            
            step_result = StepResult(
                name=step_name,
                status="ok",
                inputs=inputs,
                outputs=outputs,
                artifacts=artifacts,
                startedAt=start_time,
                endedAt=end_time,
                durationSeconds=duration,
            )
            
            if session.trace:
                session.trace.steps.append(step_result)
                session.trace.totalSteps += 1
                session.trace.successfulSteps += 1
            
            return self.session_storage.update(session)
            
        except Exception as e:
            end_time = _utcnow()
            duration = (end_time - start_time).total_seconds()
            
            step_result = StepResult(
                name=step_name,
                status="failed",
                inputs={},
                outputs={},
                artifacts=[],
                startedAt=start_time,
                endedAt=end_time,
                durationSeconds=duration,
                error=str(e),
            )
            
            if session.trace:
                session.trace.steps.append(step_result)
                session.trace.totalSteps += 1
                session.trace.failedSteps += 1
            
            self.session_storage.update(session)
            raise
    
    def _step_expand_query(self, session: IdeaSession) -> tuple:
        """Expand the seed query into search terms and build QueryPlan."""
        seed = session.config.seedQuery
        paper_type = session.config.paperType
        domain = session.config.domain or "general"

        # Build BFTS config from session settings
        search_budget = session.config.searchBudget or session.config.maxPapers
        bfts_config = BFTSConfig(maxLiteratureProbes=min(search_budget, 100))

        try:
            client = get_provider_client(session.config.providerName)

            user_prompt = prompts.EXPAND_QUERY_USER.format(
                seed_query=seed,
                paper_type=paper_type,
                domain=domain
            )

            messages = [
                ChatMessage(role="system", content=prompts.EXPAND_QUERY_SYSTEM),
                ChatMessage(role="user", content=user_prompt)
            ]

            response = client.chat(messages, model=session.config.model, max_tokens=500)

            # Parse JSON response
            try:
                data = json.loads(response.text)
                expanded_terms = data.get("searchQueries", [seed])
                key_concepts = data.get("keyConcepts", [])
                refined_question = data.get("refinedQuestion", seed)
                related_areas = data.get("relatedAreas", [])
                raw_families = data.get("queryFamilies", [])
                path_templates = data.get("pathTemplates", [])
            except json.JSONDecodeError:
                expanded_terms = [seed]
                for line in response.text.split("\n"):
                    if line.strip() and not line.startswith("{"):
                        expanded_terms.append(line.strip().strip('"').strip("'").strip("-").strip())
                key_concepts = []
                refined_question = seed
                related_areas = []
                raw_families = []
                path_templates = []

            # Build QueryPlan
            query_families = []
            if raw_families:
                for fam in raw_families:
                    if isinstance(fam, dict):
                        query_families.append(QueryFamily(
                            name=fam.get("name", "core"),
                            queries=fam.get("queries", []),
                            keyConcepts=fam.get("keyConcepts", []),
                        ))
            else:
                # Auto-create families from related areas
                if related_areas:
                    for area in related_areas:
                        query_families.append(QueryFamily(
                            name=area.lower().replace(" ", "_"),
                            queries=[f"{seed} {area}"],
                            keyConcepts=[],
                        ))
                # Always have a core family
                query_families.insert(0, QueryFamily(
                    name="core",
                    queries=expanded_terms[:3],
                    keyConcepts=key_concepts[:5],
                ))

            query_plan = QueryPlan(
                refinedQuestion=refined_question,
                queryFamilies=query_families,
                expandedTerms=expanded_terms[:5],
                keyConcepts=key_concepts[:10],
                pathTemplates=path_templates,
                bftsConfig=bfts_config,
            )

            inputs = {"seedQuery": seed, "paperType": paper_type}
            outputs = {
                "refinedQuestion": refined_question,
                "expandedTerms": expanded_terms[:5],
                "keyConcepts": key_concepts[:10],
                "queryPlan": query_plan.model_dump(),
                "llmLatencyMs": response.latency_ms,
            }

        except Exception as e:
            logger.warning(f"LLM query expansion failed: {e}, using fallback")
            expanded_terms = [
                seed,
                f"{seed} machine learning",
                f"{seed} deep learning",
                f"{seed} neural network",
            ]
            if domain != "general":
                expanded_terms.append(f"{seed} {domain}")

            query_plan = QueryPlan(
                refinedQuestion=seed,
                queryFamilies=[
                    QueryFamily(
                        name="core",
                        queries=expanded_terms[:3],
                        keyConcepts=[],
                    )
                ],
                expandedTerms=expanded_terms,
                keyConcepts=[],
                bftsConfig=bfts_config,
            )

            inputs = {"seedQuery": seed}
            outputs = {
                "expandedTerms": expanded_terms,
                "queryPlan": query_plan.model_dump(),
                "error": str(e),
            }

        return inputs, outputs, []
    
    def _step_literature_search(self, session: IdeaSession) -> tuple:
        """Search for literature and build LiteratureGraph v0.

        Uses multi-source search (Semantic Scholar, arXiv, local corpus),
        deduplicates by doi > arxivId > semanticScholarId > title hash,
        creates RawPaper[] + LiteratureGraph v0.
        Also creates LiteratureItem[] for backward compatibility.
        """
        seed = session.config.seedQuery
        max_papers = session.config.maxPapers

        # Get expanded terms from Step 1
        search_queries = [seed]
        if session.trace:
            for step in session.trace.steps:
                if step.name == "expandQuery" and step.outputs.get("expandedTerms"):
                    search_queries = step.outputs["expandedTerms"]
                    break

        # Search across sources
        search_service = get_search_service()
        all_results: List[SearchResult] = []
        sources_used: List[str] = []

        for query in search_queries[:3]:
            try:
                results = search_service.search(query, limit=max_papers)
                all_results.extend(results)
                logger.info(f"Search for '{query}' returned {len(results)} results")
            except Exception as e:
                logger.warning(f"Search failed for '{query}': {e}")

        # Dedup chain: doi > arxivId > semanticScholarId > normalized title hash
        seen_dois: set = set()
        seen_arxiv_ids: set = set()
        seen_s2_ids: set = set()
        seen_title_hashes: set = set()
        unique_results: List[SearchResult] = []

        for result in all_results:
            # Check DOI
            if result.doi and result.doi in seen_dois:
                continue
            # Check arXiv ID
            if result.arxiv_id and result.arxiv_id in seen_arxiv_ids:
                continue
            # Check Semantic Scholar ID (extracted from URL if available)
            s2_id = None
            if result.source == "semantic_scholar" and result.url:
                s2_match = re.search(r'SemanticScholarID:(\w+)', result.url)
                if s2_match:
                    s2_id = s2_match.group(1)
            if s2_id and s2_id in seen_s2_ids:
                continue
            # Check normalized title hash
            title_hash = _compute_title_hash(result.title)
            if title_hash in seen_title_hashes:
                continue

            # Register all keys
            if result.doi:
                seen_dois.add(result.doi)
            if result.arxiv_id:
                seen_arxiv_ids.add(result.arxiv_id)
            if s2_id:
                seen_s2_ids.add(s2_id)
            seen_title_hashes.add(title_hash)

            unique_results.append(result)
            if result.source not in sources_used:
                sources_used.append(result.source)

        # Limit results
        unique_results = unique_results[:max_papers]

        # Create RawPaper objects
        raw_papers: List[RawPaper] = []
        literature_items: List[LiteratureItem] = []
        literature_ids: List[str] = []

        for i, result in enumerate(unique_results):
            base_score = result.relevance_score if result.relevance_score > 0 else (1.0 - (i * 0.05))
            title_hash = _compute_title_hash(result.title)

            # Extract Semantic Scholar ID
            s2_id = None
            if result.source == "semantic_scholar" and result.url:
                s2_match = re.search(r'SemanticScholarID:(\w+)', result.url)
                if s2_match:
                    s2_id = s2_match.group(1)

            raw_paper = RawPaper(
                id=generate_raw_paper_id(),
                sessionId=session.id,
                title=result.title,
                authors=result.authors,
                year=result.year,
                venue=result.venue,
                url=result.url,
                doi=result.doi,
                arxivId=result.arxiv_id,
                semanticScholarId=s2_id,
                citationCount=result.citation_count or 0,
                abstract=result.abstract or "",
                source=[result.source] if result.source else [],
                normalizedTitleHash=title_hash,
                relevanceScore=min(1.0, max(0.0, base_score)),
            )
            self.raw_paper_storage.create(raw_paper)
            raw_papers.append(raw_paper)

            # Also create LiteratureItem for backward compatibility
            lit_item = LiteratureItem(
                id=generate_literature_id(),
                sessionId=session.id,
                title=result.title,
                authors=result.authors,
                venue=result.venue,
                year=result.year,
                url=result.url,
                doi=result.doi,
                arxivId=result.arxiv_id,
                snippet=(result.abstract or "")[:500],
                relevanceScore=min(1.0, max(0.0, base_score)),
                source=result.source,
            )
            self.literature_storage.create(lit_item)
            literature_items.append(lit_item)
            literature_ids.append(lit_item.id)

        # Build LiteratureGraph v0
        graph = self.graph_builder.build_graph_v0(
            session_id=session.id,
            raw_papers=raw_papers,
        )
        self.graph_storage.create(graph)

        inputs = {"seedQuery": seed, "maxPapers": max_papers, "searchQueries": search_queries[:3]}
        outputs = {
            "paperCount": len(raw_papers),
            "rawPaperIds": [p.id for p in raw_papers],
            "graphId": graph.id,
            "sourcesUsed": sources_used,
            # Backward-compat fields
            "paperIds": literature_ids,
        }

        return inputs, outputs, []
    
    def _step_novelty_check(self, session: IdeaSession) -> tuple:
        """Check novelty: cluster papers, select for deep reading, extract structured info.

        1. Load LiteratureGraph v0 from storage
        2. Cluster papers and select by role distribution
        3. Deep-read selected papers (LLM structured extraction)
        4. Build LiteratureMap
        5. Upgrade graph to v1
        6. Create preliminary BFTSHandoff
        """
        seed = session.config.seedQuery
        paper_type = session.config.paperType
        literature = self.get_literature(session.id)

        # Load LiteratureGraph v0 from storage
        graph = self.graph_storage.get_by_session(session.id)
        if not graph:
            logger.warning("No LiteratureGraph v0 found for session %s, using fallback", session.id)
            return self._fallback_novelty_check(session, literature, seed)

        # Get raw papers
        raw_papers = self.raw_paper_storage.list_by_session(session.id)

        # Load QueryPlan for must-cite list
        must_cite_list = session.config.mustCiteList

        # Step 3a: Cluster papers
        graph = self.graph_builder.cluster_papers(graph)

        # Step 3b: Select papers by role
        num_select = min(40, max(5, len(raw_papers) // 2))
        graph, selected_paper_ids = self.graph_builder.select_papers(
            graph, num_select=num_select, must_cite_list=must_cite_list
        )

        # Step 3c: Deep-read selected papers
        structured_papers = self.deep_reader.extract_structured_papers(
            session=session,
            selected_paper_ids=selected_paper_ids,
            raw_papers=raw_papers,
        )
        for sp in structured_papers:
            self.structured_storage.create(sp)

        # Step 3d: Build LiteratureMap
        literature_map = self.deep_reader.build_literature_map(
            session_id=session.id,
            selected_paper_ids=selected_paper_ids,
            structured_papers=structured_papers,
            graph=graph,
        )
        self.map_storage.create(literature_map)

        # Step 3e: Upgrade graph to v1
        graph = self.graph_builder.upgrade_to_v1(graph)
        self.graph_storage.update(graph)

        # Step 3f: Create preliminary BFTSHandoff
        query_plan_dict = self._get_step_output(session, "expandQuery", "queryPlan")
        bfts_config = BFTSConfig()
        if query_plan_dict:
            try:
                bfts_config = BFTSConfig(**query_plan_dict.get("bftsConfig", {}))
            except Exception:
                pass

        handoff = BFTSHandoff(
            id=generate_handoff_id(),
            sessionId=session.id,
            reasoningKgId=None,  # Phase 2
            literatureMapId=literature_map.id,
            pathSeedIds=[],  # Phase 2
            selectedPaperIds=selected_paper_ids,
            bftsConfig=bfts_config,
        )
        self.handoff_storage.create(handoff)

        # Also run the original LLM novelty check for backward compatibility
        lit_summary = "\n".join([
            f"- {item.title} ({item.year or 'N/A'}): {item.snippet[:150]}..."
            for item in literature[:8]
        ])

        covered_areas: List[str] = []
        gaps: List[str] = []
        novel_directions: List[str] = []
        assessment = ""

        try:
            client = get_provider_client(session.config.providerName)
            user_prompt = prompts.NOVELTY_CHECK_USER.format(
                seed_query=seed,
                paper_type=paper_type,
                literature_summary=lit_summary
            )
            messages = [
                ChatMessage(role="system", content=prompts.NOVELTY_CHECK_SYSTEM),
                ChatMessage(role="user", content=user_prompt)
            ]
            response = client.chat(messages, model=session.config.model, max_tokens=800)
            try:
                data = json.loads(response.text)
                covered_areas = data.get("coveredAreas", [])
                gaps = data.get("gaps", [])
                novel_directions = data.get("novelDirections", [])
                assessment = data.get("noveltyAssessment", "")
            except json.JSONDecodeError:
                for line in response.text.split("\n"):
                    line = line.strip()
                    if "gap" in line.lower() or "missing" in line.lower():
                        gaps.append(line.strip("-").strip())
                    elif "covered" in line.lower() or "existing" in line.lower():
                        covered_areas.append(line.strip("-").strip())
                assessment = response.text[:300]
        except Exception as e:
            logger.warning(f"LLM novelty check failed: {e}")
            covered_topics = set()
            for item in literature:
                words = item.title.lower().split()
                covered_topics.update(w for w in words if len(w) > 4)
            covered_areas = list(covered_topics)[:15]
            gaps = [
                f"Scalability of {seed} methods",
                f"Interpretability in {seed}",
                f"Theoretical foundations of {seed}",
            ]
            novel_directions = [
                f"Novel architectures for {seed}",
                f"Efficient training methods for {seed}",
            ]

        inputs = {"literatureCount": len(literature), "topic": seed}
        outputs = {
            # New dual-graph outputs
            "graphId": graph.id,
            "graphVersion": graph.version,
            "selectedPaperIds": selected_paper_ids,
            "structuredPaperCount": len(structured_papers),
            "literatureMapId": literature_map.id,
            "handoffId": handoff.id,
            "clusterCount": len(graph.clusters),
            # Backward-compat outputs for Step 4
            "coveredAreas": covered_areas[:10],
            "gaps": gaps[:5],
            "novelDirections": novel_directions[:5],
            "noveltyAssessment": assessment,
        }

        return inputs, outputs, []

    def _fallback_novelty_check(self, session: IdeaSession, literature: List[LiteratureItem], seed: str) -> tuple:
        """Fallback novelty check when Graph v0 is unavailable."""
        covered_topics = set()
        for item in literature:
            words = item.title.lower().split()
            covered_topics.update(w for w in words if len(w) > 4)

        inputs = {"literatureCount": len(literature)}
        outputs = {
            "coveredAreas": list(covered_topics)[:15],
            "gaps": [
                f"Scalability of {seed} methods",
                f"Interpretability in {seed}",
                f"Theoretical foundations of {seed}",
                f"Real-world deployment of {seed}",
            ],
            "novelDirections": [],
            "noveltyAssessment": f"Fallback novelty assessment for {seed}: literature corpus contains {len(literature)} papers covering {len(covered_topics)} topic areas.",
            "graphId": None,
            "graphVersion": 0,
            "selectedPaperIds": [],
            "structuredPaperCount": 0,
        }
        return inputs, outputs, []
    
    def _step_gap_analysis(self, session: IdeaSession) -> tuple:
        """Build ReasoningKG (Graph 2), link Graph 1 signals, generate path seeds.

        Contract: reads only Step 3 outputs (StructuredPaper[] + LiteratureMap).
        Does NOT read RawPaper[] or LiteratureGraph directly.
        """
        seed = session.config.seedQuery
        paper_type = session.config.paperType
        literature = self.get_literature(session.id)

        # Load Step 3 outputs
        literature_map = self.map_storage.get_by_session(session.id)
        structured_papers = self.structured_storage.list_by_session(session.id)

        if not literature_map or not structured_papers:
            logger.warning(
                "Step 3 outputs not available for session %s, using fallback gap analysis",
                session.id,
            )
            return self._fallback_gap_analysis(session, seed, literature)

        try:
            # Step 4a: Build ReasoningKG
            reasoning_kg = self.reasoning_builder.build_reasoning_kg(
                session=session,
                structured_papers=structured_papers,
                literature_map=literature_map,
            )
            self.reasoning_kg_storage.create(reasoning_kg)

            # Step 4b: Link Graph 1 signals to Graph 2 entities/relations
            evidence_links = self.graph_linker.link_graphs(
                literature_map=literature_map,
                reasoning_kg=reasoning_kg,
            )
            for link in evidence_links:
                self.evidence_link_storage.create(link)

            # Step 4c: Generate reasoning path seeds
            path_seeds = self.path_seed_gen.generate_seeds(
                session_id=session.id,
                reasoning_kg=reasoning_kg,
                evidence_links=evidence_links,
                structured_papers=structured_papers,
                literature_map=literature_map,
            )
            for seed in path_seeds:
                self.path_seed_storage.create(seed)

            # Step 4d: Update BFTSHandoff with Phase 2 data
            existing_handoff = self.handoff_storage.get_by_session(session.id)
            if existing_handoff:
                # Delete preliminary handoff, create final version with new ID
                self.handoff_storage.delete(existing_handoff.id)
            final_handoff = BFTSHandoff(
                id=generate_handoff_id(),
                sessionId=session.id,
                reasoningKgId=reasoning_kg.id,
                literatureMapId=literature_map.id,
                pathSeedIds=[s.seedId for s in path_seeds],
                selectedPaperIds=(existing_handoff.selectedPaperIds if existing_handoff
                                  else literature_map.selectedPaperIds),
                bftsConfig=existing_handoff.bftsConfig if existing_handoff else BFTSConfig(),
            )
            self.handoff_storage.create(final_handoff)

        except Exception as e:
            logger.error(f"Dual-graph Step 4 failed: {e}, using fallback")
            return self._fallback_gap_analysis(session, seed, literature)

        # Also run LLM gap analysis for backward-compat outputs
        novelty_assessment = self._get_step_output(session, "noveltyCheck", "noveltyAssessment", "")
        gaps_from_novelty = self._get_step_output(session, "noveltyCheck", "gaps", [])

        lit_summary = "\n".join([
            f"- {item.title} ({item.year or 'N/A'}): {item.snippet[:150]}..."
            for item in literature[:8]
        ])
        gaps_text = "\n".join([f"- {g}" for g in gaps_from_novelty[:5]])

        gap_analysis = []
        prioritized_gaps = []
        opportunities = []

        try:
            client = get_provider_client(session.config.providerName)
            user_prompt = prompts.GAP_ANALYSIS_USER.format(
                seed_query=seed,
                paper_type=paper_type,
                literature_summary=lit_summary,
                novelty_assessment=novelty_assessment or "Not available",
                gaps=gaps_text or "None identified yet",
            )
            messages = [
                ChatMessage(role="system", content=prompts.GAP_ANALYSIS_SYSTEM),
                ChatMessage(role="user", content=user_prompt),
            ]
            response = client.chat(messages, model=session.config.model, max_tokens=1000)
            try:
                data = json.loads(response.text)
                gap_analysis = data.get("gapAnalysis", [])
                prioritized_gaps = data.get("prioritizedGaps", [])
                opportunities = data.get("researchOpportunities", [])
            except json.JSONDecodeError:
                for line in response.text.split("\n"):
                    line = line.strip()
                    if line.startswith("-") or line.startswith("*"):
                        content = line.strip("-*").strip()
                        if "opportunity" in line.lower():
                            opportunities.append(content)
                        else:
                            prioritized_gaps.append(content)
        except Exception as e:
            logger.warning(f"LLM gap analysis failed: {e}")

        inputs = {"topic": seed, "literatureCount": len(literature)}
        outputs = {
            # Phase 2 outputs
            "reasoningKgId": reasoning_kg.id,
            "evidenceLinkCount": len(evidence_links),
            "pathSeedIds": [s.seedId for s in path_seeds],
            "pathSeedCount": len(path_seeds),
            # Backward-compat outputs
            "gapAnalysis": gap_analysis[:5],
            "prioritizedGaps": prioritized_gaps[:5],
            "researchOpportunities": opportunities[:5],
        }

        return inputs, outputs, []

    def _fallback_gap_analysis(self, session: IdeaSession, seed: str, literature) -> tuple:
        """Fallback gap analysis when Step 3 outputs are unavailable."""
        gaps_from_novelty = self._get_step_output(session, "noveltyCheck", "gaps", [])
        inputs = {"topic": seed}
        outputs = {
            "reasoningKgId": None,
            "evidenceLinkCount": 0,
            "pathSeedIds": [],
            "pathSeedCount": 0,
            "gapAnalysis": [],
            "prioritizedGaps": gaps_from_novelty[:5] if gaps_from_novelty else [
                f"Scalability of {seed} methods",
                f"Interpretability in {seed}",
                f"Theoretical foundations of {seed}",
            ],
            "researchOpportunities": [
                f"Novel architectures for {seed}",
                f"Efficient training methods for {seed}",
            ],
        }
        return inputs, outputs, []
    
    def _step_idea_brainstorm_bfts(self, session: IdeaSession) -> tuple:
        """Generate candidate ideas using BFTS tree search + reflection loop.

        Replaces the single-shot LLM call with:
          1. Load BFTSHandoff from Step 4
          2. Initialize BFTSSearchTree with path seeds
          3. Run tree search (each node gets reflection loop)
          4. Convert terminal nodes to IdeaCandidate[]

        Falls back to _step_idea_brainstorm_legacy() when BFTSHandoff is unavailable.
        """
        seed = session.config.seedQuery
        paper_type = session.config.paperType
        max_candidates = session.config.maxCandidates

        # Try to load BFTSHandoff (output of Step 4)
        handoff = None
        try:
            handoff = self.handoff_storage.get_by_session(session.id)
        except Exception as e:
            logger.warning(f"BFTSHandoff not available: {e}, using legacy brainstorm")

        if not handoff:
            logger.info("No BFTSHandoff found, falling back to legacy brainstorm")
            return self._step_idea_brainstorm_legacy(session)

        # Load BFTS config
        bfts_config = handoff.bftsConfig or BFTSConfig(
            maxNodes=min(20, max(10, max_candidates * 2)),
            beamWidth=min(2, max(1, max_candidates // 2)),
            maxReflectionRounds=2,
        )

        # Override with conservative defaults (user chose conservative)
        bfts_config = BFTSConfig(
            maxNodes=min(bfts_config.maxNodes, 20),
            maxIterations=min(bfts_config.maxIterations, 4),
            beamWidth=max(1, min(bfts_config.beamWidth, 2)),
            expansionWidth=max(1, min(bfts_config.expansionWidth, 2)),
            maxLiteratureProbes=min(bfts_config.maxLiteratureProbes, 12),
            maxReflectionRounds=max(1, min(bfts_config.maxReflectionRounds, 2)),
            minEvidenceSupport=bfts_config.minEvidenceSupport,
            minGraphGrounding=bfts_config.minGraphGrounding,
            pruneDuplicateThreshold=bfts_config.pruneDuplicateThreshold,
            scoreWeights=bfts_config.scoreWeights,
        )

        # Load path seeds
        path_seeds: List[ReasoningPathSeed] = []
        if handoff.pathSeedIds:
            for sid in handoff.pathSeedIds[:bfts_config.beamWidth]:
                try:
                    seed_obj = self.path_seed_storage.get(sid)
                    if seed_obj:
                        path_seeds.append(seed_obj)
                except Exception:
                    pass

        if not path_seeds:
            logger.info("No path seeds available, falling back to legacy brainstorm")
            return self._step_idea_brainstorm_legacy(session)

        # Load structured papers for literature context
        structured_papers: List[StructuredPaper] = []
        try:
            structured_papers = self.structured_storage.list_by_session(session.id)
        except Exception:
            pass

        # Build literature context string
        literature_context = self._build_bfts_literature_context(structured_papers)

        logger.info(
            f"BFTS: {len(path_seeds)} seeds, "
            f"maxNodes={bfts_config.maxNodes}, "
            f"beamWidth={bfts_config.beamWidth}, "
            f"maxReflectionRounds={bfts_config.maxReflectionRounds}"
        )

        try:
            from app.modules.idea.bfts_search import BFTSSearchTree

            tree = BFTSSearchTree(
                session_id=session.id,
                bfts_config=bfts_config,
                provider_name=session.config.providerName,
                model=session.config.model,
                path_seeds=path_seeds,
                structured_papers=structured_papers,
                literature_context=literature_context,
                seed_query=seed,
                paper_type=paper_type,
            )

            candidates = tree.run()

            if not candidates:
                logger.warning("BFTS produced no candidates, falling back to legacy")
                return self._step_idea_brainstorm_legacy(session)

            # Store candidates
            candidate_ids = []
            for candidate in candidates:
                self.candidate_storage.create(candidate)
                candidate_ids.append(candidate.id)
                if candidate.id not in session.candidateIds:
                    session.candidateIds.append(candidate.id)

            inputs = {
                "seedQuery": seed,
                "paperType": paper_type,
                "method": "bfts_tree_search",
                "seedCount": len(path_seeds),
                "maxNodes": bfts_config.maxNodes,
                "beamWidth": bfts_config.beamWidth,
                "maxReflectionRounds": bfts_config.maxReflectionRounds,
            }
            outputs = {
                "candidateCount": len(candidates),
                "candidateIds": candidate_ids,
                "method": "bfts_tree_search",
                "bftsConfig": bfts_config.model_dump(),
            }

            logger.info(f"BFTS: generated {len(candidates)} candidates from tree search")

        except Exception as e:
            logger.error(f"BFTS search failed: {e}, falling back to legacy brainstorm")
            import traceback
            logger.error(traceback.format_exc())
            return self._step_idea_brainstorm_legacy(session)

        return inputs, outputs, []

    def _build_bfts_literature_context(
        self, structured_papers: List[StructuredPaper], limit: int = 8
    ) -> str:
        """Build literature context string for BFTS reflection loops."""
        if not structured_papers:
            return "(No structured literature available yet)"

        lines = []
        for i, sp in enumerate(structured_papers[:limit]):
            title = sp.title or "(untitled)"
            year = sp.year or "N/A"
            claims_str = ""
            if sp.claims:
                claims_str = ". ".join(c.text[:100] for c in sp.claims[:2])
            lines.append(
                f"[{i+1}] {title} ({year})\n"
                f"    Key claims: {claims_str}"
            )
        return "\n\n".join(lines)

    def _step_idea_brainstorm(self, session: IdeaSession) -> tuple:
        """Generate candidate ideas — routes to BFTS or legacy based on availability."""
        # Use BFTS if handoff is available
        try:
            handoff = self.handoff_storage.get_by_session(session.id)
            if handoff and handoff.pathSeedIds:
                return self._step_idea_brainstorm_bfts(session)
        except Exception:
            pass

        # Fallback: legacy single-shot LLM
        return self._step_idea_brainstorm_legacy(session)

    def _step_idea_brainstorm_legacy(self, session: IdeaSession) -> tuple:
        """Original single-shot LLM brainstorm (fallback)."""
        seed = session.config.seedQuery
        paper_type = session.config.paperType
        max_candidates = session.config.maxCandidates
        literature = self.get_literature(session.id)

        # Get gap analysis results
        gap_analysis = []
        opportunities = []
        prioritized_gaps = []
        if session.trace:
            for step in session.trace.steps:
                if step.name == "gapAnalysis":
                    gap_analysis = step.outputs.get("gapAnalysis", [])
                    opportunities = step.outputs.get("researchOpportunities", [])
                    prioritized_gaps = step.outputs.get("prioritizedGaps", [])
                    break

        # Build context
        key_papers = "\n".join([
            f"- {item.title} ({item.year or 'N/A'})"
            for item in literature[:5]
        ])

        gap_text = json.dumps(gap_analysis[:3], indent=2) if gap_analysis else "\n".join([f"- {g}" for g in prioritized_gaps[:3]])
        opp_text = "\n".join([f"- {o}" for o in opportunities[:3]]) if opportunities else "Based on identified gaps"

        try:
            client = get_provider_client(session.config.providerName)

            user_prompt = prompts.IDEA_BRAINSTORM_USER.format(
                seed_query=seed,
                paper_type=paper_type,
                max_candidates=max_candidates,
                gap_analysis=gap_text,
                opportunities=opp_text,
                key_papers=key_papers
            )

            messages = [
                ChatMessage(role="system", content=prompts.IDEA_BRAINSTORM_SYSTEM),
                ChatMessage(role="user", content=user_prompt)
            ]

            response = client.chat(messages, model=session.config.model, max_tokens=3000)

            # Parse ideas from response
            candidates = self._parse_ideas_json(session.id, response.text, max_candidates)

            if not candidates:
                # Fallback to text parsing
                candidates = self._parse_ideas(session.id, response.text, max_candidates)

            if not candidates:
                # Generate fallback
                candidates = self._generate_fallback_candidates(session.id, seed, min(3, max_candidates))

            # Store candidates
            candidate_ids = []
            for candidate in candidates:
                self.candidate_storage.create(candidate)
                candidate_ids.append(candidate.id)
                session.candidateIds.append(candidate.id)

            inputs = {"topic": seed, "maxCandidates": max_candidates, "paperType": paper_type}
            outputs = {
                "candidateCount": len(candidates),
                "candidateIds": candidate_ids,
                "llmLatencyMs": response.latency_ms,
                "method": "legacy_single_shot",
            }

        except Exception as e:
            logger.error(f"LLM brainstorm failed: {e}")
            # Generate fallback candidates
            candidates = self._generate_fallback_candidates(session.id, seed, min(3, max_candidates))

            candidate_ids = []
            for candidate in candidates:
                self.candidate_storage.create(candidate)
                candidate_ids.append(candidate.id)
                session.candidateIds.append(candidate.id)

            inputs = {"topic": seed}
            outputs = {
                "candidateCount": len(candidates),
                "candidateIds": candidate_ids,
                "error": str(e),
                "method": "legacy_fallback",
            }

        return inputs, outputs, []
    
    def _parse_ideas_json(self, session_id: str, text: str, max_count: int) -> List[IdeaCandidate]:
        """Parse ideas from JSON response."""
        candidates = []
        
        # Try to extract JSON from response
        try:
            # Find JSON block
            json_match = re.search(r'\{[\s\S]*"ideas"[\s\S]*\}', text)
            if json_match:
                data = json.loads(json_match.group())
                ideas = data.get("ideas", [])
                
                for idea in ideas[:max_count]:
                    # Parse experiments
                    experiments = []
                    for exp in idea.get("requiredExperiments", []):
                        if isinstance(exp, dict):
                            experiments.append(ExperimentSpec(
                                name=exp.get("name", "Experiment"),
                                description=exp.get("description", ""),
                                metrics=exp.get("metrics", []),
                                datasets=exp.get("datasets", [])
                            ))
                    
                    # Parse risks
                    risks = []
                    for risk in idea.get("risks", []):
                        if isinstance(risk, dict):
                            risks.append(RiskItem(
                                risk=risk.get("risk", ""),
                                mitigation=risk.get("mitigation", "")
                            ))
                    
                    candidate = IdeaCandidate(
                        id=generate_candidate_id(),
                        sessionId=session_id,
                        title=idea.get("title", "Untitled Idea"),
                        problem=idea.get("problem", "Problem statement pending."),
                        keyInsight=idea.get("keyInsight", idea.get("approach", "Key insight pending.")),
                        novelty=5.0,
                        noveltyRationale="Pending ranking",
                        feasibility=5.0,
                        feasibilityRationale="Pending ranking",
                        impact=5.0,
                        impactRationale="Pending ranking",
                        scoringMethod="pending",
                        risks=risks,
                        requiredExperiments=experiments,
                        expectedMetrics=idea.get("expectedOutcomes", []),
                        draftPlan=DraftPlan(
                            researchQuestion=idea.get("problem", ""),
                            hypothesis=idea.get("keyInsight", ""),
                            methodology=idea.get("approach", "To be defined"),
                            expectedOutcomes=idea.get("expectedOutcomes", []),
                        ),
                    )
                    candidates.append(candidate)
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning(f"JSON parsing failed: {e}")
        
        return candidates
    
    def _parse_ideas(self, session_id: str, text: str, max_count: int) -> List[IdeaCandidate]:
        """Parse ideas from LLM response."""
        candidates = []
        
        # Simple parsing - split by numbered ideas
        sections = text.split("\n\n")
        current_idea = {}
        
        for section in sections:
            lines = section.strip().split("\n")
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                
                lower = line.lower()
                if "title:" in lower or line.startswith("1."):
                    # Save previous idea if exists
                    if current_idea.get("title"):
                        candidates.append(self._create_candidate(session_id, current_idea))
                        if len(candidates) >= max_count:
                            return candidates
                    current_idea = {"title": line.split(":", 1)[-1].strip() if ":" in line else line[2:].strip()}
                elif "problem" in lower:
                    current_idea["problem"] = line.split(":", 1)[-1].strip() if ":" in line else line
                elif "insight" in lower:
                    current_idea["insight"] = line.split(":", 1)[-1].strip() if ":" in line else line
                elif "novelty" in lower and "score" in lower:
                    try:
                        score = float(''.join(c for c in line if c.isdigit() or c == '.'))
                        current_idea["novelty"] = min(10, max(0, score))
                    except:
                        current_idea["novelty"] = 7.0
                elif "feasibility" in lower and "score" in lower:
                    try:
                        score = float(''.join(c for c in line if c.isdigit() or c == '.'))
                        current_idea["feasibility"] = min(10, max(0, score))
                    except:
                        current_idea["feasibility"] = 7.0
                elif "impact" in lower and "score" in lower:
                    try:
                        score = float(''.join(c for c in line if c.isdigit() or c == '.'))
                        current_idea["impact"] = min(10, max(0, score))
                    except:
                        current_idea["impact"] = 7.0
        
        # Don't forget the last idea
        if current_idea.get("title") and len(candidates) < max_count:
            candidates.append(self._create_candidate(session_id, current_idea))
        
        return candidates
    
    def _create_candidate(self, session_id: str, data: Dict[str, Any]) -> IdeaCandidate:
        """Create a candidate from parsed data."""
        return IdeaCandidate(
            id=generate_candidate_id(),
            sessionId=session_id,
            title=data.get("title", "Untitled Idea"),
            problem=data.get("problem", "Problem statement pending."),
            keyInsight=data.get("insight", "Key insight pending."),
            novelty=data.get("novelty", 5.0),
            noveltyRationale="Pending ranking",
            feasibility=data.get("feasibility", 5.0),
            feasibilityRationale="Pending ranking",
            impact=data.get("impact", 5.0),
            impactRationale="Pending ranking",
            scoringMethod="pending",
            draftPlan=DraftPlan(
                researchQuestion=data.get("problem", ""),
                hypothesis=data.get("insight", ""),
                methodology="To be defined",
                expectedOutcomes=["Improved performance", "Novel insights"],
            ),
        )
    
    def _generate_fallback_candidates(self, session_id: str, seed: str, count: int) -> List[IdeaCandidate]:
        """Generate fallback candidates when LLM fails."""
        templates = [
            {
                "title": f"Scalable {seed} with Efficient Attention",
                "problem": f"Current {seed} methods do not scale to large datasets.",
                "insight": "Using sparse attention patterns can reduce complexity.",
                "novelty": 7.5,
                "feasibility": 8.0,
                "impact": 7.0,
            },
            {
                "title": f"Interpretable {seed} via Concept Bottlenecks",
                "problem": f"{seed} models lack interpretability.",
                "insight": "Concept bottleneck layers provide human-understandable explanations.",
                "novelty": 8.0,
                "feasibility": 7.0,
                "impact": 8.5,
            },
            {
                "title": f"Self-Supervised {seed} for Low-Resource Settings",
                "problem": f"{seed} requires large labeled datasets.",
                "insight": "Self-supervised pretraining can reduce label requirements.",
                "novelty": 7.0,
                "feasibility": 8.5,
                "impact": 7.5,
            },
        ]
        
        candidates = []
        for i, template in enumerate(templates[:count]):
            candidates.append(self._create_candidate(session_id, template))
        
        return candidates
    
    def _step_rank_candidates(self, session: IdeaSession) -> tuple:
        """Rank candidates with multi-criteria scoring, evidence binding, and critique.

        Step 6 delivers:
          1. Numeric multi-criteria scoring (via ranking_service)
          2. CandidateGraphEvidence — rule-based dual-graph mapping
          3. PriorWorkComparison — LLM comparison vs selected papers
          4. IdeaCritique — LLM structured critique
          5. RankedIdeaOutput — persisted to storage

        Falls back gracefully when dual-graph artifacts are unavailable.
        """
        seed = session.config.seedQuery
        paper_type = session.config.paperType
        domain = session.config.domain or "general"
        candidates = self.get_candidates(session.id)

        if not candidates:
            return {"candidateCount": 0}, {"rankings": [], "error": "No candidates to rank"}, []

        # --- Phase 1: Numeric multi-criteria scoring ---
        ranking_service = get_ranking_service()
        try:
            updated_candidates, ranking_results = ranking_service.rank_candidates(
                candidates=candidates,
                seed_query=seed,
                paper_type=paper_type,
                domain=domain,
                provider_name=session.config.providerName,
                model=session.config.model,
                session_id=session.id,
            )
        except Exception as e:
            logger.error(f"Ranking service failed: {e}")
            updated_candidates = candidates
            ranking_results = []

        # Sort by overallScore descending
        ranked = sorted(updated_candidates, key=lambda c: c.overallScore, reverse=True)

        # --- Phase 2: Load dual-graph artifacts for evidence binding ---
        structured_papers = []
        reasoning_kg = None
        path_seeds = []
        evidence_links = []
        handoff = None
        try:
            structured_papers = self.structured_storage.list_by_session(session.id)
            reasoning_kg = self.reasoning_kg_storage.get_by_session(session.id)
            path_seeds = self.path_seed_storage.list_by_session(session.id)
            evidence_links = self.evidence_link_storage.list_by_session(session.id)
        except Exception as e:
            logger.warning(f"Dual-graph artifact loading failed: {e}, evidence binding will be partial")

        try:
            handoff = self.handoff_storage.get_by_session(session.id)
        except Exception:
            pass

        # --- Phase 3: Build CandidateGraphEvidence per candidate (rule-based) ---
        evidence_list: List[CandidateGraphEvidence] = []
        for candidate in ranked:
            evidence = self._build_candidate_evidence(
                candidate=candidate,
                structured_papers=structured_papers,
                reasoning_kg=reasoning_kg,
                path_seeds=path_seeds,
                evidence_links=evidence_links,
            )
            evidence_list.append(evidence)

        # --- Phase 4: LLM-driven PriorWorkComparison + IdeaCritique ---
        prior_work_comparisons: List[PriorWorkComparison] = []
        critiques: List[IdeaCritique] = []
        top_k = min(5, len(ranked))  # Deep analysis on top 5 candidates

        if structured_papers and ranked:
            literature_context = self._build_ranking_literature_context(
                structured_papers, reasoning_kg, path_seeds
            )
            try:
                for candidate in ranked[:top_k]:
                    comparison, critique = self._llm_analyze_candidate(
                        candidate=candidate,
                        seed_query=seed,
                        paper_type=paper_type,
                        domain=domain,
                        literature_context=literature_context,
                        provider_name=session.config.providerName,
                        model=session.config.model,
                    )
                    prior_work_comparisons.append(comparison)
                    critiques.append(critique)
            except Exception as e:
                logger.warning(f"LLM candidate analysis failed: {e}")

        # --- Phase 5: Backfill evidence/critique into final candidates (PDF v5) ---
        evidence_by_candidate = {e.candidateId: e for e in evidence_list}
        comparison_by_candidate: Dict[str, list] = {}
        for pc in prior_work_comparisons:
            comparison_by_candidate.setdefault(pc.candidateId, []).append(pc)
        critique_by_candidate = {c.candidateId: c for c in critiques}

        for candidate in ranked:
            # Embed graphEvidence into the candidate itself
            ev = evidence_by_candidate.get(candidate.id)
            if ev:
                candidate.graphEvidence = ev

            # Embed closest prior work comparisons
            comparisons = comparison_by_candidate.get(candidate.id, [])
            if comparisons and not candidate.closestPriorWork:
                candidate.closestPriorWork = comparisons

            # Embed critique
            crit = critique_by_candidate.get(candidate.id)
            if crit and not candidate.critique:
                candidate.critique = crit

            # Keep GET /ideas/sessions/{id}/candidates aligned with the final Step 6 candidate.
            try:
                self.candidate_storage.create(candidate)
            except Exception as e:
                logger.warning(f"Failed to persist final candidate {candidate.id}: {e}")

        # --- Phase 6: Assemble and persist RankedIdeaOutput ---
        scores_list = [c.overallScore for c in ranked]
        variance = 0.0
        if len(scores_list) > 1:
            mean = sum(scores_list) / len(scores_list)
            variance = sum((s - mean) ** 2 for s in scores_list) / len(scores_list)

        ranked_output = RankedIdeaOutput(
            id=generate_ranked_output_id(),
            sessionId=session.id,
            rankedCandidates=ranked,
            evidence=evidence_list,
            priorWorkComparisons=prior_work_comparisons,
            critiques=critiques,
            scoreVariance=round(variance, 3),
            minScore=round(min(scores_list), 2) if scores_list else 0.0,
            maxScore=round(max(scores_list), 2) if scores_list else 0.0,
            rankedCount=len(ranked),
            topCandidateId=ranked[0].id if ranked else None,
            rankingMethod="llm_multi_criteria" if ranking_results else "heuristic",
        )
        try:
            self.ranked_output_storage.create(ranked_output)
        except Exception as e:
            logger.warning(f"Failed to persist RankedIdeaOutput: {e}")

        # --- Build output for trace ---
        rankings = []
        for rank_idx, candidate in enumerate(ranked, 1):
            rankings.append({
                "id": candidate.id,
                "title": candidate.title,
                "totalScore": round(candidate.overallScore, 2),
                "rank": rank_idx,
                "breakdown": candidate.scoreBreakdown,
                "overallRationale": getattr(candidate, 'overallRationale', ''),
                "scoringConfidence": getattr(candidate, 'scoringConfidence', 0.5),
                "scoringMethod": getattr(candidate, 'scoringMethod', 'heuristic'),
            })

        inputs = {"candidateCount": len(candidates)}
        outputs = {
            "rankings": rankings,
            "scoreVariance": round(variance, 3),
            "minScore": round(min(scores_list), 2) if scores_list else 0,
            "maxScore": round(max(scores_list), 2) if scores_list else 0,
            "rankedOutputId": ranked_output.id,
            "evidenceCount": len(evidence_list),
            "comparisonCount": len(prior_work_comparisons),
            "critiqueCount": len(critiques),
        }

        return inputs, outputs, []

    # =========================================================================
    # Step 6 Helpers
    # =========================================================================

    def _build_candidate_evidence(
        self,
        candidate: IdeaCandidate,
        structured_papers: List[StructuredPaper],
        reasoning_kg: ReasoningKG | None,
        path_seeds: List[ReasoningPathSeed],
        evidence_links: List[GraphEvidenceLink],
    ) -> CandidateGraphEvidence:
        """Build CandidateGraphEvidence by matching candidate to dual-graph artifacts."""
        existing_evidence = getattr(candidate, 'graphEvidence', None)

        def _existing_list(field: str) -> List[Any]:
            if isinstance(existing_evidence, dict):
                value = existing_evidence.get(field, [])
            else:
                value = getattr(existing_evidence, field, []) if existing_evidence else []
            return list(value or [])

        supporting_paper_ids: List[str] = _existing_list("supportingPaperIds")
        supporting_claim_ids: List[str] = _existing_list("supportingClaimIds")
        supporting_entity_ids: List[str] = _existing_list("supportingEntityIds")
        supporting_path_seed_ids: List[str] = _existing_list("supportingPathSeedIds")
        evidence_link_ids: List[str] = _existing_list("evidenceLinkIds")
        probe_paper_ids: List[str] = _existing_list("probePaperIds")
        reasoning_trace: List[Dict[str, Any]] = _existing_list("reasoningTrace")

        if getattr(candidate, "pathSeedId", None) and candidate.pathSeedId not in supporting_path_seed_ids:
            supporting_path_seed_ids.append(candidate.pathSeedId)

        trace_entries = []
        if getattr(candidate, "searchNodeId", None):
            trace_entries.append({"step": "IdeaSearchNode", "id": candidate.searchNodeId})
        if getattr(candidate, "pathSeedId", None):
            trace_entries.append({"step": "ReasoningPathSeed", "id": candidate.pathSeedId})
        trace_entries.append({"step": "IdeaCandidate", "id": candidate.id})
        for entry in trace_entries:
            if entry not in reasoning_trace:
                reasoning_trace.append(entry)

        # Extract keywords from candidate
        candidate_text = f"{candidate.title} {candidate.problem} {candidate.keyInsight}".lower()
        keywords = set(
            w for w in candidate_text.replace(',', ' ').split()
            if len(w) > 3 and w not in ('this', 'that', 'the', 'and', 'for', 'with', 'from')
        )

        # Match keywords against structured paper titles and claims
        for sp in structured_papers:
            paper_text = f"{sp.title} {' '.join(c.text for c in sp.claims)}".lower()
            if any(kw in paper_text for kw in keywords):
                if sp.id not in supporting_paper_ids:
                    supporting_paper_ids.append(sp.id)
                for claim in sp.claims:
                    if any(kw in claim.text.lower() for kw in keywords):
                        if claim.claimId not in supporting_claim_ids:
                            supporting_claim_ids.append(claim.claimId)

        # Match against ReasoningKG entities
        if reasoning_kg:
            for entity in reasoning_kg.entities:
                entity_text = f"{entity.name} {entity.normalizedName}".lower()
                if any(kw in entity_text for kw in keywords):
                    if entity.entityId not in supporting_entity_ids:
                        supporting_entity_ids.append(entity.entityId)

        # Match against PathSeeds via candidate reference fields
        candidate_refs = getattr(candidate, 'references', []) or []
        for ps in path_seeds:
            # Path seeds with overlapping paper IDs or anchor entities
            if any(pid in ps.sourcePaperIds for pid in supporting_paper_ids):
                supporting_path_seed_ids.append(ps.seedId)
                evidence_link_ids.extend(ps.evidenceLinkIds)

        # Dedup
        supporting_paper_ids = list(dict.fromkeys(supporting_paper_ids))
        supporting_claim_ids = list(dict.fromkeys(supporting_claim_ids))
        supporting_entity_ids = list(dict.fromkeys(supporting_entity_ids))
        evidence_link_ids = list(dict.fromkeys(evidence_link_ids))
        supporting_path_seed_ids = list(dict.fromkeys(supporting_path_seed_ids))
        probe_paper_ids = list(dict.fromkeys(probe_paper_ids))

        # Build evidence summary
        parts = []
        if supporting_paper_ids:
            parts.append(f"Supported by {len(supporting_paper_ids)} papers")
        if supporting_entity_ids:
            parts.append(f"Linked to {len(supporting_entity_ids)} KG entities")
        if supporting_path_seed_ids:
            parts.append(f"Derived from {len(supporting_path_seed_ids)} path seeds")

        return CandidateGraphEvidence(
            candidateId=candidate.id,
            supportingPaperIds=supporting_paper_ids,
            supportingClaimIds=supporting_claim_ids,
            supportingEntityIds=supporting_entity_ids,
            supportingPathSeedIds=supporting_path_seed_ids,
            evidenceLinkIds=evidence_link_ids,
            probePaperIds=probe_paper_ids,
            reasoningTrace=reasoning_trace,
            evidenceSummary=". ".join(parts) if parts else "No direct dual-graph evidence found",
        )

    def _build_ranking_literature_context(
        self,
        structured_papers: List[StructuredPaper],
        reasoning_kg: ReasoningKG | None,
        path_seeds: List[ReasoningPathSeed],
        limit: int = 8,
    ) -> str:
        """Build literature context string for LLM ranking analysis."""
        lines = []
        for i, sp in enumerate(structured_papers[:limit]):
            title = sp.title or "(untitled)"
            year = sp.year or "N/A"
            claims_str = ". ".join(
                c.text[:120] for c in (sp.claims or [])[:2]
            ) or "N/A"
            lines.append(
                f"[{i+1}] {title} ({year})\n"
                f"    Claims: {claims_str}"
            )

        # Add key gaps / path seeds summary
        if path_seeds:
            seed_summaries = []
            for ps in path_seeds[:3]:
                seed_summaries.append(
                    f"  - Path: {ps.templateType}, rationale: {ps.rationale[:150]}"
                )
            if seed_summaries:
                lines.append("\nReasoning Path Seeds:")
                lines.extend(seed_summaries)

        return "\n\n".join(lines) if lines else "(No literature available)"

    def _llm_analyze_candidate(
        self,
        candidate: IdeaCandidate,
        seed_query: str,
        paper_type: str,
        domain: str,
        literature_context: str,
        provider_name: str,
        model: str,
    ) -> tuple[PriorWorkComparison, IdeaCritique]:
        """Run LLM analysis for PriorWorkComparison + IdeaCritique in one call."""
        user_prompt = RANK_CANDIDATE_ANALYSIS_USER.format(
            seed_query=seed_query,
            paper_type=paper_type,
            domain=domain,
            title=candidate.title,
            problem=candidate.problem,
            key_insight=candidate.keyInsight,
            approach=candidate.draftPlan.methodology if candidate.draftPlan else "Not specified",
            literature_context=literature_context,
        )

        messages = [
            ChatMessage(role="system", content=RANK_CANDIDATE_ANALYSIS_SYSTEM),
            ChatMessage(role="user", content=user_prompt),
        ]

        client = get_provider_client(provider_name)
        response = client.chat(messages, model=model, max_tokens=1200)

        # Parse JSON response
        try:
            text = response.text.strip()
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0]
            elif "```" in text:
                text = text.split("```")[1].split("```")[0]
            data = json.loads(text)
        except (json.JSONDecodeError, AttributeError, IndexError):
            # Try regex fallback
            json_match = re.search(r'\{[\s\S]*\}', response.text)
            if json_match:
                try:
                    data = json.loads(json_match.group())
                except json.JSONDecodeError:
                    return self._fallback_analysis(candidate)
            else:
                return self._fallback_analysis(candidate)

        prior_work = PriorWorkComparison(
            candidateId=candidate.id,
            comparedPaperIds=data.get("comparedPaperIds", []),
            differences=data.get("differences", []),
            advantages=data.get("advantages", []),
            risks=data.get("risks", []),
            overallAssessment=data.get("overallAssessment", ""),
            comparisonConfidence=float(data.get("comparisonConfidence", 0.7)),
        )

        critique = IdeaCritique(
            candidateId=candidate.id,
            strengths=data.get("strengths", []),
            weaknesses=data.get("weaknesses", []),
            assumptions=data.get("assumptions", []),
            failureModes=data.get("failureModes", []),
            suggestedImprovements=data.get("suggestedImprovements", []),
            overallCritique=data.get("overallCritique", ""),
            critiqueConfidence=float(data.get("critiqueConfidence", 0.7)),
        )

        return prior_work, critique

    def _fallback_analysis(
        self, candidate: IdeaCandidate
    ) -> tuple[PriorWorkComparison, IdeaCritique]:
        """Generate fallback analysis when LLM fails."""
        prior_work = PriorWorkComparison(
            candidateId=candidate.id,
            overallAssessment="LLM analysis unavailable — scored heuristically.",
            comparisonConfidence=0.3,
        )
        critique = IdeaCritique(
            candidateId=candidate.id,
            strengths=[f"Addresses {candidate.problem[:80]}..."],
            weaknesses=["Not assessed by LLM"],
            overallCritique="LLM critique unavailable.",
            critiqueConfidence=0.3,
        )
        return prior_work, critique
    
    def _step_finalize(self, session: IdeaSession) -> tuple:
        """Finalize the session."""
        candidates = self.get_candidates(session.id)
        literature = self.get_literature(session.id)
        
        inputs = {}
        outputs = {
            "totalCandidates": len(candidates),
            "totalLiterature": len(literature),
            "topCandidate": candidates[0].title if candidates else None,
        }
        
        return inputs, outputs, []


# Global service instance
_service: Optional[IdeaGenerationService] = None


def get_idea_service() -> IdeaGenerationService:
    global _service
    if _service is None:
        _service = IdeaGenerationService()
    return _service


# =============================================================================
# Step 6 Prompt Templates: Candidate Analysis (PriorWorkComparison + IdeaCritique)
# =============================================================================

RANK_CANDIDATE_ANALYSIS_SYSTEM = """You are a research reviewer evaluating a candidate research idea against existing literature.

Your task is to:
1. Compare this idea with the provided literature context — identify key differences and advantages
2. Critique the idea — identify strengths, weaknesses, assumptions, and failure modes
3. Suggest concrete improvements

Be specific and evidence-based. Reference paper indices from the literature context when relevant.
Do NOT fabricate paper IDs or evidence links — only reference what is explicitly provided.

Respond ONLY with valid JSON in this exact format:
{
  "comparedPaperIds": ["paper index references from context"],
  "differences": ["how this idea differs from prior work — be specific"],
  "advantages": ["advantages over existing approaches"],
  "risks": ["risks relative to established methods"],
  "overallAssessment": "Brief overall comparison assessment",
  "comparisonConfidence": 0.85,
  "strengths": ["key strengths of the idea"],
  "weaknesses": ["identified weaknesses or gaps"],
  "assumptions": ["implicit or explicit assumptions the idea makes"],
  "failureModes": ["ways the idea could fail or be disproven"],
  "suggestedImprovements": ["concrete suggestions for strengthening the idea"],
  "overallCritique": "Brief overall critique summary",
  "critiqueConfidence": 0.85
}"""

RANK_CANDIDATE_ANALYSIS_USER = """Evaluate this candidate research idea:

**Research Domain:** {domain}
**Seed Topic:** {seed_query}
**Paper Type:** {paper_type}

**Idea Title:** {title}

**Problem Statement:** {problem}

**Key Insight:** {key_insight}

**Proposed Approach:** {approach}

**Existing Literature Context:**
{literature_context}

Provide a thorough comparison with existing work and a structured critique of this idea.
Be specific — reference the paper indices from the literature context in your comparison."""
