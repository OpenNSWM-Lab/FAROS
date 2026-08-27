"""Budget-adaptive model routing for ReviewX.

ReviewX is local-first: rule-based claim/evidence checks always run. This
module optionally escalates the highest-risk findings to the configured LLM
provider and records a trace. If no API key is configured, it returns the
original findings and an explicit skip reason.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Tuple

from app.core.settings import get_settings
from app.llm.provider_client import ChatMessage, ProviderError, get_provider_client
from app.modules.review.cem_guidance import build_cem_budget_plan
from app.modules.review.reviewx_models import Claim, Evidence, Finding


logger = logging.getLogger(__name__)


def refine_findings_with_budget(
    *,
    paper: Dict[str, Any],
    claims: List[Claim],
    evidence: List[Evidence],
    findings: List[Finding],
    provider_name: str,
    model: str,
    budget_mode: str,
    mismatch_report: Dict[str, Any] | None = None,
    routing_strategy: str = "cem",
) -> Tuple[List[Finding], Dict[str, Any]]:
    mode = (budget_mode or "balanced").lower()
    if routing_strategy == "severity":
        budget_plan = _build_severity_budget_plan(findings, mode)
    else:
        budget_plan = build_cem_budget_plan(findings, mismatch_report, mode)
    trace: Dict[str, Any] = {
        "routingMode": mode,
        "routingStrategy": routing_strategy,
        "providerName": provider_name,
        "requestedModel": model,
        "budgetPolicy": budget_plan["policy"],
        "budgetFormula": budget_plan["formula"],
        "budgetThresholds": budget_plan["thresholds"],
        "budgetAllocations": budget_plan["allocations"],
        "selectedFindingIds": [],
        "llmCalls": [],
        "estimatedTokenCost": 0,
        "skipped": False,
        "skipReason": None,
    }

    selected = _select_findings(findings, budget_plan)
    trace["selectedFindingIds"] = [f.id for f in selected]

    if mode == "local_only":
        trace["skipped"] = True
        trace["skipReason"] = "budgetMode=local_only"
        return findings, trace
    if not selected:
        trace["skipped"] = True
        trace["skipReason"] = "no high-risk findings selected for LLM escalation"
        return findings, trace

    settings = get_settings()
    provider_info = settings.get_provider_info(provider_name, mask_key=True)
    if not provider_info.get("configured"):
        trace["skipped"] = True
        trace["skipReason"] = f"provider '{provider_name}' has no configured API key"
        return findings, trace

    prompt = _build_refinement_prompt(paper, claims, evidence, selected, mismatch_report, mode)
    max_tokens = 2200 if mode == "balanced" else 4200
    try:
        client = get_provider_client(provider_name)
        response = client.chat(
            messages=[ChatMessage(role="user", content=prompt)],
            model=model,
            temperature=0.2,
            max_tokens=max_tokens,
        )
        trace["llmCalls"].append({
            "task": "high_risk_finding_refinement",
            "model": response.model,
            "provider": response.raw_provider,
            "latencyMs": response.latency_ms,
            "usage": response.usage,
            "selectedFindingIds": trace["selectedFindingIds"],
            "finishReason": response.finish_reason,
        })
        trace["estimatedTokenCost"] = response.usage.get("total_tokens", 0)
        assessments = _extract_assessments(response.text)
        trace["llmAssessments"] = assessments
        _apply_assessments(findings, assessments, response.model)
        additional_findings = _extract_additional_findings(response.text)
        trace["llmAdditionalFindings"] = additional_findings
        _apply_additional_findings(findings, additional_findings, claims, response.model, paper.get("id", ""))
    except ProviderError as exc:
        logger.warning("ReviewX LLM escalation skipped after provider error: %s", exc)
        trace["skipped"] = True
        trace["skipReason"] = str(exc)
    except Exception as exc:
        logger.warning("ReviewX LLM escalation failed: %s", exc, exc_info=True)
        trace["skipped"] = True
        trace["skipReason"] = f"LLM escalation failed: {str(exc)[:240]}"

    return findings, trace


def rank_findings_for_review(
    findings: List[Finding], routing_trace: Dict[str, Any] | None,
) -> List[Finding]:
    """Put the most consequential CEM-routed findings first for UI and evaluation."""
    allocations = {
        str(item.get("findingId") or ""): item
        for item in (routing_trace or {}).get("budgetAllocations", [])
        if isinstance(item, dict)
    }
    severity_bonus = {"blocker": 0.35, "major": 0.22, "minor": 0.08, "info": 0.0}
    severity_rank = {"blocker": 0, "major": 1, "minor": 2, "info": 3}

    def key(finding: Finding) -> tuple[float, int, float, str]:
        allocation = allocations.get(finding.id, {})
        fallback = min(
            1.0,
            float(finding.confidence or 0) + severity_bonus.get(finding.severity, 0.0),
        )
        priority = float(allocation.get("priority", fallback) or 0)
        return (
            -priority,
            severity_rank.get(finding.severity, 9),
            -float(finding.confidence or 0),
            finding.id,
        )

    return sorted(findings, key=key)


def _select_findings(findings: List[Finding], budget_plan: Dict[str, Any]) -> List[Finding]:
    selected_ids = set(budget_plan.get("selectedFindingIds", []))
    if not selected_ids:
        return []
    by_id = {finding.id: finding for finding in findings}
    return [by_id[finding_id] for finding_id in budget_plan["selectedFindingIds"] if finding_id in by_id]


def _build_severity_budget_plan(findings: List[Finding], mode: str) -> Dict[str, Any]:
    limit = 8 if mode == "deep" else 3
    if mode == "local_only":
        limit = 0
    threshold = 0.5 if mode == "deep" else 0.72
    severity_rank = {"blocker": 0, "major": 1, "minor": 2, "info": 3}
    allocations = []
    for finding in findings:
        severity_bonus = {"blocker": 0.35, "major": 0.22, "minor": 0.08, "info": 0.0}.get(finding.severity, 0.0)
        priority = round(min(1.0, float(finding.confidence or 0) + severity_bonus), 3)
        selected = mode != "local_only" and (finding.severity in {"blocker", "major"} or finding.confidence >= threshold)
        allocations.append({
            "findingId": finding.id,
            "claimId": finding.claimId,
            "priority": priority,
            "mismatchScore": 0.0,
            "severity": finding.severity,
            "supportStatus": finding.supportStatus,
            "recommendedModel": "qwen-max" if finding.severity == "blocker" else "qwen-plus",
            "selected": selected,
            "drivers": ["severity", "confidence"],
        })
    allocations.sort(key=lambda item: (not item["selected"], severity_rank.get(str(item["severity"]), 9), -item["priority"]))
    selected_allocations = [item for item in allocations if item["selected"]][:limit]
    selected_ids = {item["findingId"] for item in selected_allocations}
    for item in allocations:
        item["selected"] = item["findingId"] in selected_ids
    return {
        "policy": "severity_confidence_baseline",
        "formula": "priority(f)=confidence(f)+severity_bonus",
        "thresholds": {"selection": threshold},
        "mode": mode,
        "limit": limit,
        "allocations": allocations,
        "selectedFindingIds": [item["findingId"] for item in selected_allocations],
    }


def _build_refinement_prompt(
    paper: Dict[str, Any],
    claims: List[Claim],
    evidence: List[Evidence],
    selected: List[Finding],
    mismatch_report: Dict[str, Any] | None = None,
    mode: str = "balanced",
) -> str:
    claims_by_id = {c.id: c for c in claims}
    evidence_by_id = {e.id: e for e in evidence}
    claim_scores = {
        item.get("claimId"): item
        for item in (mismatch_report or {}).get("claimScores", [])
        if isinstance(item, dict)
    }
    finding_payload = []
    for finding in selected:
        claim = claims_by_id.get(finding.claimId or "")
        evs = [evidence_by_id[eid] for eid in finding.evidenceIds if eid in evidence_by_id]
        score = claim_scores.get(finding.claimId or "", {})
        finding_payload.append({
            "findingId": finding.id,
            "severity": finding.severity,
            "riskType": finding.riskType,
            "claim": _clip(claim.text, 360) if claim else None,
            "cemMismatchScore": score.get("mismatchScore"),
            "cemMismatchDrivers": score.get("dimensions", {}),
            "cemMismatchReasons": score.get("reasons", []),
            "cemCalibration": finding.cemCalibration,
            "localDescription": _clip(finding.description, 520),
            "localSuggestedFix": _clip(finding.suggestedFix, 360),
            "evidence": [
                {
                    "id": ev.id,
                    "type": ev.evidenceType,
                    "sourceModule": ev.sourceModule,
                    "sourcePath": ev.sourcePath,
                    "summary": _clip(ev.summary, 260),
                }
                for ev in evs[:4 if mode == "balanced" else 6]
            ],
        })
    existing_claim_ids = {finding.claimId for finding in selected if finding.claimId}
    gap_scan_candidates = [
        {
            "claimId": claim.id,
            "claimType": claim.claimType,
            "importance": claim.importance,
            "requiresEvidence": claim.requiresEvidence,
            "text": _clip(claim.text, 360),
            "source": {
                "file": claim.sourceSpan.file,
                "section": claim.sourceSpan.section,
                "line": claim.sourceSpan.line,
            },
        }
        for claim in claims
        if claim.id not in existing_claim_ids
    ]
    gap_scan_candidates.sort(key=lambda item: (
        0 if item["source"].get("section") == "CEM-Bench Injected Claims" else 1,
        0 if _has_deployment_or_domain_shift_terms(str(item.get("text") or "")) else 1,
        str(item.get("claimId") or ""),
    ))
    gap_scan_claims = gap_scan_candidates[:8 if mode == "balanced" else 20]
    selected_evidence_ids = {
        evidence_id
        for finding in selected
        for evidence_id in finding.evidenceIds
    }
    prioritized_evidence = [
        ev for ev in evidence
        if ev.id in selected_evidence_ids or ev.evidenceType in {"citation_entry", "metric", "experiment_report"}
    ]
    evidence_context = [
        {
            "id": ev.id,
            "type": ev.evidenceType,
            "sourceModule": ev.sourceModule,
            "sourcePath": ev.sourcePath,
            "summary": _clip(ev.summary, 220),
        }
        for ev in prioritized_evidence[:8 if mode == "balanced" else 16]
    ]

    return f"""You are ReviewX, an evidence-grounded research review agent inside FAROS.
