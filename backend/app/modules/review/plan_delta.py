"""Content-addressed contracts that bind experiment evidence to plan changes."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, model_validator


EvidenceAuthority = Literal["observed", "deterministic", "qwen", "human"]
TriggerStatus = Literal["unmet_target", "optimization_opportunity", "guardrail_failure"]


class DeltaEvidenceRef(BaseModel):
    id: str = Field(min_length=1)
    artifact: str = Field(min_length=1)
    jsonPath: str = Field(min_length=1)
    authority: EvidenceAuthority
    summary: str = Field(min_length=1)
    value: Any = None


class DeltaTrigger(BaseModel):
    status: TriggerStatus
    statement: str = Field(min_length=1)
    metric: str = Field(min_length=1)
    observedValue: float
    targetValue: Optional[float] = None
    comparator: Optional[Literal[">", ">=", "<", "<="]] = None
    evidenceIds: List[str] = Field(min_length=1)


class CandidateAudit(BaseModel):
    candidateId: str = Field(min_length=1)
    feasible: bool
    metrics: Dict[str, float] = Field(default_factory=dict)
    failedConstraints: List[str] = Field(default_factory=list)
    change: str = Field(min_length=1)

    @model_validator(mode="after")
    def failed_candidates_need_a_reason(self) -> "CandidateAudit":
        if not self.feasible and not self.failedConstraints:
            raise ValueError("An infeasible candidate must name at least one failed constraint")
        return self


class PlanFieldDelta(BaseModel):
    fieldPath: str = Field(min_length=1)
    before: Any
    after: Any
    rationale: str = Field(min_length=1)
    expectedEffect: str = Field(min_length=1)
    evidenceIds: List[str] = Field(min_length=1)

    @model_validator(mode="after")
    def values_must_change(self) -> "PlanFieldDelta":
        if self.before == self.after:
            raise ValueError(f"Plan delta '{self.fieldPath}' does not change its value")
        return self


class QwenPlanContribution(BaseModel):
    model: str = Field(min_length=1)
    role: str = Field(min_length=1)
    selectedCandidateId: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    expectedTradeoff: str = Field(min_length=1)
    falsificationCriteria: List[str] = Field(min_length=1)
    promptHash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    finalHoldoutExposed: bool = False


class PlanDeltaContract(BaseModel):
    schemaVersion: Literal["reviewx-plan-delta/v1"] = "reviewx-plan-delta/v1"
    contractId: str = Field(min_length=1)
    researchSeriesId: str = Field(min_length=1)
    fromRunId: str = Field(min_length=1)
    toRunId: str = Field(min_length=1)
    createdAt: str = Field(min_length=1)
    benchmarkFingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    evidenceGateStatus: Literal["pass", "fail"]
    scientificDecision: Literal["revise_plan", "rerun_experiment", "needs_human"]
    trigger: DeltaTrigger
    evidence: List[DeltaEvidenceRef] = Field(min_length=1)
    candidates: List[CandidateAudit] = Field(min_length=2)
    selectedCandidateId: str = Field(min_length=1)
    changes: List[PlanFieldDelta] = Field(min_length=1)
    qwenContribution: QwenPlanContribution
    stopConditions: List[str] = Field(min_length=1)
    finalHoldoutPolicy: str = Field(min_length=1)
    contentHash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def references_must_be_consistent(self) -> "PlanDeltaContract":
        candidate_map = {item.candidateId: item for item in self.candidates}
        selected = candidate_map.get(self.selectedCandidateId)
        if selected is None:
            raise ValueError("selectedCandidateId is not present in candidates")
        if not selected.feasible:
            raise ValueError("selectedCandidateId must refer to a feasible candidate")
        if self.qwenContribution.selectedCandidateId != self.selectedCandidateId:
            raise ValueError("Qwen contribution must use the frozen selected candidate")

        evidence_ids = {item.id for item in self.evidence}
        referenced = set(self.trigger.evidenceIds)
        for change in self.changes:
            referenced.update(change.evidenceIds)
        missing = sorted(referenced - evidence_ids)
        if missing:
            raise ValueError(f"Plan delta references unknown evidence IDs: {missing}")
        return self


def contract_content_hash(payload: Dict[str, Any]) -> str:
    canonical = {
        key: value
        for key, value in payload.items()
        if key != "contentHash"
    }
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def seal_plan_delta_contract(payload: Dict[str, Any]) -> PlanDeltaContract:
    """Validate a contract and bind its semantic content to one stable hash."""

    draft = {
        **payload,
        "contentHash": f"sha256:{'0' * 64}",
    }
    normalized = PlanDeltaContract.model_validate(draft).model_dump(mode="json")
    normalized["contentHash"] = contract_content_hash(normalized)
    return PlanDeltaContract.model_validate(normalized)


def verify_plan_delta_contract(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Return explicit checks suitable for release gates and user interfaces."""

    checks = {
        "schemaValid": False,
        "contentHashValid": False,
        "selectedCandidateFeasible": False,
        "evidenceReferencesResolved": False,
        "planFieldsActuallyChanged": False,
        "finalHoldoutHiddenDuringPlanning": False,
    }
    error = None
    try:
        contract = PlanDeltaContract.model_validate(payload)
        checks["schemaValid"] = True
        checks["contentHashValid"] = (
            contract.contentHash == contract_content_hash(payload)
        )
        candidate = next(
            item for item in contract.candidates
            if item.candidateId == contract.selectedCandidateId
        )
        checks["selectedCandidateFeasible"] = candidate.feasible
        evidence_ids = {item.id for item in contract.evidence}
        referenced = set(contract.trigger.evidenceIds)
        for change in contract.changes:
            referenced.update(change.evidenceIds)
        checks["evidenceReferencesResolved"] = referenced <= evidence_ids
        checks["planFieldsActuallyChanged"] = all(
            item.before != item.after for item in contract.changes
        )
        checks["finalHoldoutHiddenDuringPlanning"] = (
            not contract.qwenContribution.finalHoldoutExposed
        )
    except (StopIteration, ValueError) as exc:
        error = str(exc)
    return {
        "status": "passed" if all(checks.values()) else "failed",
        "checks": checks,
        "error": error,
    }
