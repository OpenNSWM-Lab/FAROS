"""
Pipeline Runner - Executes the code generation pipeline.

Orchestrates all pipeline steps with tracing and error handling.
"""

import json
import uuid
import logging
import re
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple

from app.llm.provider_client import get_provider_client, ChatMessage, ProviderError
from app.code.context.repo_scanner import RepoScanner
from app.code.context.chunker import CodeChunker
from app.code.context.retriever import CodeRetriever
from app.code.context.context_pack import ContextPacker
from app.code.eval.static_eval import StaticEvaluator
from app.code.eval.scoring import EvalScorer

from .code_session_model import (
    CodeSession, CodeSessionConfig, CodeSessionStatus,
    CodeCandidate, CandidateScores, TraceStep, PipelineStep
)
from . import prompts

logger = logging.getLogger(__name__)


class PipelineRunner:
    """
    Runs the code generation pipeline.
    
    Steps:
    1. Search - Retrieve relevant context
    2. Summarize - Analyze repository
    3. Gap Analysis - Identify what needs to change
    4. Candidate Generation - Generate N solutions
    5. Ranking - Score candidates
    6. Select - Pick best candidate
    """
    
    def __init__(self, session: CodeSession, storage):
        """
        Initialize runner.
        
        Args:
            session: CodeSession to run
            storage: Storage for persisting session
        """
        self.session = session
        self.storage = storage
        self.config = session.config
        
        # Initialize components
        self.scanner = RepoScanner()
        self.chunker = CodeChunker()
        self.packer = ContextPacker(token_budget=6000)
        self.static_eval = StaticEvaluator()
        self.scorer = EvalScorer()
        
        # State
        self.context_chunks = []
        self.summary = None
        self.gap_analysis = None
        self.candidates: List[CodeCandidate] = []
    
    def _add_trace(
        self,
        step: str,
        status: str,
        inputs: Optional[Dict] = None,
        outputs: Optional[Dict] = None,
        error: Optional[str] = None,
        duration_ms: Optional[int] = None,
    ):
        """Add a trace step."""
        trace = TraceStep(
            step=step,
            status=status,
            timestamp=datetime.utcnow().isoformat(),
            durationMs=duration_ms,
            inputs=inputs,
            outputs=outputs,
            error=error,
        )
        self.session.trace.append(trace)
        self._save_session()
    
    def _save_session(self):
        """Save session to storage."""
        self.storage.save(self.session.id, self.session.to_dict())
    
    def _call_llm(self, prompt: str, max_tokens: int = 2000) -> str:
        """Call LLM with error handling."""
        try:
            client = get_provider_client(self.config.providerName)
            messages = [
                ChatMessage(role="system", content=prompts.SYSTEM_PROMPT),
                ChatMessage(role="user", content=prompt),
            ]
            response = client.chat(
                messages,
                model=self.config.model,
                max_tokens=max_tokens,
                temperature=0.7,
            )
            return response.text
        except ProviderError as e:
            logger.error(f"LLM call failed: {e}")
            raise
    
    def _parse_json_response(self, text: str) -> Dict[str, Any]:
        """Parse JSON from LLM response."""
        # Try to extract JSON from markdown code blocks
        json_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
        if json_match:
            text = json_match.group(1)
        
        # Try to find JSON object
        try:
            # Find first { and last }
            start = text.find('{')
            end = text.rfind('}')
            if start != -1 and end != -1:
                json_str = text[start:end+1]
                return json.loads(json_str)
        except json.JSONDecodeError:
            pass
        
        # Raise instead of silently returning empty dict so callers
        # can distinguish "no valid JSON" from "empty response" and
        # handle it as a pipeline step failure.
        raise ValueError(f"Failed to parse JSON from LLM response: {text[:200]}")
    
    def _step_search(self) -> Tuple[Dict, Dict]:
        """Step 1: Search and retrieve context."""
        start = datetime.utcnow()
        self._add_trace(PipelineStep.SEARCH.value, "started")
        
        try:
            # Scan repository
            scan_result = self.scanner.scan(self.config.repoPath)
            
            # Chunk files
            all_chunks = []
            for file_info in scan_result.files[:50]:  # Limit files
                try:
                    with open(file_info.absolute_path, 'r', encoding='utf-8', errors='ignore') as f:
                        content = f.read()
                    chunks = self.chunker.chunk_file(file_info.path, content, file_info.language)
                    all_chunks.extend(chunks)
                except Exception as e:
                    logger.warning(f"Failed to chunk {file_info.path}: {e}")
            
            self.context_chunks = all_chunks
            
            # Search for relevant chunks
            if all_chunks:
                retriever = CodeRetriever(all_chunks)
                results = retriever.search(self.config.goal, top_k=15)
                context_pack = self.packer.pack_search_results(results)
            else:
                context_pack = None
            
            duration = int((datetime.utcnow() - start).total_seconds() * 1000)
            
            outputs = {
                "file_count": scan_result.file_count,
                "chunk_count": len(all_chunks),
                "relevant_chunks": len(results) if all_chunks else 0,
            }
            
            self._add_trace(
                PipelineStep.SEARCH.value, "completed",
                outputs=outputs, duration_ms=duration
            )
            
            return {"context_pack": context_pack}, outputs
            
        except Exception as e:
            self._add_trace(PipelineStep.SEARCH.value, "failed", error=str(e))
            raise
    
    def _step_summarize(self, context_pack) -> Tuple[Dict, Dict]:
        """Step 2: Summarize repository."""
        start = datetime.utcnow()
        self._add_trace(PipelineStep.SUMMARIZE.value, "started")
        
        try:
            context_str = context_pack.to_prompt_string() if context_pack else "No context available"
            
            prompt = prompts.SUMMARIZE_PROMPT.format(
                context=context_str[:8000],
                goal=self.config.goal,
                constraints=self.config.constraints or "None specified",
            )
            
            response = self._call_llm(prompt)
            self.summary = self._parse_json_response(response)
            
            if not self.summary:
                self.summary = {"summary": response[:500], "relevant_files": [], "key_components": []}
            
            duration = int((datetime.utcnow() - start).total_seconds() * 1000)
            
            self._add_trace(
                PipelineStep.SUMMARIZE.value, "completed",
                outputs={"summary_length": len(str(self.summary))},
                duration_ms=duration
            )
            
            return {"summary": self.summary}, {"summary": self.summary.get("summary", "")[:200]}
            
        except Exception as e:
            self._add_trace(PipelineStep.SUMMARIZE.value, "failed", error=str(e))
            raise
    
    def _step_gap_analysis(self) -> Tuple[Dict, Dict]:
        """Step 3: Gap analysis."""
        start = datetime.utcnow()
        self._add_trace(PipelineStep.GAP_ANALYSIS.value, "started")
        
        try:
            prompt = prompts.GAP_ANALYSIS_PROMPT.format(
                summary=json.dumps(self.summary, indent=2),
                goal=self.config.goal,
                constraints=self.config.constraints or "None specified",
            )
            
            response = self._call_llm(prompt)
            self.gap_analysis = self._parse_json_response(response)
            
            if not self.gap_analysis:
                self.gap_analysis = {
                    "gaps": [{"description": "Implement the requested feature", "priority": "high"}],
                    "approach_options": [{"name": "Direct implementation", "description": response[:300]}],
                    "recommended_approach": "Direct implementation"
                }
            
            duration = int((datetime.utcnow() - start).total_seconds() * 1000)
            
            self._add_trace(
                PipelineStep.GAP_ANALYSIS.value, "completed",
                outputs={"gap_count": len(self.gap_analysis.get("gaps", []))},
                duration_ms=duration
            )
            
            return {"gap_analysis": self.gap_analysis}, {"gaps": len(self.gap_analysis.get("gaps", []))}
            
        except Exception as e:
            self._add_trace(PipelineStep.GAP_ANALYSIS.value, "failed", error=str(e))
            raise
    
    def _step_candidate_generation(self, context_pack) -> Tuple[Dict, Dict]:
        """Step 4: Generate candidates."""
        start = datetime.utcnow()
        self._add_trace(PipelineStep.CANDIDATE_GEN.value, "started")
        
        try:
            context_str = context_pack.to_prompt_string() if context_pack else ""
            approaches = self.gap_analysis.get("approach_options", [])
            
            # Generate multiple candidates with different approaches
            for i in range(min(self.config.maxCandidates, max(1, len(approaches)))):
                approach = approaches[i] if i < len(approaches) else {"name": f"Approach {i+1}", "description": "Standard implementation"}
                
                prompt = prompts.CANDIDATE_GEN_PROMPT.format(
                    context=context_str[:6000],
                    gap_analysis=json.dumps(self.gap_analysis, indent=2)[:2000],
                    goal=self.config.goal,
                    approach=json.dumps(approach),
                )
                
                response = self._call_llm(prompt, max_tokens=3000)
                candidate_data = self._parse_json_response(response)
                
                if not candidate_data.get("patch"):
                    # Try to extract patch from response
                    patch_match = re.search(r'```diff\s*([\s\S]*?)```', response)
                    if patch_match:
                        candidate_data["patch"] = patch_match.group(1)
                    else:
                        candidate_data["patch"] = f"# Generated code for: {self.config.goal}\n# Approach: {approach.get('name', 'Unknown')}\n"
                
                candidate = CodeCandidate(
                    id=f"cand_{uuid.uuid4().hex[:12]}",
                    sessionId=self.session.id,
                    title=candidate_data.get("title", f"Solution {i+1}"),
                    approach=candidate_data.get("approach", approach.get("description", "")),
                    patch=candidate_data.get("patch", ""),
                    rationale=candidate_data.get("rationale", ""),
                    createdAt=datetime.utcnow().isoformat(),
                )
                
                self.candidates.append(candidate)
                self.session.candidateIds.append(candidate.id)
            
            duration = int((datetime.utcnow() - start).total_seconds() * 1000)
            
            self._add_trace(
                PipelineStep.CANDIDATE_GEN.value, "completed",
                outputs={"candidate_count": len(self.candidates)},
                duration_ms=duration
            )
            
            return {"candidates": self.candidates}, {"count": len(self.candidates)}
            
        except Exception as e:
            self._add_trace(PipelineStep.CANDIDATE_GEN.value, "failed", error=str(e))
            raise
    
    def _step_ranking(self) -> Tuple[Dict, Dict]:
        """Step 5: Rank candidates."""
        start = datetime.utcnow()
        self._add_trace(PipelineStep.RANKING.value, "started")
        
        try:
            for candidate in self.candidates:
                prompt = prompts.RANKING_PROMPT.format(
                    title=candidate.title,
                    approach=candidate.approach,
                    patch=candidate.patch[:3000],
                    goal=self.config.goal,
                )
                
                response = self._call_llm(prompt, max_tokens=1000)
                ranking_data = self._parse_json_response(response)
                
                scores = ranking_data.get("scores", {})
                candidate.scores = CandidateScores(
                    correctness=float(scores.get("correctness", 5.0)),
                    completeness=float(scores.get("completeness", 5.0)),
                    efficiency=float(scores.get("efficiency", 5.0)),
                    readability=float(scores.get("readability", 5.0)),
                    safety=float(scores.get("safety", 5.0)),
                )
                
                # Calculate overall score with weights
                candidate.overallScore = (
                    candidate.scores.correctness * 0.30 +
                    candidate.scores.completeness * 0.25 +
                    candidate.scores.efficiency * 0.15 +
                    candidate.scores.readability * 0.15 +
                    candidate.scores.safety * 0.15
                )
                
                # Add some variance based on static eval
                static_result = self.static_eval.evaluate_diff(candidate.patch)
                if static_result.error_count > 0:
                    candidate.overallScore *= 0.8
                if static_result.risks:
                    candidate.overallScore *= 0.95
            
            # Sort and assign ranks
            self.candidates.sort(key=lambda c: c.overallScore, reverse=True)
            for i, candidate in enumerate(self.candidates):
                candidate.rank = i + 1
            
            duration = int((datetime.utcnow() - start).total_seconds() * 1000)
            
            scores_summary = [
                {"id": c.id, "title": c.title, "score": round(c.overallScore, 2), "rank": c.rank}
                for c in self.candidates
            ]
            
            self._add_trace(
                PipelineStep.RANKING.value, "completed",
                outputs={"rankings": scores_summary},
                duration_ms=duration
            )
            
            return {"ranked_candidates": self.candidates}, {"rankings": scores_summary}
            
        except Exception as e:
            self._add_trace(PipelineStep.RANKING.value, "failed", error=str(e))
            raise
    
    def run(self) -> CodeSession:
        """
        Run the full pipeline.
        
        Returns:
            Updated CodeSession
        """
        try:
            # Update session status
            self.session.status = CodeSessionStatus.RUNNING
            self.session.startedAt = datetime.utcnow().isoformat()
            self._save_session()
            
            # Step 1: Search
            self.session.currentStep = PipelineStep.SEARCH.value
            self._save_session()
            search_result, _ = self._step_search()
            context_pack = search_result.get("context_pack")
            
            # Step 2: Summarize
            self.session.currentStep = PipelineStep.SUMMARIZE.value
            self._save_session()
            self._step_summarize(context_pack)
            
            # Step 3: Gap Analysis
            self.session.currentStep = PipelineStep.GAP_ANALYSIS.value
            self._save_session()
            self._step_gap_analysis()
            
            # Step 4: Candidate Generation
            self.session.currentStep = PipelineStep.CANDIDATE_GEN.value
            self._save_session()
            self._step_candidate_generation(context_pack)
            
            # Step 5: Ranking
            self.session.currentStep = PipelineStep.RANKING.value
            self._save_session()
            self._step_ranking()
            
            # Complete
            self.session.status = CodeSessionStatus.COMPLETED
            self.session.endedAt = datetime.utcnow().isoformat()
            self.session.currentStep = None
            
            if self.session.startedAt and self.session.endedAt:
                start = datetime.fromisoformat(self.session.startedAt)
                end = datetime.fromisoformat(self.session.endedAt)
                self.session.duration = int((end - start).total_seconds())
            
            self.session.summary = f"Generated {len(self.candidates)} candidates. Top candidate: {self.candidates[0].title if self.candidates else 'None'}"
            
            self._save_session()
            
            # Save candidates
            for candidate in self.candidates:
                self.storage.save_candidate(candidate.id, candidate.to_dict())
            
            return self.session
            
        except Exception as e:
            logger.error(f"Pipeline failed: {e}")
            self.session.status = CodeSessionStatus.FAILED
            self.session.errorMessage = str(e)
            self.session.endedAt = datetime.utcnow().isoformat()
            self._save_session()
            raise
    
    def get_candidates(self) -> List[CodeCandidate]:
        """Get generated candidates."""
        return self.candidates
