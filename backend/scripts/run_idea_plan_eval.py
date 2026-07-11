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
        result[step.name] = {
            "status": str(step.status),
            "durationMs": getattr(step, "durationMs", None),
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
            item["status"] = str(session.status)
            item["finalCandidateIds"] = list(session.finalCandidateIds or [])
            item["hiddenCandidateIds"] = list(session.hiddenCandidateIds or [])
            steps = step_map(session)
            item["steps"] = {
                name: {
                    "status": data["status"],
                    "durationMs": data["durationMs"],
                    "errorCount": len(data["errors"]),
                }
                for name, data in steps.items()
            }
            novelty_out = steps.get("noveltyCheck", {}).get("outputs", {})
            brainstorm_out = steps.get("ideaBrainstorm", {}).get("outputs", {})
            rank_out = steps.get("rankCandidates", {}).get("outputs", {})
            evidence_out = steps.get("evidenceGate", {}).get("outputs", {})
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
                "ideaReviewPassedCount": rank_out.get("ideaReviewPassedCount"),
                "literatureRepairCount": rank_out.get("literatureRepairCount"),
                "regeneratedCandidateIds": rank_out.get("regeneratedCandidateIds"),
            }
            item["qualitySignals"] = {
                "finalCandidateCount": len(session.finalCandidateIds or []),
                "targetFinalCandidateCount": pick(rank_out, ["finalSelection", "targetFinalCandidateCount"]),
                "directionDiversitySatisfied": pick(rank_out, ["finalSelection", "directionDiversitySatisfied"]),
                "finalDirectionTypes": pick(rank_out, ["finalSelection", "finalDirectionTypes"]),
                "evidenceGatePassed": pick(
                    evidence_out,
                    ["evidenceGate", "passed"],
                    evidence_out.get("passed"),
                ),
                "structuredPaperQualityPassed": pick(novelty_out, ["structuredPaperQualityGate", "passed"]),
                "selectedPaperQualityPassed": pick(novelty_out, ["selectedPaperQualityGate", "passed"]),
                "researchDirectionCount": brainstorm_out.get("researchDirectionCount"),
                "candidateCount": brainstorm_out.get("candidateCount"),
            }
            item["finalCandidates"] = [candidate_quality(c) for c in service.get_candidates(session.id)]
            if session.finalCandidateIds:
                plan_start = time.perf_counter()
                package = get_plan_package_service().create_from_idea_session(
                    session.id,
                    candidate_id=session.finalCandidateIds[0],
                    generation_mode="hybrid",
                    reviewer_mode="hybrid",
                    max_repair_rounds=2,
                )
                item["planPackage"] = {
                    "packageId": package.packageId,
                    "seconds": round(time.perf_counter() - plan_start, 2),
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
                }
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
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
    print(json.dumps({"event": "eval_done", "summary": summary_path}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
