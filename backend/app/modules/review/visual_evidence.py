"""Optional multimodal verification of scientific figures for ReviewX."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.core.paths import get_data_dir
from app.core.settings import get_settings
from app.llm.provider_client import ChatMessage, ProviderError, get_provider_client
from app.modules.review.reviewx_models import (
    Claim,
    Evidence,
    EvidenceVerification,
    Finding,
    RiskNode,
)


logger = logging.getLogger(__name__)

_ALLOWED_MIME_TYPES = {"image/png", "image/jpeg", "image/webp"}
_MAX_IMAGE_BYTES = 8 * 1024 * 1024
_CLAIM_STATUS = {
    "supported",
    "weakly_supported",
    "contradicted",
    "unsupported",
    "needs_human_verification",
}
_ANOMALY_TYPES = {
    "caption_mismatch",
    "numeric_mismatch",
    "trend_reversal",
    "legend_mismatch",
    "axis_issue",
    "uncertainty_missing",
    "unreadable_figure",
    "claim_mismatch",
    "claim_support_gap",
    "other",
}
_NUMERIC_OR_RESULT_RE = re.compile(
    r"\b(?:\d+(?:\.\d+)?\s*%?|accuracy|precision|recall|f1|auc|auroc|"
    r"improv\w*|outperform\w*|increase\w*|decrease\w*|higher|lower|baseline|ablation)\b",
    re.IGNORECASE,
)


@dataclass
class VisualAuditResult:
    verifications: List[EvidenceVerification] = field(default_factory=list)
    findings: List[Finding] = field(default_factory=list)
    risk_nodes: List[RiskNode] = field(default_factory=list)
    trace: Dict[str, Any] = field(default_factory=dict)


def audit_visual_evidence(
    *,
    paper: Dict[str, Any],
    claims: List[Claim],
    evidence: List[Evidence],
    links: Dict[str, List[str]],
    artifacts: Dict[str, Any],
    provider_name: str,
    visual_model: Optional[str],
    budget_mode: str,
    enabled: bool,
    client: Any = None,
    data_root: Optional[str] = None,
) -> VisualAuditResult:
    """Inspect figure pixels only after explicit opt-in and fail soft on every provider error."""

    mode = str(budget_mode or "balanced").lower()
    model = str(
        visual_model
        or os.getenv("FAROS_REVIEWX_VISUAL_MODEL", "qwen3-vl-plus")
    ).strip()
    trace: Dict[str, Any] = {
        "enabled": bool(enabled),
        "status": "disabled",
        "providerName": provider_name,
        "model": model,
        "selectedFigureCount": 0,
        "auditedFigureCount": 0,
        "captionCheckCount": 0,
        "verificationCount": 0,
        "anomalyCount": 0,
        "estimatedTokenCost": 0,
        "calls": [],
        "skipped": True,
        "skipReason": "visual audit was not enabled",
    }
    result = VisualAuditResult(trace=trace)
    if not enabled:
        return result
    if mode == "local_only":
        trace.update(status="skipped", skipReason="budgetMode=local_only does not call a vision model")
        return result

    figures = _select_figures(artifacts.get("visualFigures", []), mode, data_root=data_root)
    trace["selectedFigureCount"] = len(figures)
    if not figures:
        trace.update(status="skipped", skipReason="no valid PNG, JPEG, or WebP figure was found in paper artifacts")
        return result

    if client is None:
        try:
            provider_info = get_settings().get_provider_info(provider_name, mask_key=True)
        except Exception as exc:
            trace.update(status="skipped", skipReason=f"provider configuration unavailable: {_clip(exc, 180)}")
            return result
        if not provider_info.get("configured"):
            trace.update(status="skipped", skipReason=f"provider '{provider_name}' has no configured API key")
            return result
        client = get_provider_client(provider_name)

    evidence_by_figure = _figure_evidence_map(evidence)
    claims_by_id = {claim.id: claim for claim in claims}
    for figure in figures:
        evidence_ids = _figure_evidence_ids(figure, evidence, evidence_by_figure)
        candidate_claims = _candidate_claims(claims, links, evidence_ids, figure)
        image_payload = _image_payload(figure, data_root=data_root)
        if not image_payload:
            trace["calls"].append({
                "task": "visual_figure_audit",
                "sourcePath": figure.get("sourcePath"),
                "status": "skipped",
                "error": "image failed final path, size, or signature validation",
            })
            continue

        prompt = _build_prompt(paper, figure, candidate_claims)
        message_content: List[Dict[str, Any]] = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": image_payload["dataUrl"]}},
        ]
        try:
            response = client.chat(
                messages=[ChatMessage(role="user", content=message_content)],
                model=model,
                temperature=0,
                max_tokens=1800,
                structured_output=True,
                request_max_retries=0,
                timeout=float(os.getenv("FAROS_REVIEWX_VISUAL_TIMEOUT", "120")),
            )
            assessment = _normalize_assessment(
                _extract_json_object(response.text),
                valid_claim_ids={claim.id for claim in candidate_claims},
            )
            if not assessment:
                raise ValueError("vision model did not return the required JSON assessment")
            call_trace = {
                "task": "visual_figure_audit",
                "sourcePath": figure.get("sourcePath"),
                "imageSha256": image_payload["sha256"],
                "model": response.model,
                "provider": response.raw_provider,
                "latencyMs": response.latency_ms,
                "usage": response.usage,
                "finishReason": response.finish_reason,
                "status": "completed",
                "captionStatus": assessment.get("captionStatus"),
                "claimAssessmentCount": len(assessment.get("claimAssessments", [])),
                "anomalyCount": len(assessment.get("anomalies", [])),
            }
            trace["calls"].append(call_trace)
            trace["auditedFigureCount"] += 1
            trace["captionCheckCount"] += 1
            trace["estimatedTokenCost"] += int(response.usage.get("total_tokens", 0) or 0)
            _append_assessment(
                result,
                paper_id=str(paper.get("id") or ""),
                figure=figure,
                assessment=assessment,
                evidence_ids=evidence_ids,
                claims_by_id=claims_by_id,
                model=response.model,
            )
        except (ProviderError, ValueError) as exc:
            logger.warning("ReviewX visual audit skipped one figure: %s", exc)
            trace["calls"].append({
                "task": "visual_figure_audit",
                "sourcePath": figure.get("sourcePath"),
                "model": model,
                "provider": provider_name,
                "status": "failed_soft",
                "error": _clip(exc, 240),
            })
        except Exception as exc:
            logger.warning("ReviewX visual audit failed soft: %s", exc, exc_info=True)
            trace["calls"].append({
                "task": "visual_figure_audit",
                "sourcePath": figure.get("sourcePath"),
                "model": model,
                "provider": provider_name,
                "status": "failed_soft",
                "error": f"visual audit failed: {_clip(exc, 220)}",
            })

    trace["verificationCount"] = len(result.verifications)
    trace["checkCount"] = trace["captionCheckCount"] + trace["verificationCount"]
    trace["anomalyCount"] = len(result.findings)
    if trace["auditedFigureCount"]:
        trace.update(
            status="completed" if trace["auditedFigureCount"] == len(figures) else "partial",
            skipped=False,
            skipReason=None,
        )
    else:
        trace.update(status="failed_soft", skipped=True, skipReason="all selected visual audits failed safely")
    return result


def _select_figures(figures: Any, mode: str, *, data_root: Optional[str]) -> List[Dict[str, Any]]:
    if not isinstance(figures, list):
        return []
    limit = 3 if mode == "deep" else 1
    valid = [item for item in figures if isinstance(item, dict) and _image_payload(item, data_root=data_root)]
    valid.sort(key=lambda item: (
        0 if str(item.get("source", "")).startswith("paper") else 1,
        0 if _NUMERIC_OR_RESULT_RE.search(f"{item.get('caption', '')} {item.get('title', '')}") else 1,
        str(item.get("sourcePath") or ""),
    ))
    return valid[:limit]


def _image_payload(figure: Dict[str, Any], *, data_root: Optional[str]) -> Optional[Dict[str, str]]:
    root = os.path.realpath(data_root or str(get_data_dir()))
    path = os.path.realpath(str(figure.get("absolutePath") or ""))
    try:
        if os.path.commonpath([root, path]) != root:
            return None
    except ValueError:
        return None
    if not os.path.isfile(path):
        return None
    size = os.path.getsize(path)
    if size <= 0 or size > _MAX_IMAGE_BYTES:
        return None
    try:
        with open(path, "rb") as handle:
            payload = handle.read()
    except OSError:
        return None
    mime = _detect_mime(payload)
    expected_mime = str(figure.get("mimeType") or mime)
    if mime not in _ALLOWED_MIME_TYPES or expected_mime != mime:
        return None
    encoded = base64.b64encode(payload).decode("ascii")
    return {
        "dataUrl": f"data:{mime};base64,{encoded}",
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _detect_mime(payload: bytes) -> Optional[str]:
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if payload.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(payload) >= 12 and payload.startswith(b"RIFF") and payload[8:12] == b"WEBP":
        return "image/webp"
    return None


def _figure_evidence_map(evidence: List[Evidence]) -> Dict[str, List[str]]:
    result: Dict[str, List[str]] = {}
    for item in evidence:
        if item.evidenceType != "figure":
            continue
        keys = {
            str(item.metadata.get("figureId") or ""),
            str(item.sourcePath or ""),
        }
        for key in keys - {""}:
            result.setdefault(key, []).append(item.id)
    return result


def _figure_evidence_ids(
    figure: Dict[str, Any],
    evidence: List[Evidence],
    evidence_map: Dict[str, List[str]],
) -> List[str]:
    ids: List[str] = []
    for key in (str(figure.get("id") or ""), str(figure.get("sourcePath") or "")):
        for evidence_id in evidence_map.get(key, []):
            if evidence_id not in ids:
                ids.append(evidence_id)
    if ids:
        return ids[:5]
    for item in evidence:
        if item.evidenceType == "figure" and str(item.sourcePath) == str(figure.get("sourcePath")):
            ids.append(item.id)
    return ids[:5]


def _candidate_claims(
    claims: List[Claim],
    links: Dict[str, List[str]],
    evidence_ids: List[str],
    figure: Dict[str, Any],
) -> List[Claim]:
    evidence_set = set(evidence_ids)
    caption = str(figure.get("caption") or "")
    source_file = str(figure.get("sourceTexPath") or "")
    source_line = figure.get("sourceLine")
    nearby: List[Claim] = []
    if source_file and isinstance(source_line, int):
        nearby = [
            claim for claim in claims
            if claim.sourceSpan.file == source_file
            and isinstance(claim.sourceSpan.line, int)
            and abs(claim.sourceSpan.line - source_line) <= 12
            and _term_overlap(claim.text, caption) >= 2
        ]
    linked = [] if source_file else [
        claim for claim in claims
        if evidence_set.intersection(links.get(claim.id, []))
        and _term_overlap(claim.text, caption) >= 3
    ]
    fallback: List[Claim] = []
    if figure.get("experimentId"):
        fallback = [
            claim for claim in claims
            if claim not in nearby
            and claim not in linked
            and (claim.claimType == "performance" or _NUMERIC_OR_RESULT_RE.search(claim.text))
            and _term_overlap(claim.text, caption) >= 2
        ]
    ordered: List[Claim] = []
    seen_claim_ids: set[str] = set()
    for claim in [*nearby, *linked, *fallback]:
        if claim.id in seen_claim_ids:
            continue
        seen_claim_ids.add(claim.id)
        ordered.append(claim)
    ordered.sort(key=lambda claim: (
        0 if claim in nearby else 1,
        0 if claim in linked else 1,
        abs((claim.sourceSpan.line or 0) - source_line) if isinstance(source_line, int) else 9999,
        0 if claim.importance == "high" else 1,
        0 if claim.claimType == "performance" else 1,
        claim.id,
    ))
    return ordered[:5]


def _term_overlap(left: str, right: str) -> int:
    stopwords = {
        "about", "after", "against", "also", "and", "are", "baseline", "been", "being",
        "between", "figure", "for", "from", "held", "into", "method", "our", "paper",
        "proposed", "result", "set", "show", "shows", "that", "the", "their", "this", "using",
        "versus", "with",
    }
    left_terms = set(re.findall(r"[a-z0-9]+(?:\.[0-9]+)?", str(left).lower())) - stopwords
    right_terms = set(re.findall(r"[a-z0-9]+(?:\.[0-9]+)?", str(right).lower())) - stopwords
    return len(left_terms & right_terms)


def _build_prompt(paper: Dict[str, Any], figure: Dict[str, Any], claims: List[Claim]) -> str:
    claim_payload = [
        {
            "claimId": claim.id,
            "text": _clip(claim.text, 420),
            "type": claim.claimType,
            "section": claim.sourceSpan.section,
        }
        for claim in claims
    ]
    return f"""You are the Visual-CEM verifier inside an evidence-grounded AI Scientist system.
