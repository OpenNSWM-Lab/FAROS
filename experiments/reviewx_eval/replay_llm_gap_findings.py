#!/usr/bin/env python3
"""Replay stored ReviewX LLM gap findings through the current merge/ranking logic."""

from __future__ import annotations

import argparse
import copy
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.modules.review.model_router import (  # noqa: E402
    _apply_additional_findings,
    rank_findings_for_review,
)
from app.modules.review.reviewx_models import Claim, Finding, SourceSpan  # noqa: E402


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def claim_from_score(score: dict[str, Any], paper_id: str) -> Claim:
    source = score.get("sourceSpan") or {}
    return Claim(
        id=str(score.get("claimId") or ""),
        paperId=paper_id,
        text=str(score.get("text") or ""),
        claimType=str(score.get("claimType") or "content"),
        importance=str(score.get("importance") or "medium"),
        requiresEvidence=bool(score.get("requiresEvidence", True)),
        sourceSpan=SourceSpan(
            file=str(source.get("file") or "main.tex"),
            section=str(source.get("section") or "Unknown"),
            line=source.get("line"),
        ),
        riskHints=list(score.get("riskHints") or []),
    )


def finding_from_dict(item: dict[str, Any], paper_id: str) -> Finding:
    return Finding(
        id=str(item.get("id") or ""),
        paperId=str(item.get("paperId") or paper_id),
        claimId=item.get("claimId"),
        severity=str(item.get("severity") or "info"),
        riskType=str(item.get("riskType") or "unsupported_claim"),
        title=str(item.get("title") or "Review finding"),
        description=str(item.get("description") or ""),
        evidenceIds=list(item.get("evidenceIds") or []),
        targetModule=str(item.get("targetModule") or "papers"),
        suggestedFix=str(item.get("suggestedFix") or ""),
        confidence=float(item.get("confidence", 0) or 0),
        location=item.get("location"),
        supportStatus=item.get("supportStatus"),
        verifierIds=list(item.get("verifierIds") or []),
        reviewerDecision=item.get("reviewerDecision"),
        reviewerAssessment=item.get("reviewerAssessment"),
        reviewerModel=item.get("reviewerModel"),
        cemCalibration=dict(item.get("cemCalibration") or {}),
        revisionRequestIds=list(item.get("revisionRequestIds") or []),
        revisionStatus=item.get("revisionStatus"),
    )


def refresh_summary(record: dict[str, Any], findings: list[Finding]) -> None:
    summary = record.setdefault("summary", {})
    summary["findingCount"] = len(findings)
    summary["severityCounts"] = dict(Counter(finding.severity for finding in findings))


def refresh_claim_finding_ids(record: dict[str, Any], findings: list[Finding]) -> None:
    ids_by_claim: dict[str, list[str]] = {}
    for finding in findings:
        if finding.claimId:
            ids_by_claim.setdefault(finding.claimId, []).append(finding.id)
    for score in record.get("claimScores") or []:
        score["findingIds"] = ids_by_claim.get(str(score.get("claimId") or ""), [])


def replay_record(record: dict[str, Any], method: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    updated = copy.deepcopy(record)
    paper_id = str(updated.get("paperId") or "")
    claims = [claim_from_score(score, paper_id) for score in updated.get("claimScores") or []]
    findings = [finding_from_dict(item, paper_id) for item in updated.get("findings") or []]
    model_trace = updated.setdefault("modelTrace", {})
    routing = model_trace.setdefault("llmRouting", {})
    additional = routing.get("llmAdditionalFindings") or []
    model = str(routing.get("requestedModel") or updated.get("model") or "unknown")
    applications = _apply_additional_findings(findings, additional, claims, model, paper_id)
    findings = rank_findings_for_review(findings, routing)

    routing["llmAdditionalFindingApplications"] = applications
    model_trace["postHocReplay"] = {
        "enabled": True,
        "sourceMethod": updated.get("method"),
        "operation": "stored_llm_gap_merge_and_rerank",
        "independentValidation": False,
    }
    updated["method"] = method
    updated["findings"] = [finding.to_dict() for finding in findings]
    updated.setdefault("methodConfig", {})["postHocReplay"] = True
    updated["methodConfig"]["llmGapMerge"] = True
    refresh_summary(updated, findings)
    refresh_claim_finding_ids(updated, findings)
    return updated, applications


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--method", default="reviewx_llm_gap_merge_replay")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    if input_path.resolve() == output_path.resolve():
        raise ValueError("input and output must be different files")

    rows, outcomes = [], Counter()
    for record in read_jsonl(input_path):
        updated, applications = replay_record(record, args.method)
        rows.append(updated)
        outcomes.update(str(item.get("outcome") or "unknown") for item in applications)
    write_jsonl(output_path, rows)
    print(
        f"runs={len(rows)} applied={sum(outcomes.values())} "
        f"merged={outcomes['merged']} added={outcomes['added']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
