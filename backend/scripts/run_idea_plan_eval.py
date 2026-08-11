import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models.idea import IdeaSessionConfig
from app.modules.idea.service import IdeaGenerationService
from app.services.plan_package_service import get_plan_package_service
from app.services.plan_package_views import build_plan_package_presentation


SEEDS = [
    {
        "label": "A_llm_agents_scientific_discovery",
        "seed": "LLM agents for scientific discovery",
        "domain": "AI for science, autonomous research agents",
        "paperType": "system",
        "maxCandidates": 5,
        "baselineSeconds": 1503,
        "baselineNote": "previous run: final=2, evidence=0.95, 5 directions, one drifted candidate",
    },
    {
        "label": "B_citation_faithful_medical_rag",
        "seed": "citation-faithful medical RAG for high-risk clinical question answering",
        "domain": "medical NLP, retrieval-augmented generation, clinical QA",
        "paperType": "evaluation",
        "maxCandidates": 3,
        "baselineSeconds": 1720,
        "baselineNote": "previous run: final=2, evidence=0.85, candidates too similar",
    },
    {
        "label": "C_reliable_multi_agent_research_automation",
        "seed": "reliable multi-agent research automation with evidence-grounded planning and self-review",
        "domain": "multi-agent systems, research automation, LLM evaluation",
        "paperType": "system",
        "maxCandidates": 3,
        "baselineSeconds": 1439,
        "baselineNote": "previous run: final=2, evidence=0.95, candidates similar",
    },
    {
        "label": "D_cjk_named_work_negative_stress",
        "seed": "预测红楼梦可能结局",
        "domain": "",
        "paperType": "system",
        "maxCandidates": 3,
        "baselineSeconds": 975,
        "baselineNote": "negative pollution stress; awaiting_evidence is valid",
        "negativeStress": True,
    },
]


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def step_map(session):
    result = {}
    if not session.trace:
        return result
    for step in session.trace.steps:
        errors = getattr(step, "errors", None)
        if errors is None:
            error = getattr(step, "error", None)
            errors = [error] if error else []
        elif isinstance(errors, str):
            errors = [errors]
        duration_seconds = getattr(step, "durationSeconds", None)
        duration_ms = getattr(step, "durationMs", None)
        if duration_ms is None and duration_seconds is not None:
            duration_ms = round(float(duration_seconds) * 1000, 3)
        if duration_seconds is None and duration_ms is not None:
            duration_seconds = round(float(duration_ms) / 1000, 3)
        result[step.name] = {
            "status": str(step.status),
            "durationMs": duration_ms,
            "durationSeconds": duration_seconds,
            "outputs": step.outputs or {},
            "errors": errors,
        }
    return result


def pick(data, path, default=None):
    cur = data
    for part in path:
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def quality_loop_summary(rank_output, session):
    summary = rank_output.get("qualityLoopSummary") if isinstance(rank_output, dict) else None
    if summary:
        return summary
    fallback = getattr(session, "qualityLoopSummary", None) or {}
    if hasattr(fallback, "model_dump"):
        return fallback.model_dump()
    return fallback if isinstance(fallback, dict) else {}


def reviewer_usage(rank_output):
    reports = []
    for gate in rank_output.get("ideaReviewGate", []) if isinstance(rank_output, dict) else []:
        if isinstance(gate, dict):
            reports.extend(report for report in gate.get("reviewerReports", []) if isinstance(report, dict))
    all_llm_reports = [
        report
        for report in reports
        if "llm" in str(report.get("mode", "")).lower()
    ]
    llm_reports = [report for report in all_llm_reports if not report.get("cacheHit", False)]
    cached_llm_reports = [report for report in all_llm_reports if report.get("cacheHit", False)]
    return {
        "reportCount": len(reports),
        "llmReportCount": len(llm_reports),
        "cachedLlmReportCount": len(cached_llm_reports),
        "llmLatencyMs": round(sum(float(report.get("llmLatencyMs", 0) or 0) for report in llm_reports), 3),
        "llmUsed": bool(llm_reports),
    }


def null_paths(value, prefix=""):
    if value is None:
        return [prefix or "$"]
    paths = []
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{prefix}.{key}" if prefix else str(key)
            paths.extend(null_paths(item, child))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            paths.extend(null_paths(item, f"{prefix}[{index}]"))
    return paths


