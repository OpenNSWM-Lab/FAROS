"""PlanPackage evidence collection for paper writing."""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple


def _data_dir() -> str:
    base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
    return os.path.join(base, "data")


def _compact_text(value: Any, max_chars: int = 500) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _load_package_raw(package_id: str) -> Optional[Dict[str, Any]]:
    path = os.path.join(_data_dir(), "plan_packages", f"{package_id}.json")
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _load_research_dossier(path_value: Any) -> Optional[Dict[str, Any]]:
    path = os.path.realpath(str(path_value or ""))
    data_root = os.path.realpath(_data_dir())
    if not path or os.path.commonpath([path, data_root]) != data_root or not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            dossier = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    return dossier if isinstance(dossier, dict) else None


def _collect_dossier_evidence(paper: Dict[str, Any], max_papers: int) -> Optional[Dict[str, Any]]:
    dossier = _load_research_dossier(paper.get("researchDossierPath"))
    if not dossier:
        return None
    evidence_map = _as_dict(dossier.get("evidenceMap"))
    problem_frame = _as_dict(dossier.get("problemFrame"))
    research_question = str(
        problem_frame.get("scopedQuestion") or problem_frame.get("originalQuestion") or ""
    )
    ranking_context = " ".join((
        research_question,
        json.dumps(dossier.get("hypotheses") or [], ensure_ascii=False),
        str(_as_dict(dossier.get("researchPlan")).get("objective") or ""),
    ))
    stopwords = {
        "a", "an", "and", "are", "as", "at", "be", "by", "can", "compared",
        "does", "for", "from", "in", "into", "is", "of", "on", "or", "than",
        "that", "the", "their", "this", "to", "with", "ai", "generated", "improve",
    }
    query_terms = {
        token
        for token in re.findall(r"[a-z0-9]+", ranking_context.lower())
        if len(token) > 2 and token not in stopwords
    }
    query_stems = {token[:6] for token in query_terms if len(token) >= 6}
    domain_weights = {
        "halluc": 10,
        "unsupp": 5,
        "claim": 4,
        "factua": 4,
        "calibr": 3,
        "consis": 3,
        "decomp": 3,
        "scient": 3,
        "entity": 2,
        "negati": 2,
        "numeri": 2,
        "review": 2,
        "semant": 2,
        "lexica": 1,
    }

    def weighted_overlap(candidate_stems: set[str]) -> int:
        return sum(
            domain_weights.get(stem, 1)
            for stem in query_stems.intersection(candidate_stems)
        )

    core_stems = {"halluc", "unsupp", "claim", "factua"}.intersection(query_stems)

    def dossier_rank(item: Dict[str, Any]) -> Tuple[bool, int, int, int, float]:
        title_terms = set(re.findall(r"[a-z0-9]+", str(item.get("title") or "").lower()))
        body_terms = set(re.findall(
            r"[a-z0-9]+",
            f"{item.get('title', '')} {item.get('summary', '')}".lower(),
        ))
        title_stems = {token[:6] for token in title_terms if len(token) >= 6}
        body_stems = {token[:6] for token in body_terms if len(token) >= 6}
        return (
            bool(item.get("verified")),
            weighted_overlap(body_stems.intersection(core_stems)),
            len(query_terms.intersection(title_terms)) + weighted_overlap(title_stems),
            len(query_terms.intersection(body_terms)) + weighted_overlap(body_stems),
            float(item.get("relevanceScore") or 0),
        )

    ranked: List[Dict[str, Any]] = []
    quotas = (("supportingEvidence", "support", 8), ("counterEvidence", "counter", 3), ("contextualEvidence", "context", 2))
    seen: set[str] = set()
    for field, role, quota in quotas:
        candidates = sorted(
            [item for item in _as_list(evidence_map.get(field)) if isinstance(item, dict)],
            key=dossier_rank,
            reverse=True,
        )
        selected = 0
        for item in candidates:
            title = str(item.get("title") or "").strip()
            identity = str(item.get("doi") or item.get("url") or title).strip().lower()
            if not title or not identity or identity in seen:
                continue
            seen.add(identity)
            ranked.append({
                "paperId": item.get("id") or f"dossier_ref_{len(ranked) + 1}",
                "title": title,
                "authors": _as_list(item.get("authors"))[:8],
                "year": item.get("year"),
                "venue": item.get("venue") or item.get("sourceType") or "",
                "url": item.get("url") or (f"https://doi.org/{item['doi']}" if item.get("doi") else ""),
                "doi": item.get("doi") or "",
                "role": role,
                "relevanceScore": item.get("relevanceScore", 0),
                "summary": _compact_text(item.get("summary", ""), 800),
                "verified": bool(item.get("verified")),
            })
            selected += 1
            if selected >= quota or len(ranked) >= max_papers:
                break
        if len(ranked) >= max_papers:
            break
    hypotheses = [item for item in _as_list(dossier.get("hypotheses")) if isinstance(item, dict)]
    research_plan = _as_dict(dossier.get("researchPlan"))
    return {
        "schemaVersion": "paper-plan-evidence/v1",
        "status": "collected",
        "resolution": {
            "source": "faros_research_dossier",
            "researchDossierPath": paper.get("researchDossierPath"),
            "runId": dossier.get("runId"),
        },
        "idea": hypotheses[0] if hypotheses else {},
        "researchQuestion": research_question,
        "hypothesis": (hypotheses[0].get("statement") or hypotheses[0].get("hypothesis") or "") if hypotheses else "",
        "background": {
            "summary": " ".join(str(item) for item in _as_list(evidence_map.get("consensus"))),
            "currentLimitations": _as_list(evidence_map.get("disputedClaims")),
        },
        "literature": {
            "summary": f"FAROS research dossier supplied {len(ranked)} verified supporting, counter, and contextual sources.",
            "coverage": {"source": "research_dossier", "selected": len(ranked)},
            "keyPapers": ranked,
        },
        "gap": {"summary": " ".join(str(item) for item in _as_list(evidence_map.get("unresolvedGaps")))},
        "validationPlan": _as_list(research_plan.get("steps")),
        "evidenceTrace": {"dossierRunId": dossier.get("runId"), "questionId": dossier.get("questionId")},
        "warnings": ["Literature evidence was resolved from the FAROS research dossier because no PlanPackage was linked."],
    }


