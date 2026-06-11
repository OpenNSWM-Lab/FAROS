"""Platform-domain contract facade.

This groups cross-domain runtime and planning contracts behind a single import
surface for future platform work.
"""

from app.models.artifact import Artifact, ArtifactType
from app.models.plan_package import (
    PlanBackground,
    PlanDownstreamContract,
    PlanEvidenceTrace,
    PlanExpectedMetric,
    PlanGap,
    PlanGapItem,
    PlanIdeaSummary,
    PlanLiteraturePaperSummary,
    PlanLiteratureSurvey,
    PlanOutput,
    PlanOutputType,
    PlanPackage,
    PlanPrinciple,
    PlanQualityGate,
    PlanStage,
    PlanStep,
)
from app.models.plan_session import (
    CandidatePlan,
    PaperType,
    PAPER_TYPE_LABELS,
    PlanSession,
    PlanSessionConfig,
    PlanSessionStatus,
    PlanStepResult,
    PlanWorkflowTrace,
    SelectedPlan,
)
from app.models.research_plan import ResearchPlan
from app.models.run import Run, RunConfig, RunStatus, RunType
from app.schemas.artifact import ArtifactCreate, ArtifactResponse
from app.schemas.execution_summary import (
    ComputePolicyBlock,
    ExecutionSummary,
    LifecycleBlock,
    StatusStripBlock,
)
from app.schemas.research_plan import ResearchPlanCreate, ResearchPlanResponse
from app.schemas.run import RunCreate, RunListResponse, RunResponse, RunUpdate

__all__ = [
    "Artifact",
    "ArtifactCreate",
    "ArtifactResponse",
    "ArtifactType",
    "ComputePolicyBlock",
    "ExecutionSummary",
    "LifecycleBlock",
    "StatusStripBlock",
    "CandidatePlan",
    "PlanSession",
    "PlanSessionConfig",
    "PlanSessionStatus",
    "PaperType",
    "PAPER_TYPE_LABELS",
    "PlanStepResult",
    "PlanWorkflowTrace",
    "SelectedPlan",
    "PlanBackground",
    "PlanDownstreamContract",
    "PlanEvidenceTrace",
    "PlanExpectedMetric",
    "PlanGap",
    "PlanGapItem",
    "PlanIdeaSummary",
    "PlanLiteraturePaperSummary",
    "PlanLiteratureSurvey",
    "PlanOutput",
    "PlanOutputType",
    "PlanPackage",
    "PlanPrinciple",
    "PlanQualityGate",
    "PlanStage",
    "PlanStep",
    "ResearchPlan",
    "ResearchPlanCreate",
    "ResearchPlanResponse",
    "Run",
    "RunConfig",
    "RunCreate",
    "RunListResponse",
    "RunResponse",
    "RunStatus",
    "RunType",
    "RunUpdate",
]
