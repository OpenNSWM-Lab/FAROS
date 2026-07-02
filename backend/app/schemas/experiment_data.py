"""
Experiment Data Schema — structured JSON payload for cross-module data exchange.

These models define the contract between the Code/Experiment module and
downstream consumers (Paper drafting, Review, external analysis).
"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class CodePrinciple(BaseModel):
    """Code principle / algorithm description extracted from project."""
    title: str = Field(..., description="Principle or algorithm name")
    description: str = Field(..., description="Natural-language explanation")
    pseudocode: Optional[str] = Field(None, description="Pseudocode if available")
    source_file: Optional[str] = Field(None, description="Source file path in project")
    language: Optional[str] = Field(None, description="Programming language")


class ExperimentMetric(BaseModel):
    """A single quantitative metric from experiment execution."""
    name: str
    value: float
    unit: Optional[str] = None
    direction: Optional[str] = Field(None, description="'higher-is-better' or 'lower-is-better'")
    baseline: Optional[float] = Field(None, description="Baseline/reference value")
    improvement_pct: Optional[float] = Field(None, description="Improvement % over baseline")


class FigureData(BaseModel):
    """Reference to a generated figure/chart."""
    title: str
    path: str = Field(..., description="Relative path within project/experiment")
    description: str = ""
    figure_type: Optional[str] = Field(None, description="line, bar, scatter, heatmap, etc.")
    width: Optional[int] = None
    height: Optional[int] = None


class DatasetInfo(BaseModel):
    """Information about a dataset used in the experiment."""
    name: str
    path: Optional[str] = None
    shape: Optional[str] = Field(None, description="e.g. '10000x128'")
    description: str = ""
    source: Optional[str] = Field(None, description="Dataset origin URL or citation")


class ExecutionResult(BaseModel):
    """Result of running a code project."""
    exit_code: int = Field(..., description="Process exit code")
    stdout_summary: Optional[str] = Field(None, description="Key stdout lines")
    stderr_summary: Optional[str] = Field(None, description="Key stderr lines")
    duration_seconds: Optional[float] = None
    command: Optional[str] = Field(None, description="Command that was executed")
    metrics: List[ExperimentMetric] = Field(default_factory=list)


class ExperimentDesign(BaseModel):
    """Experiment design motivation and methodology."""
    objective: str = Field(..., description="What the experiment aims to prove/disprove")
    hypothesis: Optional[str] = Field(None, description="Formal hypothesis statement")
    methodology: str = Field(..., description="How the experiment was conducted")
    independent_variables: List[str] = Field(default_factory=list)
    dependent_variables: List[str] = Field(default_factory=list)
    controlled_variables: List[str] = Field(default_factory=list)
    datasets: List[DatasetInfo] = Field(default_factory=list)


class AnalysisSection(BaseModel):
    """Analysis and interpretation of results."""
    summary: str = Field(..., description="Overall findings summary")
    key_observations: List[str] = Field(default_factory=list)
    limitations: List[str] = Field(default_factory=list)
    future_work: List[str] = Field(default_factory=list)
    conclusion: Optional[str] = Field(None, description="Concluding remarks")


class ExperimentData(BaseModel):
    """
    Complete experiment data package passed from Code/Experiment module
    to downstream modules (Paper drafting, Review, etc.).

    This is the canonical JSON contract between FAROS pipeline stages.
    """
    # Identity
    project_id: str = Field(..., description="CodeProjectV2 ID")
    project_title: str = Field(..., description="Human-readable project title")
    experiment_id: Optional[str] = Field(None, description="Experiment record ID")

    # Code principles (algorithms, pseudocode)
    code_principles: List[CodePrinciple] = Field(default_factory=list)

    # Experiment design
    experiment_design: Optional[ExperimentDesign] = None

    # Execution results
    execution: Optional[ExecutionResult] = None

    # Metrics
    metrics: List[ExperimentMetric] = Field(default_factory=list)

    # Figures and charts
    figures: List[FigureData] = Field(default_factory=list)

    # Analysis
    analysis: Optional[AnalysisSection] = None

    # Raw MD report path (if generated)
    report_md_path: Optional[str] = Field(None, description="Relative path to generated MD report")

    # Any additional unstructured data
    extra: Dict[str, Any] = Field(default_factory=dict)


class ExperimentDataResponse(BaseModel):
    """API response wrapper."""
    ok: bool = True
    data: ExperimentData
    report_md: Optional[str] = Field(None, description="Full MD report content if requested")
