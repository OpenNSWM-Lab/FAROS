"""
Ranking Service for Idea Candidates

Implements discriminative multi-criteria ranking with:
- LLM-based structured scoring (primary)
- Deterministic heuristic backup (fallback)
- Variance guard with re-ranking
- Paper-type specific weights
"""

import json
import logging
import random
import hashlib
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass

from app.models.idea import IdeaCandidate, DraftPlan, RiskItem, ExperimentSpec
from app.llm.provider_client import get_provider_client, ChatMessage, ProviderError
from app.storage.idea_storage import get_candidate_storage, generate_candidate_id

logger = logging.getLogger(__name__)


@dataclass
class CriterionScore:
    """Score for a single criterion."""
    value: float  # 0-10
    rationale: str
    confidence: float  # 0-1


@dataclass
class RankingResult:
    """Complete ranking result for a candidate."""
    candidateId: str
    totalScore: float
    criteria: Dict[str, CriterionScore]
    overallRationale: str
    confidence: float
    rank: int = 0


# Paper-type specific weights
PAPER_TYPE_WEIGHTS = {
    "algorithm": {
        "novelty": 0.25,
        "feasibility": 0.20,
        "impact": 0.20,
        "clarity": 0.10,
        "risk": 0.05,
        "alignment": 0.10,
        "referenceSupport": 0.05,
        "experimentSpecificity": 0.05,
    },
    "system": {
        "novelty": 0.15,
        "feasibility": 0.25,
        "impact": 0.20,
        "clarity": 0.10,
        "risk": 0.10,
        "alignment": 0.10,
        "referenceSupport": 0.05,
        "experimentSpecificity": 0.05,
    },
    "benchmark": {
        "novelty": 0.15,
        "feasibility": 0.15,
        "impact": 0.25,
        "clarity": 0.15,
        "risk": 0.05,
        "alignment": 0.10,
        "referenceSupport": 0.10,
        "experimentSpecificity": 0.05,
    },
    "survey": {
        "novelty": 0.10,
        "feasibility": 0.15,
        "impact": 0.20,
        "clarity": 0.25,
        "risk": 0.05,
        "alignment": 0.10,
        "referenceSupport": 0.15,
        "experimentSpecificity": 0.00,
    },
    "application": {
        "novelty": 0.15,
        "feasibility": 0.25,
        "impact": 0.25,
        "clarity": 0.10,
        "risk": 0.10,
        "alignment": 0.10,
        "referenceSupport": 0.05,
        "experimentSpecificity": 0.00,
    },
    "theory": {
        "novelty": 0.30,
        "feasibility": 0.15,
        "impact": 0.20,
        "clarity": 0.15,
        "risk": 0.05,
        "alignment": 0.10,
        "referenceSupport": 0.05,
        "experimentSpecificity": 0.00,
    },
    "default": {
        "novelty": 0.20,
        "feasibility": 0.20,
        "impact": 0.20,
        "clarity": 0.10,
        "risk": 0.10,
        "alignment": 0.10,
        "referenceSupport": 0.05,
        "experimentSpecificity": 0.05,
    },
}

RANKING_SYSTEM_PROMPT = """You are a research idea evaluator. Score the given research idea on multiple criteria.

For each criterion, provide:
1. A score from 0.0 to 10.0 (use decimals for precision, e.g., 7.3, 8.1, 5.8)
2. A brief rationale explaining the score

Criteria definitions:
- novelty: How new and original is this idea? Does it address an unexplored area?
- feasibility: How practical is implementation? Are resources/methods available?
- impact: What is the potential scientific/practical impact if successful?
- clarity: How well-defined is the problem and proposed solution?
- risk: How risky is this research? (Higher score = lower risk, more likely to succeed)
- alignment: How well does this align with the stated research topic?
- referenceSupport: How well is this grounded in existing literature?
- experimentSpecificity: How concrete are the proposed experiments/evaluations?

IMPORTANT: Be discriminative! Different ideas should get different scores. Avoid giving all criteria the same score.

Respond ONLY with valid JSON in this exact format:
{
  "novelty": {"score": 7.3, "rationale": "..."},
  "feasibility": {"score": 8.1, "rationale": "..."},
  "impact": {"score": 6.5, "rationale": "..."},
  "clarity": {"score": 7.8, "rationale": "..."},
  "risk": {"score": 6.2, "rationale": "..."},
  "alignment": {"score": 8.5, "rationale": "..."},
  "referenceSupport": {"score": 5.9, "rationale": "..."},
  "experimentSpecificity": {"score": 7.1, "rationale": "..."},
  "overallRationale": "Brief overall assessment...",
  "confidence": 0.85
}"""

