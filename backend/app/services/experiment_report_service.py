"""
Experiment Report Service — generates Markdown reports from experiment artifacts.

Consumes: CodeProjectV2 files, execution metrics, experiment records
Produces: Structured Markdown reports suitable for paper writing
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.schemas.experiment_data import (
    AnalysisSection,
    CodePrinciple,
    DatasetInfo,
    ExecutionResult,
    ExperimentData,
    ExperimentDesign,
    ExperimentMetric,
    FigureData,
)

logger = logging.getLogger(__name__)


@dataclass
class ReportContext:
    """Raw data gathered for report generation."""
    project_id: str
    project_title: str
    experiment_id: Optional[str] = None
    code_principles: List[CodePrinciple] = field(default_factory=list)
    experiment_design: Optional[ExperimentDesign] = None
    execution: Optional[ExecutionResult] = None
    metrics: List[ExperimentMetric] = field(default_factory=list)
    figures: List[FigureData] = field(default_factory=list)
    analysis: Optional[AnalysisSection] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_experiment_data(cls, data: ExperimentData) -> "ReportContext":
        return cls(
            project_id=data.project_id,
            project_title=data.project_title,
            experiment_id=data.experiment_id,
            code_principles=data.code_principles,
            experiment_design=data.experiment_design,
            execution=data.execution,
            metrics=data.metrics,
            figures=data.figures,
            analysis=data.analysis,
            extra=data.extra,
        )

    @classmethod
    def from_project_files(
        cls,
        project_id: str,
        project_title: str,
        project_dir: Path,
        experiment_id: Optional[str] = None,
    ) -> "ReportContext":
        """Build context by scanning a project directory for known files."""
        ctx = cls(project_id=project_id, project_title=project_title, experiment_id=experiment_id)

        # Try to load metrics.json
        metrics_path = project_dir / "metrics.json"
        if metrics_path.exists():
            try:
                metrics_data = json.loads(metrics_path.read_text(encoding="utf-8"))
                for item in metrics_data if isinstance(metrics_data, list) else [metrics_data]:
                    if isinstance(item, dict) and "name" in item:
                        ctx.metrics.append(ExperimentMetric(
                            name=item.get("name", "unknown"),
                            value=float(item.get("value", 0)),
                            unit=item.get("unit"),
                            direction=item.get("direction"),
                            baseline=item.get("baseline"),
                            improvement_pct=item.get("improvement_pct"),
                        ))
            except (json.JSONDecodeError, ValueError, TypeError) as exc:
                logger.warning("Failed to parse metrics.json: %s", exc)

        # Try to load experiment config
        config_path = project_dir / "configs" / "experiment.json"
        if config_path.exists():
            try:
                config = json.loads(config_path.read_text(encoding="utf-8"))
                if config.get("objective"):
                    ctx.experiment_design = ExperimentDesign(
                        objective=config.get("objective", ""),
                        hypothesis=config.get("hypothesis"),
                        methodology=config.get("methodology", ""),
                        independent_variables=config.get("independent_variables", []),
                        dependent_variables=config.get("dependent_variables", []),
                        controlled_variables=config.get("controlled_variables", []),
                    )
            except (json.JSONDecodeError, ValueError) as exc:
                logger.warning("Failed to parse experiment.json: %s", exc)

        # Scan for figures
        figures_dir = project_dir / "figures"
        if figures_dir.exists():
            for f in sorted(figures_dir.iterdir()):
                if f.suffix.lower() in (".png", ".jpg", ".jpeg", ".pdf", ".svg"):
                    ctx.figures.append(FigureData(
                        title=f.stem.replace("_", " ").title(),
                        path=f"figures/{f.name}",
                        description="",
                    ))

        return ctx


class ExperimentReportService:
    """Generates Markdown experiment reports from structured data."""

    def generate_report(self, data: ExperimentData) -> str:
        """Generate a full experiment report in Markdown."""
        ctx = ReportContext.from_experiment_data(data)
        return self._render(ctx)

    def generate_report_from_project(
        self,
        project_id: str,
        project_title: str,
        project_dir: Path,
        experiment_id: Optional[str] = None,
        extra_data: Optional[ExperimentData] = None,
    ) -> str:
        """Generate report by scanning a project directory, merged with optional extra data."""
        ctx = ReportContext.from_project_files(project_id, project_title, project_dir, experiment_id)

        if extra_data:
            if extra_data.code_principles:
                ctx.code_principles = extra_data.code_principles
            if extra_data.experiment_design:
                ctx.experiment_design = extra_data.experiment_design
            if extra_data.execution:
                ctx.execution = extra_data.execution
            if extra_data.metrics:
                ctx.metrics = extra_data.metrics
            if extra_data.figures:
                ctx.figures = extra_data.figures
            if extra_data.analysis:
                ctx.analysis = extra_data.analysis

        return self._render(ctx)

    # ---- rendering helpers ----

    @staticmethod
    def _render(ctx: ReportContext) -> str:
        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        lines: List[str] = []

        _h = lines.append

        _h(f"# Experiment Report: {ctx.project_title}")
        _h("")
        _h(f"> **Generated by FAROS AutoResearch Runtime**")
        _h(f"> **Project:** {ctx.project_id}")
        if ctx.experiment_id:
            _h(f"> **Experiment:** {ctx.experiment_id}")
        _h(f"> **Date:** {now_utc}")
        _h("")
        _h("---")
        _h("")

        # ---- Code Principles ----
        _h("## 1. Code Principles")
        _h("")
        if ctx.code_principles:
            for i, cp in enumerate(ctx.code_principles, 1):
                _h(f"### 1.{i} {cp.title}")
                _h("")
                _h(f"**Description:** {cp.description}")
                if cp.language:
                    _h(f"**Language:** {cp.language}")
                if cp.source_file:
                    _h(f"**Source File:** `{cp.source_file}`")
                _h("")
                if cp.pseudocode:
                    _h(f"**Pseudocode:**")
                    _h("```")
                    _h(cp.pseudocode.strip())
                    _h("```")
                    _h("")
        else:
            _h("*No code principles extracted. Add a `pseudocode` section in your project's main module.*")
            _h("")

        # ---- Experiment Design ----
        _h("## 2. Experiment Design Motivation")
        _h("")
        if ctx.experiment_design:
            ed = ctx.experiment_design
            _h(f"### 2.1 Objective")
            _h(f"{ed.objective}")
            _h("")
            if ed.hypothesis:
                _h(f"### 2.2 Hypothesis")
                _h(f"**H1:** {ed.hypothesis}")
                _h("")
            _h(f"### 2.3 Methodology")
            _h(f"{ed.methodology}")
            _h("")
            if ed.independent_variables or ed.dependent_variables or ed.controlled_variables:
                _h("| Variable | Type | Description |")
                _h("|----------|------|-------------|")
                for v in ed.independent_variables:
                    _h(f"| {v} | Independent | — |")
                for v in ed.dependent_variables:
                    _h(f"| {v} | Dependent | — |")
                for v in ed.controlled_variables:
                    _h(f"| {v} | Controlled | — |")
                _h("")
            if ed.datasets:
                _h("### 2.4 Datasets")
                _h("")
                for ds in ed.datasets:
                    _h(f"- **{ds.name}**" + (f" ({ds.shape})" if ds.shape else "") + f": {ds.description}")
                _h("")
        else:
            _h("*Experiment design not configured. Add a `configs/experiment.json` file to your project.*")
            _h("")

        # ---- Results ----
        _h("## 3. Experiment Results")
        _h("")
        if ctx.execution:
            ex = ctx.execution
            _h("### 3.1 Execution Summary")
            _h("")
            _h("| Metric | Value |")
            _h("|--------|-------|")
            _h(f"| Exit Code | {ex.exit_code} |")
            if ex.duration_seconds is not None:
                _h(f"| Duration | {ex.duration_seconds:.1f}s |")
            if ex.command:
                _h(f"| Command | `{ex.command}` |")
            _h("")

        if ctx.metrics:
            _h("### 3.2 Key Metrics")
            _h("")
            cols = ["Metric", "Value"]
            has_baseline = any(m.baseline is not None for m in ctx.metrics)
            has_improvement = any(m.improvement_pct is not None for m in ctx.metrics)
            if has_baseline:
                cols.append("Baseline")
            if has_improvement:
                cols.append("Improvement")

            _h("| " + " | ".join(cols) + " |")
            _h("|" + "|".join(["------"] * len(cols)) + "|")
            for m in ctx.metrics:
                row = [m.name, f"**{m.value}**" + (f" {m.unit}" if m.unit else "")]
                if has_baseline:
                    row.append(f"{m.baseline}" if m.baseline is not None else "—")
                if has_improvement:
                    if m.improvement_pct is not None:
                        direction = "↑" if m.improvement_pct > 0 else "↓"
                        row.append(f"{direction}{abs(m.improvement_pct):.1f}%")
                    else:
                        row.append("—")
                _h("| " + " | ".join(row) + " |")
            _h("")

        if ctx.figures:
            _h("### 3.3 Figures")
            _h("")
            for fg in ctx.figures:
                _h(f"![{fg.title}]({fg.path})")
                _h(f"*{fg.title}: {fg.description}*")
                _h("")

        # ---- Analysis ----
        _h("## 4. Analysis")
        _h("")
        if ctx.analysis:
            an = ctx.analysis
            _h("### 4.1 Summary")
            _h(an.summary)
            _h("")

            if an.key_observations:
                _h("### 4.2 Key Observations")
                _h("")
                for i, obs in enumerate(an.key_observations, 1):
                    _h(f"{i}. **{obs}**")
                _h("")

            if an.limitations:
                _h("### 4.3 Limitations")
                _h("")
                for lim in an.limitations:
                    _h(f"- {lim}")
                _h("")

            if an.future_work:
                _h("### 4.4 Future Work")
                _h("")
                for i, fw in enumerate(an.future_work, 1):
                    _h(f"{i}. {fw}")
                _h("")

            if an.conclusion:
                _h("### 4.5 Conclusion")
                _h(an.conclusion)
                _h("")
        else:
            _h("*Analysis not yet generated. Run the experiment and collect metrics to auto-generate analysis.*")
            _h("")

        # ---- Appendix ----
        _h("---")
        _h("")
        _h("## Appendix: Reproducibility")
        _h("")
        if ctx.extra:
            _h("### Extra Data")
            _h("```json")
            _h(json.dumps(ctx.extra, indent=2, ensure_ascii=False))
            _h("```")
            _h("")

        _h("> **Note for Paper Module:** All metrics, figures, pseudocode, and analysis in this report are "
           "available as structured JSON via the experiment-data API endpoint.")

        return "\n".join(lines)

    def generate_minimal_report(
        self,
        project_id: str,
        project_title: str,
        metrics: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """Generate a bare-bones report stub for projects without full experimental data."""
        data = ExperimentData(
            project_id=project_id,
            project_title=project_title,
            metrics=[
                ExperimentMetric(name=m["name"], value=m["value"], unit=m.get("unit"))
                for m in (metrics or [])
            ],
        )
        return self.generate_report(data)


# Singleton
_report_service: Optional[ExperimentReportService] = None


def get_experiment_report_service() -> ExperimentReportService:
    global _report_service
    if _report_service is None:
        _report_service = ExperimentReportService()
    return _report_service