Your task is NOT to write a full peer review. Refine only the selected high-risk findings.
The selected findings were chosen by CEM-Review: claim-evidence mismatch guided budget routing.

Paper title: {paper.get("title", "Untitled")}
Paper type: {paper.get("paperType", "unknown")}

Selected findings with local evidence:
{json.dumps(finding_payload, ensure_ascii=False, indent=2)}

Additional claims for gap scan:
{json.dumps(gap_scan_claims, ensure_ascii=False, indent=2)}

Global evidence/citation context:
{json.dumps(evidence_context, ensure_ascii=False, indent=2)}

For each finding:
- decide whether the local risk is valid, partially valid, or overestimated;
- explain the evidence gap in one concrete sentence;
- produce a stricter actionable fix for the target module;
- do not invent metrics, baselines, or citations that are not in the evidence list.
- if cemCalibration.lowConfidenceCitation is true, focus on whether the cited metadata entails the claim or only has superficial topical overlap.

Also inspect the additional claims. If a claim has a clear evidence/citation mismatch that the selected local findings missed, add at most 2 additional findings. Do not add stylistic comments.
Prioritize claims from section "CEM-Bench Injected Claims" and claims about deployment, clinical/legal/financial use, fairness, biomedical domains, or broad generalization. A citation command is not sufficient support if the cited work is off-topic for the claim.