Inspect the attached scientific figure itself, not just its filename. Check whether visible axes,
units, legend, trends, values, uncertainty marks, and annotations agree with the supplied caption
and candidate paper claims. Do not infer hidden values. Mark unreadable details as requiring human
verification. Do not provide chain-of-thought; provide only concise observable evidence.

Important decision rules:
- A candidate claim may come from nearby paper text, but this one figure may not be intended to prove it.
- If the figure simply does not discuss a candidate claim, omit that claim from claimAssessments.
- Absence from one figure is not a contradiction or an unsupported scientific claim.
- Captions may legitimately state sample size, provenance, or analysis that is not printed in the plot area.
- Only report caption_mismatch when a visible label, value, direction, or legend directly conflicts with the caption.
- Use "contradicted" only when visible content directly conflicts with a value, direction, label, or caption.
- Ignore filenames and storage paths; they are not scientific evidence.

Audit checklist:
1. Read explicit values, labels, legend mappings, and uncertainty marks.
2. Compare numeric magnitude and direction with each genuinely related candidate claim.
3. Check the axis minimum and displayed range; flag axis_issue when truncation visually exaggerates a small effect.
4. Use claim_support_gap when the visible result is directionally compatible but too weak for wording such as
   "large", "substantial", or "significant".

Paper title: {_clip(paper.get('title', 'Untitled'), 240)}
Figure caption: {_clip(figure.get('caption', ''), 1200)}
Candidate claims:
{json.dumps(claim_payload, ensure_ascii=False, indent=2)}

