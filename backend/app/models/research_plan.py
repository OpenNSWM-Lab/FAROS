"""
ResearchPlan Domain Model

Scientific Responsibility:
- Represent pre-execution scientific intent
- Enforce hypothesis-driven research methodology
- Maintain immutability for scientific integrity
- Separate conceptual design from execution details

A ResearchPlan answers:
- What question are we asking?
- What hypothesis are we testing?
- What variables are involved?
- What methodology will be used?

It does NOT specify:
- Which GPU to use
- Which file paths to read
- How many iterations to run
(Those are Run concerns)
"""

from datetime import UTC, datetime
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field
from enum import Enum

def _utcnow() -> datetime:
    return datetime.now(UTC)


class ResearchApproach(str, Enum):
    """
    High-level research methodology categories.
    
    These describe the scientific approach, not implementation details.
    """
    COMPARATIVE_TRAINING = "comparative_training"  # Compare methods A vs B
    ABLATION_STUDY = "ablation_study"              # Remove components to test necessity
    HYPERPARAMETER_SWEEP = "hyperparameter_sweep"  # Systematic parameter exploration
    BASELINE_ESTABLISHMENT = "baseline_establishment"  # Establish reference performance
    REPLICATION_STUDY = "replication_study"        # Reproduce prior work


class Variables(BaseModel):
    """
    Scientific variables following experimental design principles.
    
    Independent: What we manipulate (treatment)
    Dependent: What we measure (outcome)
    Controls: What we hold constant (confounds)
    """
    independent: List[str] = Field(
        ...,
        description="Variables being manipulated (e.g., 'training_method', 'learning_rate')",
        min_length=1
    )
    dependent: List[str] = Field(
        ...,
        description="Variables being measured (e.g., 'accuracy', 'latency', 'cost')",
        min_length=1
    )
    controls: List[str] = Field(
        default_factory=list,
        description="Variables held constant (e.g., 'model_architecture', 'dataset_size')"
    )


class Methodology(BaseModel):
    """
    Conceptual methodology specification.
    
    Describes WHAT will be done, not HOW the computer will execute it.
    """
    direction_id: str = Field(
        ...,
        description="Research direction from taxonomy (e.g., 'preference_optimization_dpo_ipo')"
    )
    approach: ResearchApproach = Field(
        ...,
        description="High-level research approach"
    )
    datasets: List[str] = Field(
        ...,
        description="Conceptual dataset names (e.g., 'MMLU', 'HumanEval'), NOT file paths",
        min_length=1
    )
    template_id: Optional[str] = Field(
        None,
        description="Optional template linking concept to execution pattern"
    )


class ExpectedOutcomes(BaseModel):
    """
    Hypothesis-driven expected results.
    
    Critical for scientific integrity - we state expectations BEFORE seeing results.
    """
    primary_metric: str = Field(
        ...,
        description="Main success criterion (e.g., 'accuracy', 'helpfulness_score')"
    )
    baseline_value: Optional[float] = Field(
        None,
        description="Expected baseline performance (if known from literature)"
    )
    target_value: Optional[float] = Field(
        None,
        description="Hypothesis target (e.g., 'improve by 10%' → 0.85 if baseline is 0.75)"
    )
    success_criteria: str = Field(
        ...,
        description="Human-readable success definition (e.g., 'Outperform baseline by >5%')"
    )


class ResearchPlan(BaseModel):
    """
    Immutable scientific intent specification.
    
    Represents the research design BEFORE execution.
    Once created, cannot be modified (scientific integrity).
    """
    # Identity
    id: str = Field(..., description="Unique identifier (UUID)")
    created_at: datetime = Field(
        default_factory=_utcnow,
        description="Creation timestamp (immutability proof)"
    )
    
    # Scientific Intent
    research_question: str = Field(
        ...,
        description="Clear research question (e.g., 'Does DPO improve helpfulness over SFT?')",
        min_length=10
    )
    hypothesis: str = Field(
        ...,
        description="Testable hypothesis (e.g., 'DPO will increase helpfulness by >10%')",
        min_length=10
    )
    
    # Experimental Design
    variables: Variables = Field(
        ...,
        description="Independent, dependent, and control variables"
    )
    methodology: Methodology = Field(
        ...,
        description="Research methodology specification"
    )
    expected_outcomes: ExpectedOutcomes = Field(
        ...,
        description="Hypothesis-driven expected results"
    )
    
    # Metadata
    tags: List[str] = Field(
        default_factory=list,
        description="Organizational tags (e.g., 'alignment', 'efficiency')"
    )
    notes: str = Field(
        default="",
        description="Free-form scientific notes and context"
    )
    
    # Idea Traceability (Phase 1.5)
    source_session_id: Optional[str] = Field(
        default=None,
        description="ID of the idea session this plan originated from"
    )
    source_candidate_id: Optional[str] = Field(
        default=None,
        description="ID of the selected candidate idea"
    )
    source_candidate_index: Optional[int] = Field(
        default=None,
        description="1-based index of the candidate in the session"
    )
    source_title: Optional[str] = Field(
        default=None,
        description="Title of the source idea candidate"
    )
    
    model_config = ConfigDict(
        frozen=True,
        json_schema_extra={
            "example": {
                "id": "plan_dpo_vs_sft_001",
                "research_question": "Does Direct Preference Optimization improve helpfulness over Supervised Fine-Tuning?",
                "hypothesis": "DPO training will increase helpfulness scores by at least 10% compared to SFT baseline",
                "variables": {
                    "independent": ["training_method"],
                    "dependent": ["helpfulness_score", "safety_score", "training_time"],
                    "controls": ["model_size", "dataset_size", "evaluation_set"]
                },
                "methodology": {
                    "direction_id": "preference_optimization_dpo_ipo",
                    "approach": "comparative_training",
                    "datasets": ["HH-RLHF", "Anthropic-Helpful"],
                    "template_id": "dpo_standard"
                },
                "expected_outcomes": {
                    "primary_metric": "helpfulness_score",
                    "baseline_value": 0.75,
                    "target_value": 0.825,
                    "success_criteria": "Helpfulness score > 0.825 (10% improvement over 0.75 baseline)"
                },
                "tags": ["alignment", "preference-learning", "comparative-study"],
                "notes": "Replicating findings from Rafailov et al. (2023) on smaller model scale"
            }
        },
    )
