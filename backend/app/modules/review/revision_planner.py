"""Convert ReviewX findings into FAROS action items."""

from __future__ import annotations

from typing import Any, Dict, List

from app.modules.review.reviewx_models import Finding


def findings_to_action_items(findings: List[Finding]) -> List[Dict[str, Any]]:
    priority = {"blocker": 0, "major": 1, "minor": 2, "info": 3}
    ordered = sorted(findings, key=lambda f: (priority.get(f.severity, 9), -f.confidence))
    items: List[Dict[str, Any]] = []
    for finding in ordered[:20]:
        items.append({
            "description": finding.title,
            "section": (finding.location or {}).get("section", "Paper"),
            "severity": finding.severity.upper(),
            "targetModule": finding.targetModule,
            "suggestedEdit": finding.suggestedFix,
            "sourceFindingId": finding.id,
            "claimId": finding.claimId,
            "evidenceIds": finding.evidenceIds,
            "riskType": finding.riskType,
            "confidence": finding.confidence,
            "supportStatus": finding.supportStatus,
            "verifierIds": finding.verifierIds,
            "acceptanceCriteria": _acceptance_criteria(finding),
        })
    return items


def _acceptance_criteria(finding: Finding) -> List[str]:
    if finding.targetModule == "experiments":
        return [
            "A metrics.json or experiment report artifact exists for the requested evidence.",
            "The paper claim cites the exact metric, baseline, or run identifier.",
            "If the metric does not support the claim, the claim is weakened or removed.",
        ]
    if finding.targetModule == "code":
        return [
            "A code export, run log, or reproducibility manifest is attached.",
            "The paper describes how the artifact supports the method claim.",
        ]
    return [
        "The affected section is updated at the cited location.",
        "The revised claim includes evidence, citation, or a clear limitation statement.",
    ]
