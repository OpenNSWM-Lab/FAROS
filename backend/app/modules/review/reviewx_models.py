"""Structured data contracts for evidence-grounded ReviewX runs."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class SourceSpan:
    file: str
    section: str = "Unknown"
    line: Optional[int] = None


@dataclass
class Claim:
    id: str
    paperId: str
    text: str
    claimType: str
    importance: str
    requiresEvidence: bool
    sourceSpan: SourceSpan
    riskHints: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Evidence:
    id: str
    paperId: str
    evidenceType: str
    sourceModule: str
    sourcePath: str
    summary: str
    confidence: float = 0.7
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Finding:
    id: str
    paperId: str
    claimId: Optional[str]
    severity: str
    riskType: str
    title: str
    description: str
    evidenceIds: List[str]
    targetModule: str
    suggestedFix: str
    confidence: float
    location: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RiskNode:
    id: str
    question: str
    claimIds: List[str]
    riskScore: float
    status: str
    assignedModel: str
    children: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ReviewXReport:
    reviewId: str
    paperId: str
    claims: List[Claim]
    evidence: List[Evidence]
    findings: List[Finding]
    riskTree: List[RiskNode]
    actionItems: List[Dict[str, Any]]
    summary: Dict[str, Any]
    modelTrace: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "reviewId": self.reviewId,
            "paperId": self.paperId,
            "claims": [c.to_dict() for c in self.claims],
            "evidence": [e.to_dict() for e in self.evidence],
            "findings": [f.to_dict() for f in self.findings],
            "riskTree": [r.to_dict() for r in self.riskTree],
            "actionItems": self.actionItems,
            "summary": self.summary,
            "modelTrace": self.modelTrace,
        }
