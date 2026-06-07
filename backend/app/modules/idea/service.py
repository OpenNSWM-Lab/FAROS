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
        bfts_config = BFTSConfig(searchBudget=search_budget)

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
                citationCount=result.citation_count,
                abstract=result.abstract or "",
                source=result.source,
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
        num_select = min(15, max(3, len(raw_papers) // 3))
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
    
    def _step_idea_brainstorm(self, session: IdeaSession) -> tuple:
        """Generate candidate ideas using LLM with structured prompts."""
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
        """Rank candidates using discriminative multi-criteria scoring."""
        seed = session.config.seedQuery
        paper_type = session.config.paperType
        domain = session.config.domain or "general"
        candidates = self.get_candidates(session.id)
        
        if not candidates:
            return {"candidateCount": 0}, {"rankings": [], "error": "No candidates to rank"}, []
        
        # Use the ranking service for discriminative scoring
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
            # Fallback: use existing scores
            updated_candidates = candidates
            ranking_results = []
        
        # Build rankings output with full criteria breakdown
        rankings = []
        for rank_idx, candidate in enumerate(sorted(updated_candidates, key=lambda c: c.overallScore, reverse=True), 1):
            ranking_entry = {
                "id": candidate.id,
                "title": candidate.title,
                "totalScore": round(candidate.overallScore, 2),
                "rank": rank_idx,
                "breakdown": candidate.scoreBreakdown,
                "overallRationale": getattr(candidate, 'overallRationale', ''),
                "scoringConfidence": getattr(candidate, 'scoringConfidence', 0.5),
                "scoringMethod": getattr(candidate, 'scoringMethod', 'heuristic'),
            }
            rankings.append(ranking_entry)
        
        # Calculate variance for diagnostics
        scores = [r["totalScore"] for r in rankings]
        variance = 0.0
        if len(scores) > 1:
            mean = sum(scores) / len(scores)
            variance = sum((s - mean) ** 2 for s in scores) / len(scores)
        
        inputs = {"candidateCount": len(candidates)}
        outputs = {
            "rankings": rankings,
            "scoreVariance": round(variance, 3),
            "minScore": round(min(scores), 2) if scores else 0,
            "maxScore": round(max(scores), 2) if scores else 0,
        }
        
        return inputs, outputs, []
    
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