def plan_quality_summary(package, elapsed_seconds):
    plan_owned = {
        "researchQuestion": package.researchQuestion,
        "hypothesis": package.hypothesis,
        "constants": package.constants,
        "stages": [stage.model_dump(mode="json") for stage in package.stages],
    }
    expected = [
        item
        for stage in plan_owned["stages"]
        for step in stage.get("steps", [])
        for item in step.get("expected", [])
    ]
    text = json.dumps(plan_owned, ensure_ascii=False).lower()
    placeholders = [
        value
        for value in [
            "specified before implementation",
            "primary_metric",
            "readiness",
            "planned_metric",
            "default plan step",
        ]
        if value in text
    ]
    segment_fallbacks = [
        warning
        for warning in package.generation.warnings
        if warning.startswith("segment_fallback:")
    ]
    presentation = build_plan_package_presentation(package)
    return {
        "elapsedSeconds": round(float(elapsed_seconds), 3),
        "status": str(
            package.status.value
            if hasattr(package.status, "value")
            else package.status
        ),
        "fallbackUsed": package.generation.fallbackUsed,
        "segmentFallbacks": segment_fallbacks,
        "llmUsedSections": package.generation.llmUsedSections,
        "repairRounds": package.generation.repairRounds,
        "schemaRepairRounds": package.generation.schemaRepairRounds,
        "llmReviewerUsed": package.generation.llmReviewerUsed,
        "implementationReady": package.qualityGate.implementationReady,
        "downstreamReady": package.qualityGate.downstreamReady,
        "stageCount": len(package.stages),
        "stepCount": sum(len(stage.steps) for stage in package.stages),
        "expectedMetricCount": len(expected),
        "placeholderValues": placeholders,
        "criticalNullPaths": null_paths(plan_owned),
        "userConcernCount": len(presentation.reviewSummary.mainConcerns),
    }


def candidate_quality(candidate):
    critique = candidate.critique
    if isinstance(critique, dict):
        critique_weakness_count = len(critique.get("weaknesses", []) or [])
    else:
        critique_weakness_count = len(critique.weaknesses) if critique else 0
    return {
        "id": candidate.id,
        "title": candidate.title,
        "overallScore": round(float(candidate.overallScore or 0), 3),
        "novelty": round(float(candidate.novelty or 0), 3),
        "feasibility": round(float(candidate.feasibility or 0), 3),
        "impact": round(float(candidate.impact or 0), 3),
        "alignment": round(float(candidate.alignment or 0), 3),
        "referenceSupport": round(float(candidate.referenceSupport or 0), 3),
        "experimentSpecificity": round(float(candidate.experimentSpecificity or 0), 3),
        "methodLen": len(candidate.proposedMethod or ""),
        "hypothesisLen": len(candidate.hypothesisStatement or ""),
        "expectedOutcomeLen": len(candidate.expectedOutcome or ""),
        "priorWorkCount": len(candidate.closestPriorWork or []),
        "hasGraphEvidence": bool(candidate.graphEvidence),
        "critiqueWeaknessCount": critique_weakness_count,
    }


def retrieval_quality(literature_outputs):
    return {
        "resultCountBeforeDedup": literature_outputs.get("resultCountBeforeDedup", 0),
        "uniqueResultCount": literature_outputs.get("uniqueResultCount", 0),
        "duplicateMergeCount": literature_outputs.get("duplicateMergeCount", 0),
        "evidenceTierCounts": literature_outputs.get("evidenceTierCounts", {}),
        "rejectionReasonCounts": literature_outputs.get("rejectionReasonCounts", {}),
        "retrievalRoleCounts": literature_outputs.get("retrievalRoleCounts", {}),
        "topicIntentProfile": literature_outputs.get("topicIntentProfile", {}),
    }


def freeze_checks(
    *,
    session,
    spec,
    literature_outputs,
    novelty_outputs,
    evidence_gate,
    deep_read_limit,
):
    status_value = getattr(session.status, "value", str(session.status))
    is_negative_stress = bool(spec.get("negativeStress", False))
    raw_gate = literature_outputs.get("paperQualityGate", {})
    return {
        "positiveSeedCompleted": is_negative_stress or status_value == "completed",
        "completionHasTwoIdeas": (
            status_value != "completed" or len(session.finalCandidateIds or []) >= 2
        ),
        "waitingStateIsRecoverable": status_value in {
            "completed",
            "awaiting_evidence",
            "awaiting_ideas",
        },
        "rawRoleCoverageEnabled": bool(
            (raw_gate.get("roleCoverage") or {}).get("enabled", False)
        ),
        "structuredRoleCoverageEnabled": (
            is_negative_stress and status_value == "awaiting_evidence"
        ) or bool((evidence_gate.get("roleCoverage") or {}).get("enabled", False)),
        "deepReadBounded": int(
            novelty_outputs.get("deepReadRequestedCount", 0) or 0
        ) <= int(deep_read_limit),
    }


