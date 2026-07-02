"""Paper-type templates for single PlanPackage generation."""

from __future__ import annotations

from typing import List

from pydantic import BaseModel, Field


class PlanRoleTemplate(BaseModel):
    id: str
    label: str
    keywords: List[str] = Field(default_factory=list)
    repairHint: str = ""


class PlanTemplate(BaseModel):
    templateId: str
    paperType: str
    requiredRoles: List[PlanRoleTemplate] = Field(default_factory=list)
    stageShape: List[dict[str, str]] = Field(default_factory=list)
    recommendedOutputs: List[str] = Field(default_factory=list)
    recommendedMetrics: List[str] = Field(default_factory=list)
    requiredComparisons: List[str] = Field(default_factory=list)
    requiredAblations: List[str] = Field(default_factory=list)
    forbiddenClaims: List[str] = Field(default_factory=list)


_COMMON_FORBIDDEN = [
    "Do not claim executed results in the idea+plan stage.",
    "Do not invent paper IDs, claim IDs, KG IDs, probe IDs, graph patch IDs, datasets, or benchmark numbers.",
    "Do not present expected metrics as observed results.",
]


_COMMON_OUTPUTS = [
    "literature_survey.md",
    "selected_gap.json",
    "method_principle.md",
    "validation_metrics.json",
    "planned_results_table.csv",
]


_GENERIC_ROLES = [
    PlanRoleTemplate(
        id="evidence_grounding",
        label="evidence and gap grounding",
        keywords=["evidence", "literature", "paper", "gap", "claim", "citation", "grounding", "probe", "graph", "证据", "论文", "文献", "缺口", "引用"],
        repairHint="Add a step that summarizes investigated papers, selects the GAP, and binds evidence IDs.",
    ),
    PlanRoleTemplate(
        id="baseline_and_comparison",
        label="baseline and comparison design",
        keywords=["baseline", "comparison", "compare", "control", "prior work", "reference", "基线", "对比", "比较", "参照"],
        repairHint="Add a step that defines baselines/control methods and comparison artifacts.",
    ),
    PlanRoleTemplate(
        id="method_specification",
        label="method implementation specification",
        keywords=["implementation", "implement", "prototype", "module", "algorithm", "pipeline", "mechanism", "method", "实现", "模块", "算法", "流程", "机制", "方法"],
        repairHint="Add a step that decomposes the proposed method into modules, inputs, outputs, and artifacts.",
    ),
    PlanRoleTemplate(
        id="validation_metrics",
        label="validation metrics and success criteria",
        keywords=["metric", "accuracy", "faithfulness", "latency", "cost", "robustness", "evaluation", "target", "指标", "评估", "验证", "目标", "准确", "鲁棒"],
        repairHint="Add measurable expected metrics that directly test the hypothesis and selected GAP.",
    ),
    PlanRoleTemplate(
        id="ablation_or_sensitivity",
        label="ablation, sensitivity, or failure analysis",
        keywords=["ablation", "sensitivity", "robustness", "failure", "stress", "negative", "消融", "敏感性", "鲁棒", "失败", "压力", "负例"],
        repairHint="Add at least one ablation, sensitivity, robustness, or failure-analysis step.",
    ),
    PlanRoleTemplate(
        id="handoff_artifacts",
        label="downstream handoff artifacts",
        keywords=["artifact", "report", "table", "chart", "checkpoint", "handoff", "code", "log", "产物", "报告", "表格", "图", "检查点", "交付", "代码"],
        repairHint="Declare concrete outputs consumed by code, experiment, paper, review, or validation modules.",
    ),
]


