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
_PLAIN_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9_.])\d+(?:\.\d+)?")
_BIB_ENTRY_RE = re.compile(r"@\w+\s*\{\s*([^,\s]+)\s*,([\s\S]*?)\n\}", re.MULTILINE)
_BIB_FIELD_RE = re.compile(r"(\w+)\s*=\s*[\{\"]([\s\S]*?)[\}\"]\s*,?", re.MULTILINE)
_BBL_ENTRY_RE = re.compile(
    r"\\bibitem(?:\[[\s\S]*?\])?\{([^}]+)\}([\s\S]*?)(?=\\bibitem|\\end\{thebibliography\})",
    re.MULTILINE,
)


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


def _numeric_values(text: str) -> list[float]:
    values: list[float] = []
    for match in _PLAIN_NUMBER_RE.finditer(text):
        value = float(match.group(0))
        suffix = text[match.end():match.end() + 8].lstrip().lower()
        values.append(value / 100 if suffix.startswith(("%", "percent")) else value)
    return values


def _bib_entries(content: str) -> Iterable[Dict[str, str]]:
    for match in _BIB_ENTRY_RE.finditer(content):
        key = match.group(1).strip()
        body = match.group(2)
        fields = {name.lower(): re.sub(r"\s+", " ", value).strip() for name, value in _BIB_FIELD_RE.findall(body)}
        title = fields.get("title")
        if not key or not title:
            continue
        yield {
            "key": key,
            "title": title,
            "venue": fields.get("booktitle") or fields.get("journal") or "",
            "year": fields.get("year") or "",
            "author": fields.get("author") or "",
            "abstract": fields.get("abstract") or "",
            "note": fields.get("note") or "",
            "url": fields.get("url") or "",
            "doi": fields.get("doi") or "",
            "keywords": fields.get("keywords") or fields.get("keyword") or "",
        }


def _clean_latex_text(value: str) -> str:
    value = re.sub(r"\\(?:emph|textit|textbf|url|doi)\{([^{}]*)\}", r"\1", value)
    value = re.sub(r"\\[A-Za-z]+(?:\[[^\]]*\])?", " ", value)
    value = value.replace("{", "").replace("}", "").replace("~", " ")
    return re.sub(r"\s+", " ", value).strip(" .,:;\n\t")


def _bbl_entries(content: str) -> Iterable[Dict[str, str]]:
    for match in _BBL_ENTRY_RE.finditer(content):
        key = match.group(1).strip()
        body = match.group(2)
        blocks = re.split(r"\\newblock\s*", body)
        if len(blocks) < 2:
            continue
        author = _clean_latex_text(blocks[0])
        title = _clean_latex_text(blocks[1])
        publication = _clean_latex_text(" ".join(blocks[2:]))
        year_match = re.search(r"\b(?:19|20)\d{2}\b", publication)
        url_match = re.search(r"\\url\{([^{}]+)\}", body)
        if not key or not title:
            continue
        yield {
            "key": key,
            "title": title,
            "venue": publication,
            "year": year_match.group(0) if year_match else "",
            "author": author,
            "abstract": "",
            "note": "Imported from compiled BibTeX .bbl output.",
            "url": url_match.group(1).strip() if url_match else "",
            "doi": "",
            "keywords": "",
        }