def _iter_package_raw() -> Iterable[Dict[str, Any]]:
    package_dir = os.path.join(_data_dir(), "plan_packages")
    if not os.path.isdir(package_dir):
        return
    for name in sorted(os.listdir(package_dir), reverse=True):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(package_dir, name), "r", encoding="utf-8") as handle:
                package = json.load(handle)
            if isinstance(package, dict) and package.get("packageId"):
                yield package
        except Exception:
            continue


def _legacy_plan_link_path(link_id: str) -> str:
    base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, "data", "plan_links", f"{link_id}.json")


def _load_plan_link(link_id: str) -> Optional[Dict[str, Any]]:
    try:
        from app.modules.platform.storage import get_plan_link

        link = get_plan_link(link_id)
        if link:
            return link
    except Exception:
        pass

    legacy_path = _legacy_plan_link_path(link_id)
    if os.path.isfile(legacy_path):
        try:
            with open(legacy_path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except Exception:
            return None
    return None


def _project_source(project_id: str) -> Dict[str, Optional[str]]:
    try:
        from app.db import crud
        from app.db.engine import get_session_context

        with get_session_context() as db:
            project = crud.get_project_v2(db, project_id)
            if project:
                return {
                    "ideaSessionId": getattr(project, "source_idea_session_id", None),
                    "ideaCandidateId": getattr(project, "source_candidate_id", None),
                }
    except Exception:
        pass
    return {"ideaSessionId": None, "ideaCandidateId": None}


def _plan_session_source(plan_session_id: Optional[str]) -> Dict[str, Optional[str]]:
    if not plan_session_id:
        return {"ideaSessionId": None, "ideaCandidateId": None}
    try:
        from app.modules.platform.storage import get_plan_session_storage

        session = get_plan_session_storage().get(plan_session_id)
        config = getattr(session, "config", None)
        if config:
            return {
                "ideaSessionId": getattr(config, "ideaSessionId", None),
                "ideaCandidateId": getattr(config, "ideaCandidateId", None),
            }
    except Exception:
        pass
    return {"ideaSessionId": None, "ideaCandidateId": None}


def resolve_plan_package_for_paper(paper: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """Resolve the PlanPackage associated with a paper record.

    Supports direct package IDs, plan links, and linked code projects. Existing
    data has used both package IDs and legacy plan-link IDs in ``planLinkId``.
    """
    resolution: Dict[str, Any] = {
        "source": "none",
        "planLinkId": paper.get("planLinkId"),
        "projectId": paper.get("projectId"),
        "packageId": paper.get("planPackageId"),
    }

    explicit_package_id = paper.get("planPackageId")
    plan_link_id = paper.get("planLinkId")
    if not explicit_package_id and isinstance(plan_link_id, str) and plan_link_id.startswith("ppkg_"):
        explicit_package_id = plan_link_id
        resolution["source"] = "planLinkId.packageId"

    if explicit_package_id:
        package = _load_package_raw(str(explicit_package_id))
        if package:
            resolution["packageId"] = package.get("packageId")
            return package, resolution

    match_idea_session_id: Optional[str] = None
    match_candidate_id: Optional[str] = None

    if plan_link_id and not str(plan_link_id).startswith("ppkg_"):
        link = _load_plan_link(str(plan_link_id))
        if link:
            resolution["source"] = "planLinkId"
            resolution["planSessionId"] = link.get("planSessionId")
            resolution["candidateId"] = link.get("candidateId")
            match_idea_session_id = link.get("planSessionId")
            match_candidate_id = link.get("candidateId")
            plan_source = _plan_session_source(link.get("planSessionId"))
            match_idea_session_id = plan_source.get("ideaSessionId") or match_idea_session_id
            match_candidate_id = plan_source.get("ideaCandidateId") or match_candidate_id

    if paper.get("projectId"):
        project_source = _project_source(str(paper["projectId"]))
        if project_source.get("ideaSessionId") or project_source.get("ideaCandidateId"):
            resolution["source"] = "project"
            resolution["projectIdeaSessionId"] = project_source.get("ideaSessionId")
            resolution["projectCandidateId"] = project_source.get("ideaCandidateId")
            match_idea_session_id = project_source.get("ideaSessionId") or match_idea_session_id
            match_candidate_id = project_source.get("ideaCandidateId") or match_candidate_id

    for package in _iter_package_raw():
        source = _as_dict(package.get("source"))
        if match_idea_session_id and source.get("ideaSessionId") == match_idea_session_id:
            resolution["packageId"] = package.get("packageId")
            return package, resolution
        if match_candidate_id and source.get("ideaCandidateId") == match_candidate_id:
            resolution["packageId"] = package.get("packageId")
            return package, resolution

    return None, resolution


def _selected_gap(package: Dict[str, Any]) -> Dict[str, Any]:
    gap = _as_dict(package.get("gap"))
    items = [item for item in _as_list(gap.get("items")) if isinstance(item, dict)]
    selected_id = gap.get("selectedGapId")
    for item in items:
        if item.get("id") == selected_id:
            return item
    return items[0] if items else {}


def _paper_supports(paper_id: str, selected_gap: Dict[str, Any], contributions: List[Dict[str, Any]]) -> List[str]:
    supports: List[str] = []
    if paper_id in _as_list(selected_gap.get("supportedByPaperIds")):
        supports.append("selected_gap")
    for contribution in contributions:
        for ref in _as_list(contribution.get("evidenceRefs")):
            if isinstance(ref, dict) and ref.get("id") == paper_id:
                supports.append(f"contribution:{contribution.get('id', '')}".rstrip(":"))
    return list(dict.fromkeys(supports))


def _summarize_paper(
    paper: Dict[str, Any],
    selected_gap: Dict[str, Any],
    contributions: List[Dict[str, Any]],
) -> Dict[str, Any]:
    paper_id = str(paper.get("paperId") or paper.get("id") or "")
    return {
        "paperId": paper_id,
        "title": paper.get("title", ""),
        "authors": _as_list(paper.get("authors"))[:8],
        "year": paper.get("year"),
        "venue": paper.get("venue", ""),
        "url": paper.get("url", ""),
        "role": paper.get("role", "background"),
        "relevanceScore": paper.get("relevanceScore", 0),
        "relevanceReason": _compact_text(paper.get("relevanceReason", ""), 300),
        "summary": _compact_text(paper.get("summary", ""), 800),
        "methods": [
            {
                "name": _compact_text(_as_dict(item).get("name", ""), 120),
                "description": _compact_text(_as_dict(item).get("description", item), 240),
                "category": _as_dict(item).get("category", ""),
            }
            for item in _as_list(paper.get("methods"))[:5]
        ],
        "findings": [
            {
                "claim": _compact_text(_as_dict(item).get("claim", item), 260),
                "evidence": _compact_text(_as_dict(item).get("evidence", ""), 220),
            }
            for item in _as_list(paper.get("findings"))[:5]
        ],
        "limitations": [_compact_text(item, 220) for item in _as_list(paper.get("limitations"))[:5]],
        "claims": [
            {
                "id": _as_dict(item).get("claimId") or _as_dict(item).get("id", ""),
                "text": _compact_text(_as_dict(item).get("text", item), 260),
            }
            for item in _as_list(paper.get("claims"))[:5]
        ],
        "supports": _paper_supports(paper_id, selected_gap, contributions),
    }


def collect_plan_evidence_for_paper(paper: Dict[str, Any], max_papers: int = 12) -> Dict[str, Any]:
    package, resolution = resolve_plan_package_for_paper(paper)
    if not package:
        dossier_evidence = _collect_dossier_evidence(paper, max_papers)
        if dossier_evidence:
            return dossier_evidence
        return {
            "schemaVersion": "paper-plan-evidence/v1",
            "status": "missing",
            "resolution": resolution,
            "warnings": ["No linked PlanPackage could be resolved from this paper."],
        }

    selected_gap = _selected_gap(package)
    contributions = [item for item in _as_list(package.get("contributionStatement")) if isinstance(item, dict)]
    papers = [item for item in _as_list(_as_dict(package.get("literatureSurvey")).get("papers")) if isinstance(item, dict)]
    support_ids = set(_as_list(selected_gap.get("supportedByPaperIds")))
    trace = _as_dict(package.get("evidenceTrace"))
    support_ids.update(str(item) for item in _as_list(trace.get("selectedPaperIds")))
    support_ids.update(str(item) for item in _as_list(trace.get("structuredPaperIds")))

    ranked_papers = sorted(
        papers,
        key=lambda item: (
            str(item.get("paperId")) in support_ids,
            float(item.get("relevanceScore") or 0),
        ),
        reverse=True,
    )

    stages = []
    for stage in _as_list(package.get("stages")):
        if not isinstance(stage, dict):
            continue
        stages.append({
            "id": stage.get("id", ""),
            "order": stage.get("order", 0),
            "title": stage.get("title", ""),
            "goal": _compact_text(stage.get("goal", stage.get("desc", "")), 500),
            "method": _compact_text(stage.get("method", ""), 500),
            "steps": [
                {
                    "id": _as_dict(step).get("id", ""),
                    "order": _as_dict(step).get("order", 0),
                    "title": _as_dict(step).get("title", ""),
                    "description": _compact_text(_as_dict(step).get("desc", ""), 500),
                    "method": _compact_text(_as_dict(step).get("method", ""), 500),
                    "outputs": _as_list(_as_dict(step).get("outputs")),
                    "expected": _as_list(_as_dict(step).get("expected")),
                    "evidenceRefs": _as_list(_as_dict(step).get("evidenceRefs")),
                }
                for step in _as_list(stage.get("steps"))
                if isinstance(step, dict)
            ],
        })

    quality_gate = _as_dict(package.get("qualityGate"))
    readiness = _as_dict(package.get("downstreamReadiness"))
    warnings = [
        "PlanPackage expected metrics are planned targets, not observed experimental results.",
    ]
    warnings.extend(str(item) for item in _as_list(quality_gate.get("warnings"))[:5])
    warnings.extend(_compact_text(_as_dict(item).get("message", item), 240) for item in _as_list(readiness.get("warnings"))[:5])

    return {
        "schemaVersion": "paper-plan-evidence/v1",
        "status": "collected",
        "resolution": resolution,
        "package": {
            "packageId": package.get("packageId", ""),
            "schemaVersion": package.get("schemaVersion", ""),
            "status": package.get("status", ""),
            "createdAt": package.get("createdAt", ""),
        },
        "idea": _as_dict(package.get("idea")),
        "researchQuestion": package.get("researchQuestion", ""),
        "hypothesis": package.get("hypothesis", ""),
        "background": {
            "summary": _compact_text(_as_dict(package.get("background")).get("summary", ""), 1400),
            "motivation": _compact_text(_as_dict(package.get("background")).get("motivation", ""), 700),
            "currentLimitations": [
                _compact_text(item, 350)
                for item in _as_list(_as_dict(package.get("background")).get("currentLimitations"))[:8]
            ],
            "domainContext": _as_list(_as_dict(package.get("background")).get("domainContext"))[:12],
            "evidenceRefs": _as_list(_as_dict(package.get("background")).get("evidenceRefs"))[:12],
        },
        "literature": {
            "summary": _compact_text(_as_dict(package.get("literatureSurvey")).get("summary", ""), 1000),
            "coverage": _as_dict(_as_dict(package.get("literatureSurvey")).get("coverage")),
            "keyPapers": [
                _summarize_paper(item, selected_gap, contributions)
                for item in ranked_papers[:max_papers]
            ],
        },
        "gap": {
            "summary": _compact_text(_as_dict(package.get("gap")).get("summary", ""), 800),
            "selected": selected_gap,
        },
        "principle": _as_dict(package.get("principle")),
        "contributionStatement": contributions,
        "validationPlan": stages,
        "qualityGate": quality_gate,
        "downstreamReadiness": readiness,
        "evidenceTrace": trace,
        "warnings": list(dict.fromkeys([item for item in warnings if item])),
    }
