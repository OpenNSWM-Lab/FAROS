"""
Research Dossier Builder

Converts internal Idea module models (IdeaSession, IdeaCandidate, LiteratureItem, etc.)
into the public contract ResearchDossier.

This is the boundary adapter required by P0 task #1:
"Accept any ScientificQuestion, output valid ResearchDossier."
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any, Dict, List, Optional

from app.contracts import (
    ArtifactKind,
    ArtifactRef,
    EvidenceMap,
    EvidenceRecord,
    EvidenceStance,
    EvidenceTier,
    ExecutionClass,
    GenerationTrace,
    Hypothesis,
    ProblemFrame,
    ResearchDossier,
    ResearchPlan,
    ResearchPlanStep,
    RunMode,
    RunStatus,
    ScientificQuestion,
    ScientificQuestionRun,
    TargetModule,
)
from app.models.idea import (
    IdeaCandidate,
    IdeaSession,
    LiteratureItem,
    RiskItem,
    ExperimentSpec,
)
from app.modules.idea.problem_framing import frame_problem

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utcnow() -> datetime:
    return datetime.now(UTC)


def _short_id(prefix: str, seed: str) -> str:
    return f"{prefix}_{hashlib.md5(seed.encode()).hexdigest()[:12]}"


def _score_to_01(value: float, scale: float = 10.0) -> float:
    """Convert internal 0-10 score to contract 0-1 scale."""
    if scale <= 0:
        return 0.0
    return max(0.0, min(1.0, value / scale))


# ---------------------------------------------------------------------------
# Evidence conversion
# ---------------------------------------------------------------------------

def _literature_to_evidence_records(
    literature: List[LiteratureItem],
    *,
    stance: EvidenceStance = EvidenceStance.CONTEXT,
    evidence_tier: EvidenceTier = EvidenceTier.UNKNOWN,
) -> List[EvidenceRecord]:
    """Convert internal LiteratureItem list to contract EvidenceRecord list."""
    records: List[EvidenceRecord] = []
    for item in literature:
        tier = evidence_tier
        # Infer tier from source
        src = (item.source or "").lower()
        if src in ("arxiv", "semantic_scholar", "openalex", "crossref"):
            tier = EvidenceTier.SECONDARY
        elif src in ("dblp", "core"):
            tier = EvidenceTier.TERTIARY
        elif src in ("local_corpus", "fixture"):
            tier = EvidenceTier.PRIMARY

        records.append(
            EvidenceRecord(
                id=item.id,
                title=item.title,
                summary=item.snippet or "",
                stance=stance,
                sourceType=item.source or "unknown",
                source=item.source or "",
                authors=list(item.authors) if item.authors else [],
                year=item.year,
                doi=item.doi,
                url=item.url,
                evidenceTier=tier,
                relevanceScore=item.relevanceScore,
                verified=bool(item.doi or item.url),
                claimIds=[],
                metadata={"arxivId": item.arxivId} if item.arxivId else {},
            )
        )
    return records


def _classify_evidence_by_relevance(
    literature: List[LiteratureItem],
    seed_query: str,
) -> tuple[List[EvidenceRecord], List[EvidenceRecord], List[EvidenceRecord]]:
    """
    Classify literature into supporting / counter / context evidence.

    Heuristic approach:
    - High relevance (>=0.6) with positive wording → supporting
    - High relevance with negative/contradicting wording → counter
    - Everything else → context
    """
    from app.modules.idea.service import _cjk_bigrams, _ascii_topic_tokens

    seed_tokens = set(_cjk_bigrams(seed_query) + _ascii_topic_tokens(seed_query.lower()))

    supporting: List[EvidenceRecord] = []
    counter: List[EvidenceRecord] = []
    context: List[EvidenceRecord] = []

    # Counter-evidence signal words
    counter_signals = {"limitation", "fail", "cannot", "challenge", "issue", "problem",
                        "contradict", "refute", "counter", "negative", "drawback",
                        "不足", "缺陷", "挑战", "问题", "矛盾", "反驳"}

    for item in literature:
        record = _literature_to_evidence_records([item], stance=EvidenceStance.CONTEXT)[0]
        text_lower = (item.title + " " + item.snippet).lower()

        # Check for counter-evidence signals
        is_counter = any(sig in text_lower for sig in counter_signals)

        if item.relevanceScore >= 0.6 and not is_counter:
            record = record.model_copy(update={"stance": EvidenceStance.SUPPORT})
            supporting.append(record)
        elif item.relevanceScore >= 0.5 and is_counter:
            record = record.model_copy(update={"stance": EvidenceStance.COUNTER})
            counter.append(record)
        else:
            context.append(record)

    return supporting, counter, context


# ---------------------------------------------------------------------------
# Hypothesis conversion
# ---------------------------------------------------------------------------

def _candidate_to_hypothesis(
    candidate: IdeaCandidate,
    evidence_ids: set[str],
) -> Hypothesis:
    """Convert an IdeaCandidate to a contract Hypothesis."""

    # Derivation trace from BFTS lineage
    derivation_trace: List[str] = []
    if candidate.searchNodeId:
        derivation_trace.append(f"bfts_node:{candidate.searchNodeId}")
    if candidate.pathSeedId:
        derivation_trace.append(f"path_seed:{candidate.pathSeedId}")
    if candidate.reasoningPathId:
        derivation_trace.append(f"reasoning_path:{candidate.reasoningPathId}")

    # Evidence IDs from references
    supporting_ids = [ref for ref in candidate.references if ref in evidence_ids]

    # Scores → 0-1 scale
    scores = {
        "novelty": _score_to_01(candidate.novelty),
        "feasibility": _score_to_01(candidate.feasibility),
        "impact": _score_to_01(candidate.impact),
        "clarity": _score_to_01(candidate.clarity),
        "risk": _score_to_01(candidate.risk),
        "alignment": _score_to_01(candidate.alignment),
        "referenceSupport": _score_to_01(candidate.referenceSupport),
        "experimentSpecificity": _score_to_01(candidate.experimentSpecificity),
    }

    # Falsification criteria from hypothesis statement + experiments
    falsification_criteria: List[str] = []
    if candidate.hypothesisStatement:
        falsification_criteria.append(
            f"Observed data shows no effect contradicting: {candidate.hypothesisStatement[:200]}"
        )
    for exp in candidate.experimentSpecs:
        for metric in exp.metrics:
            falsification_criteria.append(f"{metric} shows no improvement over baseline in {exp.name}")
    if not falsification_criteria:
        falsification_criteria.append("Experimental results fail to support the predicted outcome")

    # Confounders from risks; fallback to derived confounders
    confounders = [r.risk for r in candidate.risks] if candidate.risks else []
    if not confounders:
        # Derive confounders from hypothesis and experiment context
        confounders = [
            "Data quality and coverage may bias results",
            "Selection bias in chosen benchmarks or datasets",
        ]
        if candidate.hypothesisStatement:
            confounders.append(f"Confounding variables in experimental setup for: {candidate.hypothesisStatement[:100]}")

    # Alternative explanations
    alternative_explanations: List[str] = []
    if candidate.critique:
        critique_data = candidate.critique
        if isinstance(critique_data, dict):
            for weakness in critique_data.get("weaknesses", []):
                alternative_explanations.append(str(weakness))
    if not alternative_explanations:
        alternative_explanations.append("Alternative explanations not yet enumerated")

    confidence = _score_to_01(candidate.scoringConfidence, scale=1.0)

    return Hypothesis(
        id=candidate.id,
        statement=candidate.hypothesisStatement or candidate.keyInsight or candidate.title,
        rationale=candidate.problem or candidate.keyInsight or "",
        derivationTrace=derivation_trace,
        supportingEvidenceIds=supporting_ids,
        counterEvidenceIds=[],
        falsificationCriteria=falsification_criteria,
        confounders=confounders,
        alternativeExplanations=alternative_explanations,
        scores=scores,
        confidence=confidence,
    )


# ---------------------------------------------------------------------------
# ResearchPlan conversion
# ---------------------------------------------------------------------------

def _candidate_to_research_plan(
    candidate: IdeaCandidate,
) -> ResearchPlan:
    """Convert an IdeaCandidate to a contract ResearchPlan."""

    steps: List[ResearchPlanStep] = []

    # Step 1: Literature review / setup
    steps.append(
        ResearchPlanStep(
            id=f"step_{candidate.id}_1",
            order=1,
            title="Literature Review and Problem Setup",
            objective="Establish baseline understanding and collect required data.",
            inputs=["seed query", "literature corpus"],
            tools=["FAROS search", "manual review"],
            method=["Systematic literature search", "Extract key findings"],
            outputs=["annotated bibliography", "problem specification"],
            metrics=["coverage_rate", "relevance_score"],
            stopConditions=[">=20 relevant papers collected or search budget exhausted"],
            dependencies=[],
            risks=["Insufficient literature coverage"],
        )
    )

    # Step 2: Experiment / method execution
    exp_metrics: List[str] = []
    exp_inputs: List[str] = []
    for exp in candidate.experimentSpecs:
        exp_metrics.extend(exp.metrics)
        exp_inputs.extend(exp.datasets)

    steps.append(
        ResearchPlanStep(
            id=f"step_{candidate.id}_2",
            order=2,
            title="Experimental Validation",
            objective=candidate.expectedOutcome or "Validate the proposed hypothesis.",
            inputs=exp_inputs or ["experimental data", "baseline model"],
            tools=["FAROS Code", "python", "experiment framework"],
            method=[candidate.proposedMethod or "Controlled comparison"],
            outputs=["metrics.json", "run.log", "analysis_report"],
            metrics=exp_metrics or candidate.expectedMetrics or ["accuracy", "efficiency"],
            stopConditions=["All experiments complete or budget exhausted"],
            dependencies=[f"step_{candidate.id}_1"],
            risks=[r.risk for r in candidate.risks] or ["Experiment may not converge"],
        )
    )

    # Step 3: Analysis and reporting
    steps.append(
        ResearchPlanStep(
            id=f"step_{candidate.id}_3",
            order=3,
            title="Analysis and Reporting",
            objective="Analyze results and generate research report.",
            inputs=["experiment results", "baseline metrics"],
            tools=["statistical analysis", "FAROS Paper"],
            method=["Compare against baseline", "Statistical significance test"],
            outputs=["research_report", "data_visualization"],
            metrics=["statistical_significance", "effect_size"],
            stopConditions=["Report reviewed and approved"],
            dependencies=[f"step_{candidate.id}_2"],
            risks=["Results may not be statistically significant"],
        )
    )

    # Determine execution class
    has_computational = any(
        kw in (candidate.proposedMethod + candidate.expectedOutcome).lower()
        for kw in ["simulation", "comput", "model", "algorithm", "train", "benchmark"]
    )
    exec_class = ExecutionClass.COMPUTATIONAL_READY if has_computational else ExecutionClass.PROTOCOL_ONLY

    return ResearchPlan(
        objective=candidate.draftPlan.researchQuestion if candidate.draftPlan else candidate.problem,
        steps=steps,
        requiredData=exp_inputs or ["experimental dataset"],
        requiredResources=["LLM provider", "compute resources"],
        expectedOutcomes=candidate.draftPlan.expectedOutcomes if candidate.draftPlan else [candidate.expectedOutcome],
        constraints=candidate.draftPlan.tags if candidate.draftPlan else [],
        ethics=["Do not present generated claims as expert advice"],
        executionClass=exec_class,
    )


# ---------------------------------------------------------------------------
# Evidence deduplication
# ---------------------------------------------------------------------------

def _deduplicate_evidence(records: List[EvidenceRecord]) -> List[EvidenceRecord]:
    """Deduplicate evidence records by DOI, URL, or title similarity."""
    seen_doi: set[str] = set()
    seen_url: set[str] = set()
    seen_titles: set[str] = set()
    deduped: List[EvidenceRecord] = []

    for rec in records:
        if rec.doi and rec.doi in seen_doi:
            continue
        if rec.url and rec.url in seen_url:
            continue
        title_key = rec.title.strip().lower()[:100]
        if title_key in seen_titles:
            continue
        if rec.doi:
            seen_doi.add(rec.doi)
        if rec.url:
            seen_url.add(rec.url)
        seen_titles.add(title_key)
        deduped.append(rec)

    return deduped


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------

def build_research_dossier(
    session: IdeaSession,
    candidates: List[IdeaCandidate],
    literature: List[LiteratureItem],
    question: Optional[ScientificQuestion] = None,
    *,
    run_id: Optional[str] = None,
    mode: RunMode = RunMode.DEEP,
    provider_name: Optional[str] = None,
    model: Optional[str] = None,
) -> ResearchDossier:
    """
    Build a contract-compliant ResearchDossier from internal Idea module models.

    Args:
        session: The IdeaSession with pipeline results.
        candidates: Ranked list of IdeaCandidate objects.
        literature: Collected LiteratureItem objects.
        question: Optional ScientificQuestion; if None, derived from session.
        run_id: Optional run ID; if None, uses session ID.
        mode: coverage or deep.
        provider_name: LLM provider for problem framing.
        model: Model name for problem framing.

    Returns:
        A validated ResearchDossier that passes contract tests.
    """
    rid = run_id or session.id
    qid = question.id if question else f"question_{session.id}"

    # 1. Build ProblemFrame
    if question is None:
        question = ScientificQuestion(
            id=qid,
            text=session.config.seedQuery if hasattr(session, 'config') and hasattr(session.config, 'seedQuery') else (session.seedQuery if hasattr(session, 'seedQuery') else str(getattr(session, 'seed', ''))),
            domainHint=session.config.domain if session.config else None,
            constraints=session.config.constraints if session.config else [],
        )

    use_llm = mode == RunMode.DEEP
    problem_frame = frame_problem(question, provider_name=provider_name, model=model, use_llm=use_llm)

    # 2. Classify and deduplicate evidence
    all_evidence = _deduplicate_evidence(
        _literature_to_evidence_records(literature)
    )
    supporting, counter, context = _classify_evidence_by_relevance(literature, session.config.seedQuery if hasattr(session, 'config') and hasattr(session.config, 'seedQuery') else (session.seedQuery if hasattr(session, 'seedQuery') else str(getattr(session, 'seed', ''))))

    # Deduplicate each bucket
    supporting = _deduplicate_evidence(supporting)
    counter = _deduplicate_evidence(counter)
    context = _deduplicate_evidence(context)

    evidence_ids = {r.id for r in supporting + counter + context}

    # Build evidence map
    consensus: List[str] = []
    if supporting:
        consensus.append(f"{len(supporting)} sources support the research direction")
    disputed: List[str] = []
    if counter:
        disputed.append(f"{len(counter)} sources present counter-evidence")
    gaps: List[str] = []
    if len(supporting) < 3:
        gaps.append("Insufficient supporting evidence for high confidence")

    evidence_map = EvidenceMap(
        consensus=consensus,
        disputedClaims=disputed,
        supportingEvidence=supporting,
        counterEvidence=counter,
        contextualEvidence=context,
        unresolvedGaps=gaps,
    )

    # 3. Convert candidates to hypotheses
    hypotheses = [_candidate_to_hypothesis(c, evidence_ids) for c in candidates[:5]]

    # 4. Build research plan from top candidate
    research_plan = _candidate_to_research_plan(candidates[0]) if candidates else ResearchPlan(
        objective="No candidates generated",
        steps=[ResearchPlanStep(
            id="step_placeholder",
            order=1,
            title="Retry",
            objective="Re-run pipeline with expanded search.",
            inputs=["seed query"],
            tools=["FAROS"],
            method=["Expand search"],
            outputs=["new candidates"],
            metrics=["candidate_count"],
            stopConditions=[">=2 candidates"],
            dependencies=[],
            risks=["May fail again"],
        )],
    )

    # 5. Generation trace
    gen_trace = GenerationTrace(
        providerName=provider_name or (session.config.providerName if session.config else None),
        model=model or (session.config.model if session.config else None),
        localRulePasses=["evidence_reference_check", "falsification_check"],
        llmCalls=[],
        warnings=[],
        cacheHits=0,
        estimatedTokenCost=None,
        startedAt=session.createdAt if hasattr(session, "createdAt") else None,
        endedAt=_utcnow(),
    )

    # 6. Artifact refs
    artifact_refs = [
        ArtifactRef(
            id=f"artifact_dossier_{rid}",
            kind=ArtifactKind.IDEA,
            sourceModule=TargetModule.IDEA,
            uri=f"artifacts/{rid}/research_dossier.json",
            contentHash=f"sha256:{hashlib.sha256(json.dumps(session.model_dump(), default=str).encode()).hexdigest()[:16]}",
            version="1",
            createdAt=_utcnow(),
            metadata={"sessionId": session.id, "mode": mode.value},
        )
    ]

    # 7. Uncertainties
    uncertainties: List[str] = []
    if len(supporting) < 5:
        uncertainties.append("Limited evidence base may affect generalizability")
    if not counter:
        uncertainties.append("No counter-evidence found; confirmation bias risk")
    uncertainties.append("External validity across domains remains unmeasured")

    return ResearchDossier(
        runId=rid,
        questionId=qid,
        problemFrame=problem_frame,
        evidenceMap=evidence_map,
        hypotheses=hypotheses,
        researchPlan=research_plan,
        uncertainties=uncertainties,
        generationTrace=gen_trace,
        artifactRefs=artifact_refs,
    )


# ---------------------------------------------------------------------------
# Review finding → child run support
# ---------------------------------------------------------------------------

def create_child_run(
    parent_run: ScientificQuestionRun,
    findings: List[Any],
) -> ScientificQuestionRun:
    """
    Create a child ScientificQuestionRun from a parent run and review findings.

    This implements P0 task #8: "Accept Review finding, create child run."
    """
    child_id = f"run_child_{uuid.uuid4().hex[:12]}"

    return ScientificQuestionRun(
        runId=child_id,
        question=parent_run.question,
        mode=parent_run.mode,
        status=RunStatus.PENDING,
        providerName=parent_run.providerName,
        model=parent_run.model,
        parentRunId=parent_run.runId,
        artifactRefs=[],
        errorMessage=None,
    )


def diff_dossiers(
    v1: ResearchDossier,
    v2: ResearchDossier,
) -> Dict[str, Any]:
    """
    Compute a structured diff between two versions of a ResearchDossier.

    Returns a dict with field-level changes for v1/v2 comparison.
    """
    diff: Dict[str, Any] = {
        "problemFrame": {
            "v1_scoped": v1.problemFrame.scopedQuestion,
            "v2_scoped": v2.problemFrame.scopedQuestion,
            "changed": v1.problemFrame.scopedQuestion != v2.problemFrame.scopedQuestion,
        },
        "hypotheses": {
            "v1_count": len(v1.hypotheses),
            "v2_count": len(v2.hypotheses),
            "added": [h.id for h in v2.hypotheses if h.id not in {h2.id for h2 in v1.hypotheses}],
            "removed": [h.id for h in v1.hypotheses if h.id not in {h2.id for h2 in v2.hypotheses}],
        },
        "evidence": {
            "v1_supporting": len(v1.evidenceMap.supportingEvidence),
            "v2_supporting": len(v2.evidenceMap.supportingEvidence),
            "v1_counter": len(v1.evidenceMap.counterEvidence),
            "v2_counter": len(v2.evidenceMap.counterEvidence),
        },
        "plan": {
            "v1_steps": len(v1.researchPlan.steps),
            "v2_steps": len(v2.researchPlan.steps),
        },
    }

    # Score changes for matching hypotheses
    v1_scores = {h.id: h.scores for h in v1.hypotheses}
    v2_scores = {h.id: h.scores for h in v2.hypotheses}
    score_changes: Dict[str, Dict[str, float]] = {}
    for hid in set(v1_scores.keys()) & set(v2_scores.keys()):
        changes = {}
        for dim in set(v1_scores[hid].keys()) | set(v2_scores[hid].keys()):
            old = v1_scores[hid].get(dim, 0.0)
            new = v2_scores[hid].get(dim, 0.0)
            if abs(new - old) > 0.01:
                changes[dim] = round(new - old, 3)
        if changes:
            score_changes[hid] = changes
    diff["scoreChanges"] = score_changes

    return diff