def build_closure_report(summary):
    hard_checks = [
        check
        for seed in summary.get("seeds", [])
        for check in seed.get("freezeChecks", {}).values()
    ]
    return {
        "decision": (
            "freeze" if hard_checks and all(hard_checks) else "continue_closure"
        ),
        "technicalSeeds": [
            {
                "label": seed["label"],
                "sessionId": seed.get("sessionId"),
                "status": seed.get("status"),
                "finalCandidateIds": seed.get("finalCandidateIds", []),
                "performance": seed.get("performance", {}),
                "retrievalQuality": seed.get("retrievalQuality", {}),
                "freezeChecks": seed.get("freezeChecks", {}),
            }
            for seed in summary.get("seeds", [])
        ],
        "externalProviderIncidents": [
            seed.get("sessionError")
            for seed in summary.get("seeds", [])
            if seed.get("sessionError")
            and "provider" in seed.get("sessionError", "").lower()
        ],
        "remainingRisks": [
            f"{seed['label']}:{name}"
            for seed in summary.get("seeds", [])
            for name, passed in seed.get("freezeChecks", {}).items()
            if not passed
        ],
    }


def main() -> None:
    summary_path = os.environ["FAROS_EVAL_SUMMARY"]
    summary_dir = os.path.dirname(os.path.abspath(summary_path))
    if summary_dir:
        os.makedirs(summary_dir, exist_ok=True)
    summary = {
        "runId": os.environ.get("FAROS_EVAL_RUN_ID", ""),
        "startedAt": utcnow(),
        "provider": "qwen",
        "model": "qwen-max",
        "seeds": [],
    }
    print(json.dumps({"event": "eval_start", "at": utcnow(), "seedCount": len(SEEDS)}, ensure_ascii=False), flush=True)

    only = {
        item.strip()
        for item in os.environ.get("FAROS_EVAL_ONLY", "").split(",")
        if item.strip()
    }
    selected_seeds = [spec for spec in SEEDS if not only or spec["label"] in only]
    summary["selectedLabels"] = [spec["label"] for spec in selected_seeds]
    for spec in selected_seeds:
        seed_start = time.perf_counter()
        item = {
            "label": spec["label"],
            "seed": spec["seed"],
            "baselineSeconds": spec["baselineSeconds"],
            "baselineNote": spec["baselineNote"],
            "startedAt": utcnow(),
        }
        print(json.dumps({"event": "seed_start", **item}, ensure_ascii=False), flush=True)
        try:
            service = IdeaGenerationService()
            config = IdeaSessionConfig(
                providerName="qwen",
                model="qwen-max",
                seedQuery=spec["seed"],
                paperType=spec["paperType"],
                maxCandidates=spec["maxCandidates"],
                maxPapers=80,
                domain=spec["domain"],
                constraints=[
                    "Return evidence-grounded, implementation-ready research ideas; do not claim executed experiments."
                ],
            )
            session = service.create_session(config)
            item["sessionId"] = session.id
            print(
                json.dumps(
                    {"event": "idea_session_created", "label": spec["label"], "sessionId": session.id},
                    ensure_ascii=False,
                ),
                flush=True,
            )
            service.start_session(session.id)
            session = service.run_pipeline(session.id)
            item["ideaSeconds"] = round(time.perf_counter() - seed_start, 2)
            item["status"] = getattr(session.status, "value", str(session.status))
            item["sessionError"] = session.errorMessage
            item["finalCandidateIds"] = list(session.finalCandidateIds or [])
            item["hiddenCandidateIds"] = list(session.hiddenCandidateIds or [])
            steps = step_map(session)
            item["steps"] = {
                name: {
                    "status": data["status"],
                    "durationMs": data["durationMs"],
                    "durationSeconds": data["durationSeconds"],
                    "errorCount": len(data["errors"]),
                }
                for name, data in steps.items()
            }
            novelty_out = steps.get("noveltyCheck", {}).get("outputs", {})
            brainstorm_out = steps.get("ideaBrainstorm", {}).get("outputs", {})
            rank_out = steps.get("rankCandidates", {}).get("outputs", {})
            evidence_out = steps.get("evidenceGate", {}).get("outputs", {})
            evidence_gate = evidence_out.get("evidenceGate", evidence_out)
            literature_out = steps.get("literatureSearch", {}).get("outputs", {})
            repair_novelty_out = pick(evidence_out, ["repairReport", "noveltyOutputs"], {}) or {}
            quality_loop = quality_loop_summary(rank_out, session)
            review_usage = reviewer_usage(rank_out)
            item["performance"] = {
                "baselineSeconds": spec["baselineSeconds"],
                "ideaSeconds": item["ideaSeconds"],
                "deltaSeconds": round(item["ideaSeconds"] - spec["baselineSeconds"], 2),
                "speedupVsBaseline": round(spec["baselineSeconds"] / item["ideaSeconds"], 3)
                if item["ideaSeconds"]
                else None,
                "structuredPaperCount": novelty_out.get("structuredPaperCount"),
                "structuredCacheHitCount": novelty_out.get("structuredCacheHitCount"),
                "structuredSessionCacheHitCount": novelty_out.get("structuredSessionCacheHitCount"),
                "structuredGlobalCacheHitCount": novelty_out.get("structuredGlobalCacheHitCount"),
                "deepReadRequestedCount": novelty_out.get("deepReadRequestedCount"),
                "structuredCacheStoredCount": novelty_out.get("structuredCacheStoredCount"),
                "repairStructuredCacheHitCount": repair_novelty_out.get("structuredCacheHitCount", 0),
                "repairDeepReadRequestedCount": repair_novelty_out.get("deepReadRequestedCount", 0),
                "totalStructuredCacheHitCount": int(novelty_out.get("structuredCacheHitCount", 0) or 0)
                + int(repair_novelty_out.get("structuredCacheHitCount", 0) or 0),
                "totalDeepReadRequestedCount": int(novelty_out.get("deepReadRequestedCount", 0) or 0)
                + int(repair_novelty_out.get("deepReadRequestedCount", 0) or 0),
                "ideaReviewPassedCount": rank_out.get("ideaReviewPassedCount"),
                "literatureRepairCount": rank_out.get("literatureRepairCount"),
                "regeneratedCandidateIds": rank_out.get("regeneratedCandidateIds"),
                "reviewerUsage": review_usage,
            }
            item["retrievalQuality"] = retrieval_quality(literature_out)
            item["freezeChecks"] = freeze_checks(
                session=session,
                spec=spec,
                literature_outputs=literature_out,
                novelty_outputs=novelty_out,
                evidence_gate=evidence_gate,
                deep_read_limit=int(
                    os.getenv("FAROS_IDEA_DEEP_READ_MAX_PAPERS", "24")
                ),
            )
            item["qualitySignals"] = {
                "finalCandidateCount": len(session.finalCandidateIds or []),
                "targetFinalCandidateCount": quality_loop.get("targetFinalCandidateCount"),
                "qualityStatus": quality_loop.get("qualityStatus"),
                "requiresRegeneration": quality_loop.get("requiresRegeneration", False),
                "directionDiversitySatisfied": quality_loop.get("directionDiversitySatisfied"),
                "finalDirectionTypes": quality_loop.get("finalDirectionTypes"),
                "evidenceGatePassed": pick(
                    evidence_out,
                    ["evidenceGate", "passed"],
                    evidence_out.get("passed"),
                ),
                "structuredPaperQualityPassed": pick(novelty_out, ["structuredPaperQualityGate", "passed"]),
                "selectedPaperQualityPassed": pick(novelty_out, ["selectedPaperQualityGate", "passed"]),
                "researchDirectionCount": brainstorm_out.get("researchDirectionCount"),
                "candidateCount": brainstorm_out.get("candidateCount"),
                "externalPaperCount": evidence_gate.get("externalPaperCount"),
                "localPaperCount": evidence_gate.get("localPaperCount"),
                "localOnly": evidence_gate.get("localOnly"),
                "sourcesUsed": evidence_gate.get("sourcesUsed"),
                "sourceQuality": evidence_gate.get("sourceQuality"),
                "providerFallbackRisk": evidence_gate.get("providerFallbackRisk"),
                "evidenceReviewMode": evidence_gate.get("reviewMode"),
                "evidenceLlmReviewerPassed": pick(evidence_gate, ["llmReviewer", "passed"]),
                "evidenceLlmReviewerScore": pick(evidence_gate, ["llmReviewer", "score"]),
                "evidenceRepairAttempted": evidence_out.get("repairAttempted", False),
            }
            all_candidates = service.get_candidates(session.id, view="debug")
            candidate_by_id = {candidate.id: candidate for candidate in all_candidates}
            item["finalCandidates"] = [
                candidate_quality(candidate_by_id[candidate_id])
                for candidate_id in session.finalCandidateIds or []
                if candidate_id in candidate_by_id
            ]
            item["reviewedCandidates"] = [candidate_quality(candidate) for candidate in all_candidates]
            item["ideaReviewGate"] = [
                {
                    "candidateId": gate.get("candidateId"),
                    "passed": gate.get("passed"),
                    "scoreAfterGate": gate.get("scoreAfterGate"),
                    "blockingIssues": gate.get("blockingIssues", []),
                    "reviewerReportCount": len(gate.get("reviewerReports", []) or []),
                    "llmReviewerReportCount": reviewer_usage({"ideaReviewGate": [gate]})["llmReportCount"],
                    "cachedLlmReviewerReportCount": reviewer_usage({"ideaReviewGate": [gate]})["cachedLlmReportCount"],
                }
                for gate in rank_out.get("ideaReviewGate", [])
                if isinstance(gate, dict)
            ]
            if session.finalCandidateIds:
                plan_start = time.perf_counter()
                package = get_plan_package_service().create_from_idea_session(
                    session.id,
                    candidate_id=session.finalCandidateIds[0],
                    generation_mode="hybrid",
                    reviewer_mode="hybrid",
                    max_repair_rounds=2,
                )
                plan_elapsed = time.perf_counter() - plan_start
                item["planPackage"] = {
                    "packageId": package.packageId,
                    "seconds": round(plan_elapsed, 2),
                    "schemaVersion": package.schemaVersion,
                    "status": str(package.status),
                    "qualityGate": package.qualityGate.model_dump()
                    if hasattr(package.qualityGate, "model_dump")
                    else {},
                    "stageCount": len(package.stages or []),
                    "literatureSurveyCount": len(package.literatureSurvey.papers or [])
                    if package.literatureSurvey
                    else 0,
                    "contributionCount": len(getattr(package, "contributionStatement", []) or []),
                    "researchQuestion": package.researchQuestion,
                    "hypothesis": package.hypothesis,
                    "ideaHypothesis": package.idea.hypothesisStatement,
                    "hypothesisMatchesIdea": package.hypothesis.strip() == package.idea.hypothesisStatement.strip(),
                    "stageTitles": [stage.title for stage in package.stages or []],
                    "metrics": list(dict.fromkeys(
                        expected.metric
                        for stage in package.stages or []
                        for step in stage.steps or []
                        for expected in step.expected or []
                    )),
                    "upstreamFieldsNonNull": {
                        "idea": package.idea is not None,
                        "background": package.background is not None,
                        "literatureSurvey": package.literatureSurvey is not None,
                        "gap": package.gap is not None,
                        "principle": package.principle is not None,
                        "evidenceTrace": package.evidenceTrace is not None,
                        "qualityGate": package.qualityGate is not None,
                    },
                    "criticalNullPaths": null_paths({
                        "researchQuestion": package.researchQuestion,
                        "hypothesis": package.hypothesis,
                        "constants": package.constants,
                        "stages": [stage.model_dump() for stage in package.stages or []],
                    }),
                    "generation": package.generation.model_dump()
                    if hasattr(package.generation, "model_dump")
                    else {},
                }
                item["planPackage"].update(
                    plan_quality_summary(package, plan_elapsed)
                )
            item["finishedAt"] = utcnow()
            print(
                json.dumps(
                    {
                        "event": "seed_done",
                        "label": spec["label"],
                        "sessionId": item.get("sessionId"),
                        "ideaSeconds": item.get("ideaSeconds"),
                        "finalCount": item["qualitySignals"].get("finalCandidateCount"),
                        "planPackage": item.get("planPackage", {}).get("packageId"),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
        except Exception as exc:
            item["status"] = "failed"
            item["error"] = str(exc)
            item["traceback"] = traceback.format_exc()
            item["finishedAt"] = utcnow()
            print(
                json.dumps({"event": "seed_failed", "label": spec["label"], "error": str(exc)}, ensure_ascii=False),
                flush=True,
            )
        summary["seeds"].append(item)
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2, default=str)

    summary["finishedAt"] = utcnow()
    closure_report = build_closure_report(summary)
    report_path = os.path.join(summary_dir, "idea-closure-report.json")
    summary["closureReport"] = report_path
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(closure_report, f, ensure_ascii=False, indent=2, default=str)
    print(
        json.dumps(
            {
                "event": "eval_done",
                "summary": summary_path,
                "closureReport": report_path,
                "decision": closure_report["decision"],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    if closure_report["decision"] != "freeze":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