Return one strict JSON object with this schema:
{{
  "chartType": "line | bar | scatter | table | diagram | image | mixed | unknown",
  "readable": true,
  "observations": ["short directly visible observation"],
  "captionStatus": "consistent | partially_consistent | contradicted | unverifiable",
  "captionRationale": "one sentence grounded in visible content",
  "claimAssessments": [
    {{
      "claimId": "one supplied claimId",
      "status": "supported | weakly_supported | contradicted | needs_human_verification",
      "verdict": "one sentence grounded in visible content",
      "confidence": 0.0
    }}
  ],
  "anomalies": [
    {{
      "type": "caption_mismatch | numeric_mismatch | trend_reversal | legend_mismatch | axis_issue | uncertainty_missing | unreadable_figure | claim_mismatch | claim_support_gap | other",
      "claimId": "supplied claimId or null",
      "severity": "blocker | major | minor | info",
      "description": "specific visible mismatch",
      "suggestedFix": "specific correction",
      "acceptanceCriterion": "observable condition that would resolve it",
      "confidence": 0.0
    }}
  ]
}}
"""


def _extract_json_object(text: str) -> Dict[str, Any]:
    cleaned = str(text or "").strip()
    if "```json" in cleaned:
        cleaned = cleaned.split("```json", 1)[1].split("```", 1)[0]
    elif "```" in cleaned:
        parts = cleaned.split("```")
        if len(parts) >= 3:
            cleaned = parts[1]
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", cleaned)
        if not match:
            return {}
        try:
            payload = json.loads(match.group())
        except json.JSONDecodeError:
            return {}
    return payload if isinstance(payload, dict) else {}


def _normalize_assessment(payload: Dict[str, Any], *, valid_claim_ids: set[str]) -> Dict[str, Any]:
    if not isinstance(payload, dict) or not any(
        key in payload for key in ("captionStatus", "claimAssessments", "anomalies", "observations")
    ):
        return {}
    caption_status = str(payload.get("captionStatus") or "unverifiable").lower()
    if caption_status not in {"consistent", "partially_consistent", "contradicted", "unverifiable"}:
        caption_status = "unverifiable"
    claim_assessments = []
    for item in payload.get("claimAssessments", []) or []:
        if not isinstance(item, dict):
            continue
        claim_id = str(item.get("claimId") or "")
        status = str(item.get("status") or "needs_human_verification").lower()
        if claim_id not in valid_claim_ids or status not in _CLAIM_STATUS:
            continue
        if status == "unsupported":
            status = "needs_human_verification"
        claim_assessments.append({
            "claimId": claim_id,
            "status": status,
            "verdict": _clip(item.get("verdict"), 600),
            "confidence": _confidence(item.get("confidence")),
        })
    anomalies = []
    for item in payload.get("anomalies", []) or []:
        if not isinstance(item, dict):
            continue
        anomaly_type = str(item.get("type") or "other").lower()
        claim_id = str(item.get("claimId") or "") or None
        severity = str(item.get("severity") or "minor").lower()
        if anomaly_type == "caption_mismatch" and not claim_id and caption_status != "contradicted":
            continue
        if anomaly_type == "uncertainty_missing" and not claim_id and severity in {"blocker", "major"}:
            severity = "minor"
        anomalies.append({
            "type": anomaly_type if anomaly_type in _ANOMALY_TYPES else "other",
            "claimId": claim_id if claim_id in valid_claim_ids else None,
            "severity": severity if severity in {"blocker", "major", "minor", "info"} else "minor",
            "description": _clip(item.get("description"), 700),
            "suggestedFix": _clip(item.get("suggestedFix"), 700),
            "acceptanceCriterion": _clip(item.get("acceptanceCriterion"), 500),
            "confidence": _confidence(item.get("confidence")),
        })
    return {
        "chartType": _clip(payload.get("chartType") or "unknown", 60),
        "readable": bool(payload.get("readable", False)),
        "observations": [_clip(item, 300) for item in (payload.get("observations", []) or []) if str(item).strip()][:8],
        "captionStatus": caption_status,
        "captionRationale": _clip(payload.get("captionRationale"), 600),
        "claimAssessments": claim_assessments[:8],
        "anomalies": anomalies[:8],
    }


def _append_assessment(
    result: VisualAuditResult,
    *,
    paper_id: str,
    figure: Dict[str, Any],
    assessment: Dict[str, Any],
    evidence_ids: List[str],
    claims_by_id: Dict[str, Claim],
    model: str,
) -> None:
    verifier_by_claim: Dict[str, str] = {}
    for item in assessment.get("claimAssessments", []):
        claim_id = item["claimId"]
        verifier_id = f"verify_visual_{len(result.verifications) + 1:03d}"
        verifier_by_claim[claim_id] = verifier_id
        result.verifications.append(EvidenceVerification(
            id=verifier_id,
            paperId=paper_id,
            claimId=claim_id,
            verifierType="visual_claim_consistency",
            supportStatus=item["status"],
            verdict=item["verdict"] or "The visual model could not provide a specific verdict.",
            evidenceIds=evidence_ids,
            confidence=item["confidence"],
            expectedEvidence=["visible figure content consistent with the paper claim"],
            observedEvidence=assessment.get("observations", [])[:5],
            diagnostics={
                "model": model,
                "sourcePath": figure.get("sourcePath"),
                "chartType": assessment.get("chartType"),
                "captionStatus": assessment.get("captionStatus"),
                "readable": assessment.get("readable"),
            },
        ))

    anomaly_claims: set[str] = set()
    for anomaly in _merge_related_anomalies(assessment.get("anomalies", [])):
        if anomaly["severity"] == "info":
            continue
        claim_id = anomaly.get("claimId")
        if claim_id:
            anomaly_claims.add(claim_id)
        _append_finding(
            result,
            paper_id=paper_id,
            figure=figure,
            claim=claims_by_id.get(claim_id or ""),
            anomaly=anomaly,
            evidence_ids=evidence_ids,
            verifier_id=verifier_by_claim.get(claim_id or ""),
            model=model,
        )

    if assessment.get("captionStatus") == "contradicted" and not assessment.get("anomalies"):
        _append_finding(
            result,
            paper_id=paper_id,
            figure=figure,
            claim=None,
            anomaly={
                "type": "caption_mismatch",
                "claimId": None,
                "severity": "major",
                "description": assessment.get("captionRationale") or "The visible figure contradicts its caption.",
                "suggestedFix": "Correct the caption or regenerate the figure from the cited experiment artifact.",
                "acceptanceCriterion": "A reviewer can map every caption statement to visible labels, values, and trends.",
                "confidence": 0.8,
            },
            evidence_ids=evidence_ids,
            verifier_id=None,
            model=model,
        )

    for item in assessment.get("claimAssessments", []):
        if item["claimId"] in anomaly_claims:
            continue
        claim = claims_by_id.get(item["claimId"])
        if item["status"] == "contradicted":
            anomaly_type = "claim_mismatch"
            severity = "major"
            suggested_fix = "Correct the figure, regenerate it from the audited metrics, or revise the paper claim."
            acceptance = "The regenerated figure and exact claim agree on direction, values, labels, and uncertainty."
        elif (
            item["status"] == "weakly_supported"
            and item["confidence"] >= 0.6
            and claim
            and (claim.importance == "high" or claim.claimType == "performance")
        ):
            anomaly_type = "claim_support_gap"
            severity = "minor"
            suggested_fix = "Report the visible effect size precisely, soften the claim, or add the missing statistical evidence."
            acceptance = "The claim strength matches the visible effect size and its stated uncertainty."
        else:
            continue
        _append_finding(
            result,
            paper_id=paper_id,
            figure=figure,
            claim=claim,
            anomaly={
                "type": anomaly_type,
                "claimId": item["claimId"],
                "severity": severity,
                "description": item["verdict"],
                "suggestedFix": suggested_fix,
                "acceptanceCriterion": acceptance,
                "confidence": item["confidence"],
            },
            evidence_ids=evidence_ids,
            verifier_id=verifier_by_claim.get(item["claimId"]),
            model=model,
        )


def _merge_related_anomalies(anomalies: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Collapse duplicate descriptions of one claim/figure mismatch into one action."""

    mismatch_types = {
        "caption_mismatch",
        "numeric_mismatch",
        "trend_reversal",
        "legend_mismatch",
        "claim_mismatch",
        "claim_support_gap",
    }
    type_priority = {
        "numeric_mismatch": 0,
        "trend_reversal": 1,
        "legend_mismatch": 2,
        "caption_mismatch": 3,
        "claim_mismatch": 4,
        "claim_support_gap": 5,
    }
    severity_rank = {"blocker": 0, "major": 1, "minor": 2, "info": 3}
    grouped: Dict[tuple[str, str], List[Dict[str, Any]]] = {}
    for index, anomaly in enumerate(anomalies):
        claim_id = str(anomaly.get("claimId") or "")
        anomaly_type = str(anomaly.get("type") or "other")
        group_name = "claim_consistency" if claim_id and anomaly_type in mismatch_types else f"item_{index}"
        grouped.setdefault((claim_id, group_name), []).append(anomaly)

    merged: List[Dict[str, Any]] = []
    for items in grouped.values():
        primary = min(
            items,
            key=lambda item: (
                severity_rank.get(str(item.get("severity")), 9),
                type_priority.get(str(item.get("type")), 9),
                -float(item.get("confidence") or 0),
            ),
        )
        combined = dict(primary)
        if len(items) > 1:
            descriptions = list(dict.fromkeys(str(item.get("description") or "") for item in items if item.get("description")))
            criteria = list(dict.fromkeys(str(item.get("acceptanceCriterion") or "") for item in items if item.get("acceptanceCriterion")))
            combined["description"] = " ".join(descriptions)
            combined["acceptanceCriterion"] = " ".join(criteria)
            combined["confidence"] = max(float(item.get("confidence") or 0.5) for item in items)
        merged.append(combined)
    return merged


