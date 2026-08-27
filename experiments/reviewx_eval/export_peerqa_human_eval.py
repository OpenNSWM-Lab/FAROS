#!/usr/bin/env python3
"""Align PeerQA expert questions with ReviewX findings for blind human review."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import re
from pathlib import Path
from typing import Any


OUTPUT_FIELDS = [
    "annotationId", "method", "sampleId", "paperId", "sourcePaperId",
    "findingId", "claimId", "claimText", "claimSection", "riskType",
    "supportStatus", "severity", "confidence", "reviewerDecision",
    "reviewerAssessment", "findingTitle", "findingDescription", "suggestedFix",
    "expertReviewerQuestion", "authorAnswerable", "authorAnswer",
    "referenceEvidence", "automaticMatchScore", "automaticCoverageCandidate",
    "humanCorrectness", "humanActionability", "humanSpecificity",
    "humanGrounding", "humanSeverityAgreement", "humanNotes",
]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def tokens(text: str) -> set[str]:
    return {token for token in re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{2,}", text.lower())}


def jaccard(left: str, right: str) -> float:
    a, b = tokens(left), tokens(right)
    return len(a & b) / len(a | b) if a and b else 0.0


def candidate_score(reference: dict[str, Any], finding: dict[str, Any], claim: dict[str, Any]) -> float:
    question = str(reference.get("reviewerQuestion") or "")
    evidence = " ".join(str(item) for item in reference.get("evidenceSentences", []) or [])
    claim_text = str(claim.get("text") or "")
    finding_text = " ".join(str(finding.get(key) or "") for key in (
        "title", "description", "suggestedFix", "reviewerAssessment",
    ))
    return max(
        jaccard(question, claim_text),
        jaccard(evidence, claim_text),
        jaccard(question, finding_text),
        0.6 * jaccard(question + " " + evidence, claim_text + " " + finding_text),
    )


def best_candidate(
    reference: dict[str, Any],
    record: dict[str, Any],
    max_findings: int = 0,
) -> tuple[float, dict[str, Any], dict[str, Any]]:
    claims = {str(row.get("claimId")): row for row in record.get("claimScores", [])}
    candidates = []
    findings = list(record.get("findings", []))
    if max_findings > 0:
        findings = findings[:max_findings]
    for finding in findings:
        claim = claims.get(str(finding.get("claimId") or ""), {})
        candidates.append((candidate_score(reference, finding, claim), finding, claim))
    if candidates:
        return max(candidates, key=lambda item: item[0])
    claim_candidates = [
        (jaccard(str(reference.get("reviewerQuestion") or ""), str(claim.get("text") or "")), {}, claim)
        for claim in record.get("claimScores", [])
    ]
    return max(claim_candidates, key=lambda item: item[0]) if claim_candidates else (0.0, {}, {})


def assessment_text(
    finding: dict[str, Any],
    covered: bool,
) -> str:
    reviewx = str(finding.get("reviewerAssessment") or finding.get("description") or "")
    if not covered:
        return "未找到达到自动匹配阈值的 ReviewX finding；请人工判断是否确实未覆盖。"
    return reviewx or "ReviewX 未提供详细判断。"


def build_rows(
    records: list[dict[str, Any]],
    references: list[dict[str, Any]],
    threshold: float,
    max_findings: int = 0,
) -> list[dict[str, Any]]:
    records_by_sample = {str(row.get("sampleId")): row for row in records}
    rows = []
    for reference in references:
        record = records_by_sample.get(str(reference.get("sampleId")))
        if not record:
            continue
        score, finding, claim = best_candidate(reference, record, max_findings=max_findings)
        covered = bool(finding) and score >= threshold
        row = {
            "annotationId": "peerqa_ann_" + hashlib.sha1(
                str(reference["referenceId"]).encode("utf-8")
            ).hexdigest()[:12],
            "method": record.get("method") or "reviewx",
            "sampleId": reference.get("sampleId"),
            "paperId": reference.get("paperId"),
            "sourcePaperId": reference.get("sourcePaperId"),
            "findingId": finding.get("id") if covered else "NO_MATCH",
            "claimId": claim.get("claimId") or "",
            "claimText": claim.get("text") or "未匹配到 ReviewX claim",
            "claimSection": (claim.get("sourceSpan") or {}).get("section") or "",
            "riskType": finding.get("riskType") if covered else "expert_question_not_covered",
            "supportStatus": finding.get("supportStatus") if covered else "not_detected",
            "severity": finding.get("severity") if covered else "unrated",
            "confidence": finding.get("confidence") if covered else "",
            "reviewerDecision": finding.get("reviewerDecision") if covered else "",
            "reviewerAssessment": assessment_text(finding, covered),
            "findingTitle": finding.get("title") if covered else "No automatic ReviewX match",
            "findingDescription": finding.get("description") if covered else "",
            "suggestedFix": finding.get("suggestedFix") if covered else "",
            "expertReviewerQuestion": reference.get("reviewerQuestion"),
            "authorAnswerable": reference.get("authorAnswerable"),
            "authorAnswer": reference.get("authorAnswer"),
            "referenceEvidence": " || ".join(reference.get("evidenceSentences", []) or []),
            "automaticMatchScore": round(score, 4),
            "automaticCoverageCandidate": covered,
            "humanCorrectness": "",
            "humanActionability": "",
            "humanSpecificity": "",
            "humanGrounding": "",
            "humanSeverityAgreement": "",
            "humanNotes": "",
        }
        rows.append(row)
    return rows


def blind_rows(rows: list[dict[str, Any]], seed: int) -> list[dict[str, Any]]:
    result = []
    for row in rows:
        copy = dict(row)
        for field, prefix in (("sampleId", "sample"), ("paperId", "paper")):
            digest = hashlib.sha1(f"{field}|{copy.get(field)}".encode("utf-8")).hexdigest()[:10]
            copy[field] = f"{prefix}_{digest}"
        copy["sourcePaperId"] = ""
        copy["method"] = "method_" + hashlib.sha1(
            str(copy.get("method") or "").encode("utf-8")
        ).hexdigest()[:6]
        result.append(copy)
    random.Random(seed).shuffle(result)
    for index, row in enumerate(result, start=1):
        row["annotationId"] = f"peerqa_ann_{index:04d}"
        row["automaticMatchScore"] = ""
        row["automaticCoverageCandidate"] = ""
    return result


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in OUTPUT_FIELDS} for row in rows)


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--predictions", required=True)
    parser.add_argument("--references", required=True)
    parser.add_argument("--output-prefix", required=True)
    parser.add_argument("--threshold", type=float, default=0.12)
    parser.add_argument(
        "--max-findings",
        type=int,
        default=0,
        help="Use only the first N ranked findings per method; 0 keeps all findings.",
    )
    parser.add_argument("--shuffle-seed", type=int, default=20260711)
    args = parser.parse_args()

    rows = build_rows(
        read_jsonl(Path(args.predictions)),
        read_jsonl(Path(args.references)),
        args.threshold,
        max_findings=args.max_findings,
    )
    prefix = Path(args.output_prefix)
    write_csv(prefix.with_name(prefix.name + "_answer_key.csv"), rows)
    write_jsonl(prefix.with_name(prefix.name + "_answer_key.jsonl"), rows)
    blind = blind_rows(rows, args.shuffle_seed)
    write_csv(prefix.with_name(prefix.name + "_blind.csv"), blind)
    write_jsonl(prefix.with_name(prefix.name + "_blind.jsonl"), blind)
    covered = sum(bool(row["automaticCoverageCandidate"]) for row in rows)
    print(f"tasks={len(rows)} autoCoverageCandidates={covered} noMatch={len(rows) - covered}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