def build_evidence(artifacts: Dict[str, Any]) -> List[Evidence]:
    paper = artifacts["paper"]
    paper_id = paper["id"]
    evidence: List[Evidence] = []
    seen_citation_keys: set[str] = set()
    visual_by_figure_id = {
        str(item.get("id")): item
        for item in artifacts.get("visualFigures", [])
        if item.get("id")
    }
    represented_visual_ids: set[str] = set()

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

    latex_files = sorted(
        artifacts.get("latexFiles", []),
        key=lambda item: (
            0 if str(item.get("path", "")).endswith(".bib") else
            2 if str(item.get("path", "")).endswith(".bbl") else 1,
            str(item.get("path", "")),
        ),
    )
    for latex_file in latex_files:
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
        if "\\bibliography" in content or latex_file["path"].endswith((".bib", ".bbl")):
            evidence.append(Evidence(
                id=f"evidence_{len(evidence) + 1:03d}",
                paperId=paper_id,
                evidenceType="bibliography",
                sourceModule="paper",
                sourcePath=latex_file["path"],
                summary=f"Bibliography material is present in {latex_file['path']}.",
                confidence=0.75,
            ))
        if latex_file["path"].endswith((".bib", ".bbl")):
            entries = _bib_entries(content) if latex_file["path"].endswith(".bib") else _bbl_entries(content)
            for entry in entries:
                normalized_key = entry["key"].strip().lower()
                if normalized_key in seen_citation_keys:
                    continue
                seen_citation_keys.add(normalized_key)
                evidence.append(Evidence(
                    id=f"evidence_{len(evidence) + 1:03d}",
                    paperId=paper_id,
                    evidenceType="citation_entry",
                    sourceModule="paper",
                    sourcePath=f"{latex_file['path']}#{entry['key']}",
                    summary=(
                        f"{entry['key']}: {entry['title']}"
                        + (f" ({entry['venue']}, {entry['year']})" if entry.get("venue") or entry.get("year") else "")
                    ),
                    confidence=0.82,
                    metadata={
                        "citationKey": entry["key"],
                        "title": entry["title"],
                        "venue": entry.get("venue", ""),
                        "year": entry.get("year", ""),
                        "author": entry.get("author", ""),
                        "abstract": entry.get("abstract", ""),
                        "note": entry.get("note", ""),
                        "url": entry.get("url", ""),
                        "doi": entry.get("doi", ""),
                        "keywords": entry.get("keywords", ""),
                    },
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
            visual = visual_by_figure_id.get(str(fig.get("id") or ""))
            if visual:
                represented_visual_ids.add(str(visual["id"]))
            evidence.append(Evidence(
                id=f"evidence_{len(evidence) + 1:03d}",
                paperId=paper_id,
                evidenceType="figure",
                sourceModule="experiment",
                sourcePath=str(
                    visual.get("sourcePath")
                    if visual
                    else fig.get("pathPng") or fig.get("fileNamePng") or "figures.json"
                ),
                summary=str(fig.get("caption") or fig.get("title") or "Generated figure"),
                confidence=0.75,
                metadata={
                    "experimentId": exp["id"],
                    "figureId": fig.get("id"),
                    "visualAuditEligible": bool(visual),
                    "mimeType": visual.get("mimeType") if visual else None,
                },
            ))

    for visual in artifacts.get("visualFigures", []):
        visual_id = str(visual.get("id") or "")
        if not visual_id or visual_id in represented_visual_ids:
            continue
        evidence.append(Evidence(
            id=f"evidence_{len(evidence) + 1:03d}",
            paperId=paper_id,
            evidenceType="figure",
            sourceModule="paper" if str(visual.get("source", "")).startswith("paper") else "experiment",
            sourcePath=str(visual.get("sourcePath") or "figure"),
            summary=str(visual.get("caption") or visual.get("title") or "Paper figure"),
            confidence=0.78,
            metadata={
                "experimentId": visual.get("experimentId"),
                "figureId": visual_id,
                "visualAuditEligible": True,
                "mimeType": visual.get("mimeType"),
                "visualSource": visual.get("source"),
            },
        ))

    structured_evidence = artifacts.get("experimentEvidence") or {}
    if structured_evidence:
        evidence.append(Evidence(
            id=f"evidence_{len(evidence) + 1:03d}",
            paperId=paper_id,
            evidenceType="experiment_evidence",
            sourceModule="experiment",
            sourcePath="code_project/artifacts/evidence/experiment_evidence.json",
            summary=(
                f"ExperimentEvidence status={structured_evidence.get('status', 'unknown')}; "
                f"codeHash={structured_evidence.get('codeHash', '')}; "
                f"environmentHash={structured_evidence.get('environmentHash', '')}"
            ),
            confidence=0.95,
            metadata={
                "status": structured_evidence.get("status"),
                "codeRunId": structured_evidence.get("codeRunId"),
                "failures": structured_evidence.get("failures", []),
            },
        ))
        existing_metric_names = {
            str(item.metadata.get("metricName") or "").lower()
            for item in evidence
            if item.evidenceType == "metric"
        }
        for metric in _metric_entries(structured_evidence.get("metrics", [])):
            key = str(metric.get("key") or metric.get("name") or "metric")
            if key.lower() in existing_metric_names:
                continue
            value = metric.get("value", metric.get("mean"))
            evidence.append(Evidence(
                id=f"evidence_{len(evidence) + 1:03d}",
                paperId=paper_id,
                evidenceType="metric",
                sourceModule="experiment",
                sourcePath=str(metric.get("sourcePath") or "experiment_evidence.json"),
                summary=f"{key} = {value}: {metric.get('definition', '')}",
                confidence=0.95,
                metadata={
                    "metricName": key,
                    "value": value,
                    "definition": metric.get("definition", ""),
                    "split": metric.get("split", ""),
                    "codeRunId": structured_evidence.get("codeRunId"),
                },
            ))
            existing_metric_names.add(key.lower())

        metric_audit = structured_evidence.get("metricAudit") or {}
        if metric_audit and metric_audit.get("status") != "not_applicable":
            audit_errors = [str(item) for item in metric_audit.get("errors", [])]
            evidence.append(Evidence(
                id=f"evidence_{len(evidence) + 1:03d}",
                paperId=paper_id,
                evidenceType="metric_audit",
                sourceModule="experiment",
                sourcePath=str(metric_audit.get("sourcePath") or "evaluation_records.json"),
                summary=(
                    f"Independent metric audit status={metric_audit.get('status', 'unknown')}; "
                    f"positiveClass={metric_audit.get('positiveClass', '')}; "
                    f"records={metric_audit.get('recordCount', 0)}"
                    + (f"; errors={' | '.join(audit_errors[:3])}" if audit_errors else "")
                ),
                confidence=0.99,
                metadata=metric_audit,
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
        claim_text = claim.text.lower()
        claim_numbers = _numeric_values(claim.text)
        scored: List[Tuple[int, str]] = []
        for ev in evidence:
            ev_keywords = _keywords(ev.summary + " " + ev.sourcePath)
            overlap = len(claim_keywords & ev_keywords)
            if claim.claimType == "performance" and ev.evidenceType == "metric":
                overlap += 3
            if claim.claimType == "performance" and ev.evidenceType == "metric_audit":
                overlap += 4
            if claim.claimType == "method" and ev.sourceModule in {"idea", "code"}:
                overlap += 1
            if _NUMERIC_RE.search(claim.text) and ev.evidenceType == "metric":
                overlap += 2
            if ev.evidenceType == "metric":
                metric_name = str(ev.metadata.get("metricName") or "").lower()
                if "baseline" in claim_text and "baseline" in metric_name:
                    overlap += 6
                if any(term in claim_text for term in ("method", "proposed", "ours", "our ")):
                    if any(term in metric_name for term in ("method", "proposed", "ours")):
                        overlap += 5
                try:
                    metric_value = float(ev.metadata.get("value"))
                except (TypeError, ValueError):
                    metric_value = None
                if metric_value is not None and any(
                    abs(metric_value - claim_value) <= max(1e-8, abs(metric_value) * 1e-5)
                    for claim_value in claim_numbers
                ):
                    overlap += 8
            if overlap > 0:
                scored.append((overlap, ev.id))
        scored.sort(reverse=True)
        links[claim.id] = [ev_id for _score, ev_id in scored[:8]]
    return links