def _append_finding(
    result: VisualAuditResult,
    *,
    paper_id: str,
    figure: Dict[str, Any],
    claim: Optional[Claim],
    anomaly: Dict[str, Any],
    evidence_ids: List[str],
    verifier_id: Optional[str],
    model: str,
) -> None:
    finding_id = f"finding_visual_{len(result.findings) + 1:03d}"
    anomaly_type = str(anomaly.get("type") or "other")
    title = {
        "caption_mismatch": "Figure content conflicts with its caption",
        "numeric_mismatch": "Figure value conflicts with the reported result",
        "trend_reversal": "Figure trend conflicts with the reported direction",
        "legend_mismatch": "Figure legend does not support the stated comparison",
        "axis_issue": "Figure axis design weakens result interpretability",
        "uncertainty_missing": "Figure omits uncertainty needed for the claim",
        "unreadable_figure": "Figure cannot be audited at its current resolution",
        "claim_mismatch": "Figure content conflicts with a paper claim",
        "claim_support_gap": "Figure evidence is weaker than the paper claim",
    }.get(anomaly_type, "Visual evidence requires revision")
    suggested_fix = str(anomaly.get("suggestedFix") or "Regenerate the figure from audited data and align the caption and claim.")
    acceptance = str(anomaly.get("acceptanceCriterion") or "The figure, caption, and linked claim agree under visual re-audit.")
    confidence = _confidence(anomaly.get("confidence"))
    finding = Finding(
        id=finding_id,
        paperId=paper_id,
        claimId=claim.id if claim else None,
        severity=str(anomaly.get("severity") or "minor"),
        riskType=f"visual_{anomaly_type}",
        title=title,
        description=str(anomaly.get("description") or "The vision audit identified a figure-level inconsistency."),
        evidenceIds=evidence_ids,
        targetModule="experiments",
        suggestedFix=suggested_fix,
        confidence=confidence,
        location={
            "section": claim.sourceSpan.section if claim else "Figures",
            "sourcePath": figure.get("sourcePath"),
        },
        supportStatus="contradicted" if anomaly_type in {
            "caption_mismatch", "numeric_mismatch", "trend_reversal", "legend_mismatch", "claim_mismatch"
        } else "needs_human_verification",
        verifierIds=[verifier_id] if verifier_id else [],
        reviewerDecision="valid",
        reviewerAssessment=f"Visual-CEM observed this issue directly in the attached figure using {model}.",
        reviewerModel=model,
        cemCalibration={
            "visualEvidence": {
                "model": model,
                "sourcePath": figure.get("sourcePath"),
                "anomalyType": anomaly_type,
                "acceptanceCriterion": acceptance,
            },
        },
    )
    result.findings.append(finding)
    result.risk_nodes.append(RiskNode(
        id=f"risk_visual_{len(result.risk_nodes) + 1:03d}",
        question=f"Does the regenerated figure resolve {title.lower()}?",
        claimIds=[claim.id] if claim else [],
        riskScore=confidence,
        status="needs_deep_review" if finding.severity in {"blocker", "major"} else "needs_traceability",
        assignedModel=model,
        level=2,
        category="visual_evidence",
        findingIds=[finding.id],
        evidenceIds=evidence_ids,
        supportCounts={finding.supportStatus or "unknown": 1},
        expansionPolicy="visual_cem_targeted_audit",
        mismatchDrivers=[anomaly_type],
    ))


def _confidence(value: Any) -> float:
    try:
        return round(max(0.0, min(1.0, float(value))), 2)
    except (TypeError, ValueError):
        return 0.5


def _clip(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."