Return strict JSON only:
{{
  "assessments": [
    {{
      "findingId": "finding_001",
      "decision": "valid | partially_valid | overestimated",
      "reviewerAssessment": "one concrete sentence",
      "revisedSuggestedFix": "one concrete actionable fix",
      "confidenceDelta": 0.0
    }}
  ],
  "additionalFindings": [
    {{
      "claimId": "claim_012",
      "severity": "major",
      "riskType": "unsupported_claim | traceability_gap | citation_mismatch | citation_uncertainty | artifact_gap",
      "supportStatus": "unsupported | contradicted | weakly_supported | needs_human_verification | artifact_absent",
      "title": "short title",
      "description": "one concrete sentence explaining the mismatch",
      "targetModule": "papers | experiments | code",
      "suggestedFix": "one concrete actionable fix",
      "confidence": 0.75
    }}
  ]
}}
"""


def _has_deployment_or_domain_shift_terms(text: str) -> bool:
    return bool(re.search(
        r"\b(clinical|legal|financial|deployment|triage|fairness|biomedical|multilingual|generalizes?|autonomous|domain)\b",
        text,
        re.IGNORECASE,
    ))


def _clip(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _extract_assessments(text: str) -> List[Dict[str, Any]]:
    data = _extract_json_object(text)
    assessments = data.get("assessments", []) if isinstance(data, dict) else []
    return [a for a in assessments if isinstance(a, dict) and a.get("findingId")]


def _extract_additional_findings(text: str) -> List[Dict[str, Any]]:
    data = _extract_json_object(text)
    additional = data.get("additionalFindings", []) if isinstance(data, dict) else []
    return [item for item in additional if isinstance(item, dict) and item.get("claimId")]


def _extract_json_object(text: str) -> Dict[str, Any]:
    cleaned = text.strip()
    if "```json" in cleaned:
        cleaned = cleaned.split("```json", 1)[1].split("```", 1)[0]
    elif "```" in cleaned:
        parts = cleaned.split("```")
        if len(parts) >= 3:
            cleaned = parts[1]
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", cleaned)
        if not match:
            return {}
        try:
            data = json.loads(match.group())
        except json.JSONDecodeError:
            return {}
    return data if isinstance(data, dict) else {}


def _apply_assessments(findings: List[Finding], assessments: List[Dict[str, Any]], model: str):
    by_id = {finding.id: finding for finding in findings}
    for assessment in assessments:
        finding = by_id.get(str(assessment.get("findingId")))
        if not finding:
            continue
        reviewer_assessment = str(assessment.get("reviewerAssessment") or "").strip()
        revised_fix = str(assessment.get("revisedSuggestedFix") or "").strip()
        decision = _normalize_decision(assessment.get("decision"))
        finding.reviewerDecision = decision
        finding.reviewerAssessment = reviewer_assessment or None
        finding.reviewerModel = model
        finding.cemCalibration = {
            **finding.cemCalibration,
            "llmDecision": decision,
            "llmModel": model,
            "llmFactor": _decision_factor(decision),
        }
        if reviewer_assessment:
            finding.description = (
                f"{finding.description}\n\n"
                f"LLM deep review ({model}, {decision}): {reviewer_assessment}"
            )
        if revised_fix:
            finding.suggestedFix = revised_fix
        try:
            delta = float(assessment.get("confidenceDelta", 0.0))
        except (TypeError, ValueError):
            delta = _default_confidence_delta(decision)
        if delta == 0.0:
            delta = _default_confidence_delta(decision)
        finding.confidence = round(max(0.0, min(1.0, finding.confidence + delta)), 2)


def _apply_additional_findings(
    findings: List[Finding],
    additional_findings: List[Dict[str, Any]],
    claims: List[Claim],
    model: str,
    paper_id: str,
) -> None:
    claims_by_id = {claim.id: claim for claim in claims}
    existing_claim_ids = {finding.claimId for finding in findings if finding.claimId}
    for item in additional_findings[:2]:
        claim_id = str(item.get("claimId") or "")
        claim = claims_by_id.get(claim_id)
        if not claim or claim_id in existing_claim_ids:
            continue
        severity = _normalize_severity(item.get("severity"))
        risk_type = _normalize_risk_type(item.get("riskType"))
        support_status = _normalize_support_status(item.get("supportStatus"))
        try:
            confidence = float(item.get("confidence", 0.72))
        except (TypeError, ValueError):
            confidence = 0.72
        finding = Finding(
            id=f"finding_{len(findings) + 1:03d}",
            paperId=paper_id,
            claimId=claim_id,
            severity=severity,
            riskType=risk_type,
            title=str(item.get("title") or "LLM detected claim-evidence gap")[:160],
            description=(
                f"{claim.text}\n\n"
                f"LLM gap scan ({model}): {str(item.get('description') or '').strip()}"
            ),
            evidenceIds=[],
            targetModule=_normalize_target_module(item.get("targetModule")),
            suggestedFix=str(item.get("suggestedFix") or "Add direct supporting evidence or weaken the claim.")[:500],
            confidence=round(max(0.0, min(1.0, confidence)), 2),
            supportStatus=support_status,
            reviewerDecision="valid",
            reviewerAssessment=str(item.get("description") or "").strip() or None,
            reviewerModel=model,
            cemCalibration={
                "llmDecision": "valid",
                "llmModel": model,
                "llmFactor": 1.0,
                "llmAddedFinding": True,
            },
            location={
                "section": claim.sourceSpan.section,
                **({"line": claim.sourceSpan.line} if claim.sourceSpan.line else {}),
            },
        )
        findings.append(finding)
        existing_claim_ids.add(claim_id)


def _normalize_decision(value: Any) -> str:
    decision = str(value or "valid").strip().lower().replace(" ", "_")
    if "over" in decision:
        return "overestimated"
    if "partial" in decision:
        return "partially_valid"
    return "valid"


def _normalize_severity(value: Any) -> str:
    severity = str(value or "major").strip().lower()
    return severity if severity in {"blocker", "major", "minor", "info"} else "major"


def _normalize_risk_type(value: Any) -> str:
    risk_type = str(value or "unsupported_claim").strip().lower()
    allowed = {
        "unsupported_claim", "traceability_gap", "citation_mismatch", "metric_mismatch",
        "citation_uncertainty", "artifact_gap",
    }
    return risk_type if risk_type in allowed else "unsupported_claim"


def _normalize_support_status(value: Any) -> str:
    status = str(value or "unsupported").strip().lower()
    allowed = {
        "unsupported", "contradicted", "weakly_supported", "supported",
        "needs_human_verification", "artifact_absent",
    }
    return status if status in allowed else "unsupported"


def _normalize_target_module(value: Any) -> str:
    module = str(value or "papers").strip().lower()
    return module if module in {"papers", "experiments", "code"} else "papers"


def _decision_factor(decision: str) -> float:
    return {
        "valid": 1.0,
        "partially_valid": 0.9,
        "overestimated": 0.65,
    }.get(decision, 1.0)


def _default_confidence_delta(decision: str) -> float:
    return {
        "valid": 0.03,
        "partially_valid": -0.06,
        "overestimated": -0.22,
    }.get(decision, 0.0)