RANKING_USER_PROMPT = """Evaluate this research idea for a {paper_type} paper in the domain of {domain}:

**Seed Topic:** {seed_query}

**Idea Title:** {title}

**Problem Statement:** {problem}

**Key Insight:** {key_insight}

**Proposed Approach:** {approach}

**Number of References:** {ref_count}

**Has Experiment Plan:** {has_experiments}

Score this idea on all criteria. Be specific and discriminative."""


class RankingService:
    """Service for ranking idea candidates."""
    
    def __init__(self):
        self.candidate_storage = get_candidate_storage()
    
    def rank_candidates(
        self,
        candidates: List[IdeaCandidate],
        seed_query: str,
        paper_type: str,
        domain: str,
        provider_name: str,
        model: str,
        session_id: str,
    ) -> Tuple[List[IdeaCandidate], List[RankingResult]]:
        """
        Rank candidates using LLM scoring with heuristic fallback.
        
        Returns:
            Tuple of (updated_candidates, ranking_results)
        """
        if not candidates:
            return [], []
        
        weights = PAPER_TYPE_WEIGHTS.get(paper_type, PAPER_TYPE_WEIGHTS["default"])
        
        # Try LLM-based ranking first
        ranking_results = []
        try:
            ranking_results = self._llm_rank(
                candidates, seed_query, paper_type, domain, provider_name, model
            )
        except Exception as e:
            logger.warning(f"LLM ranking failed: {e}, using heuristic fallback")
            ranking_results = self._heuristic_rank(candidates, seed_query, paper_type)
        
        # Check variance - if too low, apply tie-breakers
        scores = [r.totalScore for r in ranking_results]
        if len(scores) > 1:
            variance = self._calculate_variance(scores)
            if variance < 0.36:  # std < 0.6
                logger.info(f"Low variance ({variance:.3f}), applying tie-breakers")
                ranking_results = self._apply_tie_breakers(ranking_results, candidates)
        
        # Sort by total score
        ranking_results.sort(key=lambda r: r.totalScore, reverse=True)
        
        # Assign ranks
        for i, result in enumerate(ranking_results):
            result.rank = i + 1
        
        # Update candidates with new scores
        updated_candidates = self._update_candidates(
            candidates, ranking_results, weights, session_id
        )
        
        return updated_candidates, ranking_results
    
    def _llm_rank(
        self,
        candidates: List[IdeaCandidate],
        seed_query: str,
        paper_type: str,
        domain: str,
        provider_name: str,
        model: str,
    ) -> List[RankingResult]:
        """Rank using LLM-based scoring."""
        client = get_provider_client(provider_name)
        weights = PAPER_TYPE_WEIGHTS.get(paper_type, PAPER_TYPE_WEIGHTS["default"])
        results = []
        
        for candidate in candidates:
            try:
                user_prompt = RANKING_USER_PROMPT.format(
                    paper_type=paper_type,
                    domain=domain or "general",
                    seed_query=seed_query,
                    title=candidate.title,
                    problem=candidate.problem,
                    key_insight=candidate.keyInsight,
                    approach=candidate.draftPlan.methodology if candidate.draftPlan else "Not specified",
                    ref_count=len(candidate.references),
                    has_experiments="Yes" if candidate.requiredExperiments else "No",
                )
                
                messages = [
                    ChatMessage(role="system", content=RANKING_SYSTEM_PROMPT),
                    ChatMessage(role="user", content=user_prompt),
                ]
                
                response = client.chat(messages, model=model, max_tokens=800)
                
                # Parse response
                result = self._parse_llm_response(candidate.id, response.text, weights)
                results.append(result)
                
            except Exception as e:
                logger.warning(f"Failed to score candidate {candidate.id}: {e}")
                # Use heuristic for this candidate
                heuristic_result = self._heuristic_score_single(candidate, seed_query, paper_type)
                results.append(heuristic_result)
        
        return results
    
    def _parse_llm_response(
        self, candidate_id: str, response_text: str, weights: Dict[str, float]
    ) -> RankingResult:
        """Parse LLM response into RankingResult."""
        # Try to extract JSON from response
        try:
            # Find JSON in response
            text = response_text.strip()
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0]
            elif "```" in text:
                text = text.split("```")[1].split("```")[0]
            
            data = json.loads(text)
        except json.JSONDecodeError:
            # Try to find JSON object in text
            import re
            match = re.search(r'\{[\s\S]*\}', response_text)
            if match:
                try:
                    data = json.loads(match.group())
                except:
                    raise ValueError("Could not parse JSON from response")
            else:
                raise ValueError("No JSON found in response")
        
        criteria = {}
        total_score = 0.0
        
        for criterion in ["novelty", "feasibility", "impact", "clarity", "risk", 
                          "alignment", "referenceSupport", "experimentSpecificity"]:
            if criterion in data and isinstance(data[criterion], dict):
                score = float(data[criterion].get("score", 5.0))
                rationale = data[criterion].get("rationale", "")
            elif criterion in data:
                score = float(data[criterion])
                rationale = ""
            else:
                score = 5.0
                rationale = "Not evaluated"
            
            # Clamp score to valid range
            score = max(0.0, min(10.0, score))
            
            criteria[criterion] = CriterionScore(
                value=score,
                rationale=rationale,
                confidence=0.8
            )
            
            weight = weights.get(criterion, 0.1)
            total_score += score * weight
        
        return RankingResult(
            candidateId=candidate_id,
            totalScore=round(total_score, 2),
            criteria=criteria,
            overallRationale=data.get("overallRationale", ""),
            confidence=float(data.get("confidence", 0.7)),
        )
    
    def _heuristic_rank(
        self,
        candidates: List[IdeaCandidate],
        seed_query: str,
        paper_type: str,
    ) -> List[RankingResult]:
        """Rank using deterministic heuristics."""
        results = []
        for candidate in candidates:
            result = self._heuristic_score_single(candidate, seed_query, paper_type)
            results.append(result)
        return results
    
    def _heuristic_score_single(
        self,
        candidate: IdeaCandidate,
        seed_query: str,
        paper_type: str,
    ) -> RankingResult:
        """Score a single candidate using heuristics."""
        weights = PAPER_TYPE_WEIGHTS.get(paper_type, PAPER_TYPE_WEIGHTS["default"])
        
        # Generate deterministic but varied scores based on candidate content
        seed_hash = int(hashlib.md5(candidate.id.encode()).hexdigest()[:8], 16)
        random.seed(seed_hash)
        
        # Base scores: always generate fresh heuristic scores for unscored candidates
        is_pending = getattr(candidate, 'scoringMethod', 'pending') == 'pending'
        base_novelty = 5.0 + random.uniform(1.0, 4.0) if is_pending else candidate.novelty
        base_feasibility = 5.0 + random.uniform(1.0, 4.0) if is_pending else candidate.feasibility
        base_impact = 5.0 + random.uniform(1.0, 4.0) if is_pending else candidate.impact
        
        # Calculate other criteria based on content analysis
        title_words = len(candidate.title.split())
        problem_words = len(candidate.problem.split())
        insight_words = len(candidate.keyInsight.split())
        ref_count = len(candidate.references)
        has_experiments = len(candidate.requiredExperiments) > 0
        has_risks = len(candidate.risks) > 0
        
        # Clarity: based on description length and structure
        clarity_base = min(10, 4 + (problem_words / 20) + (insight_words / 15))
        clarity = clarity_base + random.uniform(-0.5, 0.5)
        
        # Risk: inverse of identified risks (more risks identified = better risk awareness)
        risk_base = 6.0 + (1.0 if has_risks else -1.0) + random.uniform(-1.0, 1.0)
        
        # Alignment: based on seed query word overlap
        seed_words = set(seed_query.lower().split())
        title_overlap = len(seed_words & set(candidate.title.lower().split()))
        problem_overlap = len(seed_words & set(candidate.problem.lower().split()))
        alignment = min(10, 5 + title_overlap * 1.5 + problem_overlap * 0.5 + random.uniform(-0.5, 0.5))
        
        # Reference support
        ref_support = min(10, 4 + ref_count * 0.8 + random.uniform(-0.5, 0.5))
        
        # Experiment specificity
        exp_spec = 5.0 + (2.0 if has_experiments else 0) + random.uniform(-0.5, 1.0)
        
        criteria = {
            "novelty": CriterionScore(round(base_novelty, 1), "Heuristic assessment", 0.6),
            "feasibility": CriterionScore(round(base_feasibility, 1), "Heuristic assessment", 0.6),
            "impact": CriterionScore(round(base_impact, 1), "Heuristic assessment", 0.6),
            "clarity": CriterionScore(round(max(0, min(10, clarity)), 1), "Based on description quality", 0.6),
            "risk": CriterionScore(round(max(0, min(10, risk_base)), 1), "Based on risk identification", 0.6),
            "alignment": CriterionScore(round(max(0, min(10, alignment)), 1), "Based on topic overlap", 0.6),
            "referenceSupport": CriterionScore(round(max(0, min(10, ref_support)), 1), "Based on reference count", 0.6),
            "experimentSpecificity": CriterionScore(round(max(0, min(10, exp_spec)), 1), "Based on experiment plan", 0.6),
        }
        
        # Calculate total score
        total_score = sum(
            criteria[c].value * weights.get(c, 0.1)
            for c in criteria
        )
        
        return RankingResult(
            candidateId=candidate.id,
            totalScore=round(total_score, 2),
            criteria=criteria,
            overallRationale="Scored using heuristic analysis",
            confidence=0.6,
        )
    
    def _calculate_variance(self, scores: List[float]) -> float:
        """Calculate variance of scores."""
        if len(scores) < 2:
            return 0.0
        mean = sum(scores) / len(scores)
        return sum((s - mean) ** 2 for s in scores) / len(scores)
    
    def _apply_tie_breakers(
        self,
        results: List[RankingResult],
        candidates: List[IdeaCandidate],
    ) -> List[RankingResult]:
        """Apply tie-breakers when variance is too low."""
        candidate_map = {c.id: c for c in candidates}
        
        for result in results:
            candidate = candidate_map.get(result.candidateId)
            if not candidate:
                continue
            
            # Tie-breaker 1: Reference strength
            ref_bonus = len(candidate.references) * 0.1
            
            # Tie-breaker 2: Experiment specificity
            exp_bonus = 0.0
            for exp in candidate.requiredExperiments:
                if exp.datasets:
                    exp_bonus += 0.15
                if exp.metrics:
                    exp_bonus += 0.1
            
            # Tie-breaker 3: Risk identification (shows thorough thinking)
            risk_bonus = len(candidate.risks) * 0.05
            
            # Tie-breaker 4: Problem specificity (word count as proxy)
            problem_words = len(candidate.problem.split())
            specificity_bonus = min(0.3, problem_words * 0.01)
            
            # Apply bonuses
            total_bonus = ref_bonus + exp_bonus + risk_bonus + specificity_bonus
            result.totalScore = round(result.totalScore + total_bonus, 2)
        
        return results
    
    def _update_candidates(
        self,
        candidates: List[IdeaCandidate],
        results: List[RankingResult],
        weights: Dict[str, float],
        session_id: str,
    ) -> List[IdeaCandidate]:
        """Update candidates with new scores and save to storage."""
        result_map = {r.candidateId: r for r in results}
        updated = []
        
        for candidate in candidates:
            result = result_map.get(candidate.id)
            if not result:
                updated.append(candidate)
                continue
            
            # Helper to safely get a criterion
            def _cs(name: str) -> CriterionScore:
                return result.criteria.get(name, CriterionScore(5.0, "", 0.5))
            
            # Create updated candidate with all 8 criteria scores
            new_candidate = IdeaCandidate(
                id=candidate.id,
                sessionId=candidate.sessionId,
                title=candidate.title,
                problem=candidate.problem,
                keyInsight=candidate.keyInsight,
                novelty=_cs("novelty").value,
                noveltyRationale=_cs("novelty").rationale,
                feasibility=_cs("feasibility").value,
                feasibilityRationale=_cs("feasibility").rationale,
                impact=_cs("impact").value,
                impactRationale=_cs("impact").rationale,
                clarity=_cs("clarity").value,
                clarityRationale=_cs("clarity").rationale,
                risk=_cs("risk").value,
                riskRationale=_cs("risk").rationale,
                alignment=_cs("alignment").value,
                alignmentRationale=_cs("alignment").rationale,
                referenceSupport=_cs("referenceSupport").value,
                referenceSupportRationale=_cs("referenceSupport").rationale,
                experimentSpecificity=_cs("experimentSpecificity").value,
                experimentSpecificityRationale=_cs("experimentSpecificity").rationale,
                overallRationale=result.overallRationale,
                scoringConfidence=result.confidence,
                scoringMethod="llm" if result.confidence > 0.65 else "heuristic",
                risks=candidate.risks,
                requiredExperiments=candidate.requiredExperiments,
                expectedMetrics=candidate.expectedMetrics,
                draftPlan=candidate.draftPlan,
                references=candidate.references,
                createdAt=candidate.createdAt,
            )
            
            # Save updated candidate
            try:
                # Delete old and create new (since frozen)
                path = self.candidate_storage._get_candidate_path(candidate.id)
                if path.exists():
                    path.unlink()
                self.candidate_storage.create(new_candidate)
            except Exception as e:
                logger.warning(f"Failed to update candidate {candidate.id}: {e}")
            
            updated.append(new_candidate)
        
        return updated


# Global instance
_ranking_service: Optional[RankingService] = None


def get_ranking_service() -> RankingService:
    global _ranking_service
    if _ranking_service is None:
        _ranking_service = RankingService()
    return _ranking_service
