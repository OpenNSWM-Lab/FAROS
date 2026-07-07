"""Build a lightweight claim-evidence graph from FAROS artifacts."""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Tuple

from app.modules.review.reviewx_models import Claim, Evidence


_STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "into", "while", "using",
    "method", "approach", "framework", "system", "paper", "model", "models", "results",
}
_NUMERIC_RE = re.compile(r"\b\d+(\.\d+)?\s*(%|percent|x|times|k|ms|s|tokens?|accuracy|f1|auc)\b", re.IGNORECASE)


def _keywords(text: str) -> set[str]:
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9_-]{3,}", text.lower())
    return {t for t in tokens if t not in _STOPWORDS}


def _metric_entries(metrics: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(metrics, list):
        for item in metrics:
            if isinstance(item, dict):
                yield item
    elif isinstance(metrics, dict):
        for key, value in metrics.items():
            if isinstance(value, dict):
                yield {"key": key, **value}
            else:
                yield {"key": key, "value": value}


def build_evidence(artifacts: Dict[str, Any]) -> List[Evidence]:
    paper = artifacts["paper"]
    paper_id = paper["id"]
    evidence: List[Evidence] = []

    brief = paper.get("briefJson") or {}
    if isinstance(brief, dict):
        if brief.get("research_question"):
            evidence.append(Evidence(
                id=f"evidence_{len(evidence) + 1:03d}",
                paperId=paper_id,
                evidenceType="brief",
                sourceModule="idea",
                sourcePath="paper.meta.json.briefJson.research_question",
                summary=str(brief["research_question"]),
                confidence=0.75,
                metadata={"field": "research_question"},
            ))
        for idx, item in enumerate(brief.get("must_use_evidence", []) or []):
            evidence.append(Evidence(
                id=f"evidence_{len(evidence) + 1:03d}",
                paperId=paper_id,
                evidenceType="required_evidence",
                sourceModule="idea",
                sourcePath=f"paper.meta.json.briefJson.must_use_evidence[{idx}]",
                summary=str(item),
                confidence=0.9,
                metadata={"field": "must_use_evidence", "index": idx},
            ))

    for latex_file in artifacts.get("latexFiles", []):
        content = latex_file.get("content", "")
        citations = re.findall(r"\\cite[p|t]?\{([^}]+)\}", content)
        if citations:
            evidence.append(Evidence(
                id=f"evidence_{len(evidence) + 1:03d}",
                paperId=paper_id,
                evidenceType="citation",
                sourceModule="paper",
                sourcePath=latex_file["path"],
                summary=f"{len(citations)} citation commands found in {latex_file['path']}: {', '.join(citations[:8])}",
                confidence=0.7,
                metadata={"citationCount": len(citations), "sample": citations[:8]},
            ))
        if "\\bibliography" in content or latex_file["path"].endswith(".bib"):
            evidence.append(Evidence(
                id=f"evidence_{len(evidence) + 1:03d}",
                paperId=paper_id,
                evidenceType="bibliography",
                sourceModule="paper",
                sourcePath=latex_file["path"],
                summary=f"Bibliography material is present in {latex_file['path']}.",
                confidence=0.75,
            ))

    for exp in artifacts.get("experiments", []):
        record = exp.get("record") or {}
        evidence.append(Evidence(
            id=f"evidence_{len(evidence) + 1:03d}",
            paperId=paper_id,
            evidenceType="experiment_record",
            sourceModule="experiment",
            sourcePath=f"{exp['path']}/experiment.json",
            summary=f"Experiment {exp['id']} status={record.get('status', 'unknown')}: {record.get('description', '')[:240]}",
            confidence=0.7,
            metadata={"experimentId": exp["id"], "status": record.get("status")},
        ))
        for metric in _metric_entries(exp.get("metrics", [])):
            key = str(metric.get("key") or metric.get("name") or "metric")
            value = metric.get("value", metric.get("mean"))
            evidence.append(Evidence(
                id=f"evidence_{len(evidence) + 1:03d}",
                paperId=paper_id,
                evidenceType="metric",
                sourceModule="experiment",
                sourcePath=f"{exp['path']}/metrics.json",
                summary=f"{key} = {value}",
                confidence=0.85,
                metadata={"experimentId": exp["id"], "metricName": key, "value": value},
            ))
        if exp.get("report"):
            evidence.append(Evidence(
                id=f"evidence_{len(evidence) + 1:03d}",
                paperId=paper_id,
                evidenceType="experiment_report",
                sourceModule="experiment",
                sourcePath=f"{exp['path']}/experiment_report.md",
                summary=exp["report"][:500],
                confidence=0.8,
                metadata={"experimentId": exp["id"]},
            ))
        for fig in exp.get("figures", []) or []:
            evidence.append(Evidence(
                id=f"evidence_{len(evidence) + 1:03d}",
                paperId=paper_id,
                evidenceType="figure",
                sourceModule="experiment",
                sourcePath=str(fig.get("pathPng") or fig.get("fileNamePng") or "figures.json"),
                summary=str(fig.get("caption") or fig.get("title") or "Generated figure"),
                confidence=0.75,
                metadata={"experimentId": exp["id"], "figureId": fig.get("id")},
            ))

    for artifact in artifacts.get("codeArtifacts", []):
        evidence.append(Evidence(
            id=f"evidence_{len(evidence) + 1:03d}",
            paperId=paper_id,
            evidenceType="code_artifact",
            sourceModule="code",
            sourcePath=artifact["path"],
            summary=f"Code artifact {artifact['name']} is available.",
            confidence=0.65,
            metadata={"name": artifact["name"]},
        ))

    return evidence


def link_claims_to_evidence(claims: List[Claim], evidence: List[Evidence]) -> Dict[str, List[str]]:
    links: Dict[str, List[str]] = {}
    for claim in claims:
        claim_keywords = _keywords(claim.text)
        scored: List[Tuple[int, str]] = []
        for ev in evidence:
            ev_keywords = _keywords(ev.summary + " " + ev.sourcePath)
            overlap = len(claim_keywords & ev_keywords)
            if claim.claimType == "performance" and ev.evidenceType == "metric":
                overlap += 3
            if claim.claimType == "method" and ev.sourceModule in {"idea", "code"}:
                overlap += 1
            if _NUMERIC_RE.search(claim.text) and ev.evidenceType == "metric":
                overlap += 2
            if overlap > 0:
                scored.append((overlap, ev.id))
        scored.sort(reverse=True)
        links[claim.id] = [ev_id for _score, ev_id in scored[:5]]
    return links