_TEMPLATES: dict[str, PlanTemplate] = {
    "generic": PlanTemplate(
        templateId="plan-template-generic-v1",
        paperType="generic",
        requiredRoles=_GENERIC_ROLES,
        stageShape=[
            {"title": "Evidence and baseline grounding", "mustCover": "key papers, selected GAP, baselines/control methods, evidenceRefs"},
            {"title": "Method implementation specification", "mustCover": "proposed modules, algorithm/pipeline, constants, implementation artifacts"},
            {"title": "Validation, ablation, and handoff", "mustCover": "main comparison metrics, ablation/sensitivity, robustness/failure checks, paper-ready outputs"},
        ],
        recommendedOutputs=_COMMON_OUTPUTS,
        recommendedMetrics=["task effectiveness", "robustness", "efficiency", "evidence traceability"],
        requiredComparisons=["closest prior work", "baseline/control method"],
        requiredAblations=["remove or vary one core component"],
        forbiddenClaims=_COMMON_FORBIDDEN,
    ),
    "algorithmic_method": PlanTemplate(
        templateId="plan-template-algorithmic-method-v1",
        paperType="algorithmic_method",
        requiredRoles=_GENERIC_ROLES,
        stageShape=[
            {"title": "Literature, GAP, and baseline definition", "mustCover": "closest prior algorithms, control settings, selected GAP"},
            {"title": "Algorithm and implementation specification", "mustCover": "core algorithm, modules, inputs/outputs, complexity assumptions"},
            {"title": "Main evaluation and ablation", "mustCover": "task metrics, baseline comparison, component ablation, robustness/sensitivity"},
        ],
        recommendedOutputs=[*_COMMON_OUTPUTS, "algorithm_spec.md", "ablation_plan.csv"],
        recommendedMetrics=["accuracy or task score", "latency or compute cost", "robustness", "ablation delta"],
        requiredComparisons=["strong baseline", "closest prior method", "simple control variant"],
        requiredAblations=["remove core module", "sensitivity to key hyperparameter"],
        forbiddenClaims=_COMMON_FORBIDDEN,
    ),
    "system": PlanTemplate(
        templateId="plan-template-system-v1",
        paperType="system",
        requiredRoles=_GENERIC_ROLES,
        stageShape=[
            {"title": "Use case, evidence, and baseline system", "mustCover": "scenario, existing systems, selected GAP"},
            {"title": "Architecture and module contract", "mustCover": "components, APIs, data flow, deployment constraints"},
            {"title": "End-to-end system evaluation", "mustCover": "throughput, latency, cost, failure cases, ablation"},
        ],
        recommendedOutputs=[*_COMMON_OUTPUTS, "system_architecture.md", "api_contract.json"],
        recommendedMetrics=["latency", "throughput", "cost", "reliability", "task success"],
        requiredComparisons=["existing system baseline", "component-off control"],
        requiredAblations=["disable one module", "stress-test bottleneck"],
        forbiddenClaims=_COMMON_FORBIDDEN,
    ),
    "benchmark": PlanTemplate(
        templateId="plan-template-benchmark-v1",
        paperType="benchmark",
        requiredRoles=[
            PlanRoleTemplate(
                id="task_definition",
                label="task and dataset definition",
                keywords=["task", "dataset", "annotation", "label", "data", "benchmark", "任务", "数据集", "标注", "基准"],
                repairHint="Define the benchmark task, data source, annotation protocol, and split policy.",
            ),
            PlanRoleTemplate(
                id="baseline_coverage",
                label="baseline coverage",
                keywords=["baseline", "model", "coverage", "comparison", "基线", "模型", "覆盖", "对比"],
                repairHint="Declare baseline families and coverage criteria for benchmark evaluation.",
            ),
            PlanRoleTemplate(
                id="evaluation_protocol",
                label="evaluation protocol",
                keywords=["metric", "protocol", "evaluation", "scoring", "指标", "协议", "评估", "评分"],
                repairHint="Specify metrics, scoring protocol, and quality checks.",
            ),
            PlanRoleTemplate(
                id="dataset_quality",
                label="dataset quality and risk checks",
                keywords=["quality", "bias", "leakage", "agreement", "robustness", "质量", "偏差", "泄漏", "一致性"],
                repairHint="Plan data quality, bias, leakage, and annotation agreement checks.",
            ),
            PlanRoleTemplate(
                id="handoff_artifacts",
                label="benchmark handoff artifacts",
                keywords=["artifact", "dataset", "report", "table", "leaderboard", "产物", "数据集", "报告", "榜单"],
                repairHint="Declare dataset cards, benchmark schema, result tables, and review artifacts.",
            ),
        ],
        stageShape=[
            {"title": "Task and dataset protocol", "mustCover": "task boundary, data collection, annotation/split policy"},
            {"title": "Baseline coverage and evaluation", "mustCover": "baseline families, metrics, scoring scripts"},
            {"title": "Quality, bias, and handoff", "mustCover": "leakage checks, agreement checks, dataset card, leaderboard outputs"},
        ],
        recommendedOutputs=["dataset_card.md", "benchmark_schema.json", "baseline_matrix.csv", "evaluation_protocol.md"],
        recommendedMetrics=["annotation agreement", "coverage", "baseline performance", "leakage rate", "bias check"],
        requiredComparisons=["baseline model families", "simple heuristic baseline"],
        requiredAblations=["data slice analysis", "metric sensitivity"],
        forbiddenClaims=_COMMON_FORBIDDEN,
    ),
    "analysis": PlanTemplate(
        templateId="plan-template-analysis-v1",
        paperType="analysis",
        requiredRoles=_GENERIC_ROLES,
        stageShape=[
            {"title": "Evidence and analysis question grounding", "mustCover": "related work, variables, selected GAP"},
            {"title": "Controlled analysis design", "mustCover": "variables, controls, slices, statistical checks"},
            {"title": "Interpretation and failure analysis", "mustCover": "visualizations, robustness, failure cases, claims"},
        ],
        recommendedOutputs=["analysis_protocol.md", "variable_control_table.csv", "slice_analysis_plan.csv", "visualization_plan.md"],
        recommendedMetrics=["effect size", "statistical significance", "slice consistency", "failure rate"],
        requiredComparisons=["control setting", "prior explanation"],
        requiredAblations=["variable sensitivity", "slice ablation"],
        forbiddenClaims=_COMMON_FORBIDDEN,
    ),
    "application": PlanTemplate(
        templateId="plan-template-application-v1",
        paperType="application",
        requiredRoles=_GENERIC_ROLES,
        stageShape=[
            {"title": "Scenario, users, and evidence grounding", "mustCover": "application scene, constraints, selected GAP"},
            {"title": "Application workflow specification", "mustCover": "inputs, user/system actions, safety boundaries"},
            {"title": "Task utility and risk validation", "mustCover": "task metrics, usability, safety, failure cases"},
        ],
        recommendedOutputs=["scenario_spec.md", "workflow_contract.json", "utility_metrics.json", "risk_checklist.md"],
        recommendedMetrics=["task success", "user effort", "latency", "safety violation rate", "cost"],
        requiredComparisons=["current workflow", "baseline tool"],
        requiredAblations=["remove assistance component", "stress-test edge case"],
        forbiddenClaims=_COMMON_FORBIDDEN,
    ),
    "survey": PlanTemplate(
        templateId="plan-template-survey-v1",
        paperType="survey",
        requiredRoles=[
            PlanRoleTemplate(
                id="literature_taxonomy",
                label="literature taxonomy",
                keywords=["taxonomy", "classification", "category", "cluster", "survey", "文献", "分类", "图谱", "综述"],
                repairHint="Add a step that builds a taxonomy over investigated papers.",
            ),
            PlanRoleTemplate(
                id="comparison_dimensions",
                label="comparison dimensions",
                keywords=["dimension", "comparison", "criteria", "axis", "对比", "维度", "标准", "轴"],
                repairHint="Define comparison dimensions such as method, assumptions, evidence, limitation, and application scope.",
            ),
            PlanRoleTemplate(
                id="gap_synthesis",
                label="gap synthesis",
                keywords=["gap", "limitation", "open problem", "future", "缺口", "限制", "开放问题", "趋势"],
                repairHint="Synthesize gaps and future directions from literature limitations and claims.",
            ),
            PlanRoleTemplate(
                id="survey_artifacts",
                label="survey handoff artifacts",
                keywords=["table", "figure", "taxonomy", "map", "report", "表", "图", "分类", "报告"],
                repairHint="Declare taxonomy tables, comparison matrices, figures, and paper-facing report artifacts.",
            ),
        ],
        stageShape=[
            {"title": "Literature taxonomy", "mustCover": "paper grouping, comparison axes, evidence table"},
            {"title": "GAP and trend synthesis", "mustCover": "limitations, unresolved issues, future directions"},
            {"title": "Survey artifact handoff", "mustCover": "taxonomy figure, comparison matrix, related-work narrative"},
        ],
        recommendedOutputs=["taxonomy_table.csv", "comparison_matrix.csv", "gap_synthesis.md", "survey_figures_plan.md"],
        recommendedMetrics=["paper coverage", "taxonomy consistency", "gap support count"],
        requiredComparisons=["method categories", "claim and limitation dimensions"],
        requiredAblations=[],
        forbiddenClaims=[*_COMMON_FORBIDDEN, "Do not describe downstream experiments as required for a survey-style PlanPackage."],
    ),
}


_ALIASES = {
    "algorithm": "algorithmic_method",
    "method": "algorithmic_method",
    "algorithmic": "algorithmic_method",
    "empirical": "algorithmic_method",
    "evaluation": "analysis",
    "theory": "analysis",
    "theoretical": "analysis",
    "dataset": "benchmark",
    "bench": "benchmark",
    "application_study": "application",
    "applied": "application",
}


def normalize_paper_type(paper_type: str | None) -> str:
    raw = (paper_type or "generic").strip().lower().replace("-", "_").replace(" ", "_")
    return _ALIASES.get(raw, raw if raw in _TEMPLATES else "generic")


def get_plan_template(paper_type: str | None) -> PlanTemplate:
    return _TEMPLATES[normalize_paper_type(paper_type)]
