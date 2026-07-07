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
) -> Tuple[List[Finding], Dict[str, Any]]:
    mode = (budget_mode or "balanced").lower()
    trace: Dict[str, Any] = {
        "routingMode": mode,
        "providerName": provider_name,
        "requestedModel": model,
        "selectedFindingIds": [],
        "llmCalls": [],
        "estimatedTokenCost": 0,
        "skipped": False,
        "skipReason": None,
    }

    selected = _select_findings(findings, mode)
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

    prompt = _build_refinement_prompt(paper, claims, evidence, selected)
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
        _apply_assessments(findings, assessments, response.model)
    except ProviderError as exc:
        logger.warning("ReviewX LLM escalation skipped after provider error: %s", exc)
        trace["skipped"] = True
        trace["skipReason"] = str(exc)
    except Exception as exc:
        logger.warning("ReviewX LLM escalation failed: %s", exc, exc_info=True)
        trace["skipped"] = True
        trace["skipReason"] = f"LLM escalation failed: {str(exc)[:240]}"

    return findings, trace


def _select_findings(findings: List[Finding], mode: str) -> List[Finding]:
    if mode == "local_only":
        return []
    severity_rank = {"blocker": 0, "major": 1, "minor": 2, "info": 3}
    threshold = 0.5 if mode == "deep" else 0.72
    limit = 8 if mode == "deep" else 3
    candidates = [
        f for f in findings
        if f.severity in {"blocker", "major"} or f.confidence >= threshold
    ]
    candidates.sort(key=lambda f: (severity_rank.get(f.severity, 9), -f.confidence))
    return candidates[:limit]


def _build_refinement_prompt(
    paper: Dict[str, Any],
    claims: List[Claim],
    evidence: List[Evidence],
    selected: List[Finding],
) -> str:
    claims_by_id = {c.id: c for c in claims}
    evidence_by_id = {e.id: e for e in evidence}
    finding_payload = []
    for finding in selected:
        claim = claims_by_id.get(finding.claimId or "")
        evs = [evidence_by_id[eid] for eid in finding.evidenceIds if eid in evidence_by_id]
        finding_payload.append({
            "findingId": finding.id,
            "severity": finding.severity,
            "riskType": finding.riskType,
            "claim": claim.text if claim else None,
            "localDescription": finding.description,
            "localSuggestedFix": finding.suggestedFix,
            "evidence": [
                {
                    "id": ev.id,
                    "type": ev.evidenceType,
                    "sourceModule": ev.sourceModule,
                    "sourcePath": ev.sourcePath,
                    "summary": ev.summary,
                }
                for ev in evs[:6]
            ],
        })

    return f"""You are ReviewX, an evidence-grounded research review agent inside FAROS.
Your task is NOT to write a full peer review. Refine only the selected high-risk findings.

Paper title: {paper.get("title", "Untitled")}
Paper type: {paper.get("paperType", "unknown")}

Selected findings with local evidence:
{json.dumps(finding_payload, ensure_ascii=False, indent=2)}

For each finding:
- decide whether the local risk is valid, partially valid, or overestimated;
- explain the evidence gap in one concrete sentence;
- produce a stricter actionable fix for the target module;
- do not invent metrics, baselines, or citations that are not in the evidence list.

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
  ]
}}
"""


def _extract_assessments(text: str) -> List[Dict[str, Any]]:
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
            return []
        try:
            data = json.loads(match.group())
        except json.JSONDecodeError:
            return []
    assessments = data.get("assessments", []) if isinstance(data, dict) else []
    return [a for a in assessments if isinstance(a, dict) and a.get("findingId")]


def _apply_assessments(findings: List[Finding], assessments: List[Dict[str, Any]], model: str):
    by_id = {finding.id: finding for finding in findings}
    for assessment in assessments:
        finding = by_id.get(str(assessment.get("findingId")))
        if not finding:
            continue
        reviewer_assessment = str(assessment.get("reviewerAssessment") or "").strip()
        revised_fix = str(assessment.get("revisedSuggestedFix") or "").strip()
        decision = str(assessment.get("decision") or "valid").strip()
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
            delta = 0.0
        finding.confidence = round(max(0.0, min(1.0, finding.confidence + delta)), 2)
