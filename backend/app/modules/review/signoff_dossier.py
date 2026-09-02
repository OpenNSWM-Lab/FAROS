"""Deterministic, human-readable ReviewX signoff dossiers.

The experiment feedback record remains the source of truth.  This module only
normalizes fields that are already present in the record; it never parses
scientific values from prose or recomputes experimental results.
"""

from __future__ import annotations

import hashlib
import html
import json
from collections import Counter
from datetime import UTC, datetime
from typing import Any, Dict, Iterable, Literal

from pydantic import BaseModel, Field

from app.modules.review.audit_chain import record_audit_integrity
from app.modules.review.human_feedback import human_feedback_state
from app.modules.review.human_feedback_verification import (
    human_condition_verification_state,
)
from app.modules.review.human_signoff import human_signoff_state, publication_ready


NOT_PROVIDED = "未提供 / Not provided"


class SignoffDossier(BaseModel):
    """Stable public contract for a ReviewX signoff dossier."""

    schemaVersion: Literal["reviewx-signoff-dossier/v1"] = "reviewx-signoff-dossier/v1"
    release: Literal["draft", "official"] = "draft"
    watermark: str | None = "DRAFT_NOT_HUMAN_APPROVED"
    generatedAt: str
    contentHash: str
    subject: Dict[str, Any] = Field(default_factory=dict)
    executiveDecision: Dict[str, Any] = Field(default_factory=dict)
    plan: Dict[str, Any] = Field(default_factory=dict)
    evidence: Dict[str, Any] = Field(default_factory=dict)
    review: Dict[str, Any] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)
    provenance: Dict[str, Any] = Field(default_factory=dict)
    signoffs: Dict[str, Any] = Field(default_factory=dict)


def _content_hash(payload: Dict[str, Any]) -> str:
    stable = {
        key: value
        for key, value in payload.items()
        if key not in {"generatedAt", "contentHash"}
    }
    encoded = json.dumps(
        stable,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _first(record: Dict[str, Any], *keys: str, default: Any = NOT_PROVIDED) -> Any:
    for key in keys:
        value = record.get(key)
        if value is not None and value != "" and value != [] and value != {}:
            return value
    return default


def _list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _redact_secrets(value: Any) -> Any:
    """Remove credential-shaped fields before traces enter a public dossier."""

    if isinstance(value, list):
        return [_redact_secrets(item) for item in value]
    if not isinstance(value, dict):
        return value
    public: Dict[str, Any] = {}
    for key, item in value.items():
        normalized = str(key).lower().replace("-", "").replace("_", "")
        if normalized in {
            "authorization",
            "apikey",
            "accesskey",
            "secretkey",
            "password",
            "token",
            "accesstoken",
        }:
            continue
        public[str(key)] = _redact_secrets(item)
    return public


def _metric_source(record: Dict[str, Any], metric: Dict[str, Any]) -> tuple[str | None, str]:
    explicit = metric.get("sourceArtifactId") or metric.get("artifactId")
    if explicit:
        return str(explicit), "verified_artifact"
    experiment_artifact = (record.get("sourceArtifacts") or {}).get("experimentEvidence")
    if experiment_artifact:
        return str(experiment_artifact), "record.metricSnapshot"
    return None, "record.metricSnapshot"


def _metrics(record: Dict[str, Any]) -> list[Dict[str, Any]]:
    """Copy numeric evidence only from the structured metric snapshot."""

    result: list[Dict[str, Any]] = []
    for raw in record.get("metricSnapshot") or []:
        if not isinstance(raw, dict):
            continue
        artifact_id, source = _metric_source(record, raw)
        current = raw.get("current") if "current" in raw else raw.get("value")
        ci_lower = raw.get("ciLower")
        ci_upper = raw.get("ciUpper")
        explicit_decision = raw.get("decision") or raw.get("gateDecision")
        if (
            isinstance(ci_lower, (int, float))
            and isinstance(ci_upper, (int, float))
            and ci_lower <= 0 <= ci_upper
        ):
            display_decision = "BOUNDARY"
            interpretation = "方向一致但统计不确定 / Directionally consistent but statistically uncertain"
        else:
            display_decision = explicit_decision or NOT_PROVIDED
            interpretation = raw.get("interpretation") or NOT_PROVIDED
        result.append({
            "name": str(raw.get("name") or NOT_PROVIDED),
            "direction": str(raw.get("direction") or NOT_PROVIDED),
            "baseline": raw.get("baseline"),
            "current": current,
            "delta": raw.get("delta"),
            "relativeDelta": raw.get("relativeDelta"),
            "ciLower": ci_lower,
            "ciUpper": ci_upper,
            "confidenceLevel": raw.get("confidenceLevel"),
            "decision": str(display_decision),
            "interpretation": str(interpretation),
            "role": str(raw.get("role") or NOT_PROVIDED),
            "split": str(raw.get("split") or NOT_PROVIDED),
            "sourceArtifactId": artifact_id,
            "source": source,
        })
    return result


def _plan_delta(record: Dict[str, Any]) -> Dict[str, Any]:
    raw = record.get("planDelta") or record.get("planRevision") or {}
    if not isinstance(raw, dict):
        raw = {}
    changes = raw.get("parameterChanges") or raw.get("changes") or []
    normalized_changes = []
    for item in changes:
        if not isinstance(item, dict):
            continue
        normalized_changes.append({
            "field": item.get("field") or item.get("parameter") or NOT_PROVIDED,
            "oldValue": item.get("oldValue", NOT_PROVIDED),
            "newValue": item.get("newValue", NOT_PROVIDED),
            "rationale": item.get("rationale") or item.get("evidence") or NOT_PROVIDED,
            "targetNode": item.get("targetNode") or item.get("affectedNode") or NOT_PROVIDED,
        })
    changed_sections = raw.get("changedSections") or []
    return {
        "changedSections": [str(item) for item in _list(changed_sections)],
        "parameterChanges": normalized_changes,
        "evidenceReferences": [
            str(item) for item in _list(raw.get("evidenceReferences"))
        ],
        "revisionId": raw.get("revisionId") or raw.get("id"),
    }


def _blocking_reasons(record: Dict[str, Any]) -> list[Dict[str, str]]:
    reasons: list[Dict[str, str]] = []

    def add(code: str, message: str, next_step: str) -> None:
        reasons.append({"code": code, "message": message, "nextStep": next_step})

    quality = record.get("qualityAssessment") or {}
    blockers = [
        item
        for item in quality.get("findings") or []
        if str((item or {}).get("severity") or "").lower() == "blocker"
    ]
    if str(quality.get("gateStatus") or "").lower() == "fail":
        add("QUALITY_GATE_FAILED", "质量门未通过", "修复失败项并重新运行 ReviewX 审计")
    if blockers:
        add("OPEN_BLOCKERS", f"仍有 {len(blockers)} 个 Blocker", "处理全部 Blocker 后重新审计")
    decision = str((record.get("iterationDecision") or {}).get("decision") or "")
    if decision != "accept_results":
        add("RESULT_NOT_ACCEPTED", "当前迭代尚未接受实验结果", "完成要求的修复或重跑")

    feedback = human_feedback_state(record)
    if feedback.get("requiresApplication") and not feedback.get("applied"):
        add("FEEDBACK_NOT_APPLIED", "人工修改意见尚未应用", "将反馈应用到目标节点并重跑")
    conditions = human_condition_verification_state(record)
    if conditions.get("required") and not conditions.get("allResolved"):
        add("CONDITIONS_UNRESOLVED", "继承的验收条件尚未全部解决", "逐项核验证据并记录结果")

    integrity = record_audit_integrity(record)
    if not integrity.get("valid"):
        add("AUDIT_INTEGRITY_INVALID", "签核审计链校验失败", "恢复可信记录并重新签核")
    if record.get("publicationEligible", True) is not True:
        add("PUBLICATION_INELIGIBLE", "该记录未获正式发布资格", "使用具备发布资格的科学评审记录")
    if record.get("reviewPurpose") == "technical_test":
        add("TECHNICAL_TEST", "技术测试记录不能正式发布", "创建独立的科学评审记录")

    for stage, state in human_signoff_state(record).items():
        if state.get("stale"):
            add(
                f"{stage.upper()}_SIGNOFF_STALE",
                f"{stage} 签核因证据变化已失效",
                f"重新核对当前证据并完成 {stage} 签核",
            )
        elif state.get("required") and state.get("status") != "approved":
            add(
                f"{stage.upper()}_SIGNOFF_REQUIRED",
                f"{stage} 阶段尚未批准",
                f"完成 {stage} 阶段责任确认与签核",
            )
    return reasons


def build_signoff_dossier(
    record: Dict[str, Any],
    *,
    release: Literal["draft", "official"] = "draft",
) -> SignoffDossier:
    """Build a deterministic dossier from one persisted feedback record."""

    ready = publication_ready(record)
    if release == "official" and not ready:
        raise ValueError("Official signoff dossier requires publicationReady=true")

    quality = record.get("qualityAssessment") or {}
    findings = [dict(item) for item in quality.get("findings") or [] if isinstance(item, dict)]
    finding_counts = Counter(str(item.get("severity") or "unknown").lower() for item in findings)
    raw_plan = record.get("plan") or record.get("researchPlan") or {}
    if not isinstance(raw_plan, dict):
        raw_plan = {}
    sources = record.get("sourceArtifacts") or {}
    limitations = [str(item) for item in _list(record.get("limitations")) if str(item).strip()]
    uncertainty = quality.get("uncertainty")
    if uncertainty:
        limitations.append(str(uncertainty))
    if not limitations:
        limitations = [NOT_PROVIDED]

    payload: Dict[str, Any] = {
        "schemaVersion": "reviewx-signoff-dossier/v1",
        "release": release,
        "watermark": None if release == "official" else "DRAFT_NOT_HUMAN_APPROVED",
        "generatedAt": datetime.now(UTC).isoformat(),
        "subject": {
            "feedbackId": record.get("id") or NOT_PROVIDED,
            "runId": record.get("runId") or NOT_PROVIDED,
            "researchSeriesId": record.get("researchSeriesId") or NOT_PROVIDED,
            "scientificQuestion": _first(
                record,
                "scientificQuestion",
                "researchQuestion",
                "question",
                "questionId",
            ),
            "planPackageId": record.get("planPackageId") or NOT_PROVIDED,
            "iterationNumber": record.get("iterationNumber") or 1,
            "artifactHash": human_signoff_state(record)["conclusion"]["artifactHash"],
            "createdAt": record.get("createdAt") or NOT_PROVIDED,
        },
        "executiveDecision": {
            "iterationDecision": (record.get("iterationDecision") or {}).get("decision") or NOT_PROVIDED,
            "qualityGate": quality.get("gateStatus") or NOT_PROVIDED,
            "publicationReady": ready,
            "blockingReasons": _blocking_reasons(record),
        },
        "plan": {
            "hypothesis": raw_plan.get("hypothesis") or record.get("hypothesis") or NOT_PROVIDED,
            "baseline": raw_plan.get("baseline") or record.get("baseline") or NOT_PROVIDED,
            "intervention": raw_plan.get("intervention") or record.get("intervention") or NOT_PROVIDED,
            "primaryMetric": raw_plan.get("primaryMetric") or record.get("primaryMetric") or NOT_PROVIDED,
            "guardrails": _list(raw_plan.get("guardrails") or record.get("guardrails")),
            "stopConditions": _list(raw_plan.get("stopConditions") or record.get("stopConditions")),
            "delta": _plan_delta(record),
        },
        "evidence": {
            "dataSource": _list(record.get("dataSource") or record.get("dataSources")),
            "dataSplitPolicy": record.get("dataSplitPolicy") or NOT_PROVIDED,
            "metrics": _metrics(record),
        },
        "review": {
            "findingCounts": dict(sorted(finding_counts.items())),
            "findings": findings,
            "humanFeedback": human_feedback_state(record),
            "acceptanceConditions": human_condition_verification_state(record),
        },
        "limitations": limitations,
        "provenance": {
            "sourceArtifacts": dict(sources),
            "sourceArtifactUrls": dict(record.get("sourceArtifactUrls") or {}),
            "benchmarkFingerprint": record.get("benchmarkFingerprint") or NOT_PROVIDED,
            "qwenCalls": _redact_secrets(
                list(quality.get("llmTrace") or record.get("qwenCalls") or [])
            ),
            "auditIntegrity": record_audit_integrity(record),
        },
        "signoffs": human_signoff_state(record),
    }
    payload["contentHash"] = _content_hash(payload)
    return SignoffDossier.model_validate(payload)


def _e(value: Any) -> str:
    if value is None or value == "":
        value = NOT_PROVIDED
    return html.escape(str(value), quote=True)


def _metric_cell(value: Any) -> str:
    if isinstance(value, float):
        return _e(f"{value:.6g}")
    return _e(value)


def _items(values: Iterable[Any]) -> str:
    rows = "".join(f"<li>{_e(value)}</li>" for value in values)
    return f"<ul>{rows}</ul>" if rows else f"<span class=\"missing\">{_e(NOT_PROVIDED)}</span>"


def render_signoff_dossier_html(dossier: SignoffDossier) -> str:
    """Render a self-contained, escaped and A4-printable dossier."""

    data = dossier.model_dump(mode="json")
    subject = data["subject"]
    decision = data["executiveDecision"]
    plan = data["plan"]
    evidence = data["evidence"]
    review = data["review"]
    provenance = data["provenance"]
    blockers = decision.get("blockingReasons") or []
    metrics = evidence.get("metrics") or []
    changes = (plan.get("delta") or {}).get("parameterChanges") or []
    findings = review.get("findings") or []
    signoffs = data.get("signoffs") or {}

    blocker_html = "".join(
        f"<li><strong>{_e(item.get('message'))}</strong><br><span>{_e(item.get('nextStep'))}</span></li>"
        for item in blockers
    ) or "<li class=\"ok\">无阻断项 / No blocking reason</li>"
    metric_rows = "".join(
        "<tr>"
        f"<td>{_e(item.get('name'))}<small>{_e(item.get('role'))}</small></td>"
        f"<td>{_e(item.get('direction'))}</td>"
        f"<td>{_metric_cell(item.get('baseline'))}</td>"
        f"<td>{_metric_cell(item.get('current'))}</td>"
        f"<td>{_metric_cell(item.get('delta'))}</td>"
        f"<td>[{_metric_cell(item.get('ciLower'))}, {_metric_cell(item.get('ciUpper'))}]</td>"
        f"<td>{_e(item.get('decision'))}</td>"
        f"<td>{_e(item.get('sourceArtifactId') or item.get('source'))}</td>"
        "</tr>"
        for item in metrics
    ) or f"<tr><td colspan=\"8\" class=\"missing\">{_e(NOT_PROVIDED)}</td></tr>"
    change_rows = "".join(
        "<tr>"
        f"<td>{_e(item.get('field'))}</td><td>{_e(item.get('oldValue'))}</td>"
        f"<td>{_e(item.get('newValue'))}</td><td>{_e(item.get('rationale'))}</td>"
        f"<td>{_e(item.get('targetNode'))}</td></tr>"
        for item in changes
    ) or f"<tr><td colspan=\"5\" class=\"missing\">{_e(NOT_PROVIDED)}</td></tr>"
    finding_rows = "".join(
        "<tr>"
        f"<td>{_e(item.get('severity'))}</td><td>{_e(item.get('code') or item.get('id'))}</td>"
        f"<td>{_e(item.get('description'))}</td><td>{_e(item.get('suggestedFix'))}</td></tr>"
        for item in findings
    ) or "<tr><td colspan=\"4\" class=\"ok\">无 ReviewX finding / No finding</td></tr>"
    signoff_rows = "".join(
        "<tr>"
        f"<td>{_e(stage)}</td><td>{_e(item.get('status'))}</td>"
        f"<td>{_e(item.get('reviewerName') or item.get('reviewerId'))}</td>"
        f"<td>{_e(item.get('actorAccountId'))}</td><td>{_e(item.get('authAssurance'))}</td>"
        f"<td>{_e(item.get('decidedAt'))}</td><td class=\"mono\">{_e(str(item.get('artifactHash') or '')[:20])}</td>"
        "</tr>"
        for stage, item in signoffs.items()
    )

    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ReviewX Signoff Dossier · {_e(subject.get('feedbackId'))}</title>
<style>
@page {{ size:A4; margin:14mm; }}
:root {{ color-scheme:light; --ink:#17212b; --muted:#64748b; --line:#cbd5e1; --teal:#087f7a; --yellow:#ffb400; --danger:#b42318; --paper:#fff; }}
* {{ box-sizing:border-box; }} body {{ margin:0; background:#eef2f5; color:var(--ink); font:10.5pt/1.48 system-ui,-apple-system,"Segoe UI",sans-serif; }}
main {{ width:210mm; min-height:297mm; margin:16px auto; padding:14mm; background:var(--paper); }}
h1 {{ margin:0 0 4px; font-size:22pt; }} h2 {{ border-bottom:2px solid var(--teal); padding-bottom:5px; margin:24px 0 10px; font-size:15pt; }}
h3 {{ margin:14px 0 6px; font-size:11.5pt; }} p {{ margin:5px 0; }} small,.muted,.missing {{ color:var(--muted); }} small {{ display:block; }}
.status {{ display:grid; grid-template-columns:repeat(4,1fr); gap:8px; margin:14px 0; }} .status div {{ border:1px solid var(--line); border-top:4px solid var(--teal); padding:9px; }}
.watermark {{ color:var(--danger); border:2px solid var(--danger); padding:6px 10px; display:inline-block; font-weight:700; letter-spacing:.08em; }}
.official {{ color:var(--teal); border-color:var(--teal); }} .danger {{ color:var(--danger); }} .ok {{ color:var(--teal); }}
table {{ width:100%; border-collapse:collapse; margin:7px 0 14px; table-layout:fixed; font-size:9pt; }} th,td {{ border:1px solid var(--line); padding:6px; vertical-align:top; overflow-wrap:anywhere; }} th {{ background:#e8f4f2; text-align:left; }} tr {{ break-inside:avoid; }}
.mono {{ font-family:ui-monospace,SFMono-Regular,Consolas,monospace; font-variant-numeric:tabular-nums; }} .kv {{ display:grid; grid-template-columns:150px 1fr; gap:4px 12px; }}
section {{ break-inside:auto; }} ul {{ margin:5px 0; padding-left:20px; }}
@media(max-width:800px) {{ main {{ width:100%; margin:0; padding:18px; }} .status {{ grid-template-columns:1fr 1fr; }} table {{ display:block; overflow-x:auto; table-layout:auto; }} .kv {{ grid-template-columns:1fr; }} }}
@media print {{ body {{ background:#fff; }} main {{ margin:0; width:auto; min-height:auto; padding:0; }} a {{ color:inherit; text-decoration:none; }} }}
</style></head><body><main>
<header><div class="watermark {'official' if data['release'] == 'official' else ''}">{_e(data.get('watermark') or 'OFFICIAL_HUMAN_APPROVED')}</div>
<h1>ReviewX 人工签核档案</h1><p class="muted">Human-readable scientific signoff dossier</p>
<div class="kv"><b>Feedback ID</b><span class="mono">{_e(subject.get('feedbackId'))}</span><b>Run ID</b><span class="mono">{_e(subject.get('runId'))}</span><b>生成时间</b><span>{_e(data.get('generatedAt'))}</span><b>内容哈希</b><span class="mono">{_e(data.get('contentHash'))}</span></div></header>
<section><h2>1. 决策摘要</h2><h3>{_e(subject.get('scientificQuestion'))}</h3>
<div class="status"><div><small>轮次</small><b>{_e(subject.get('iterationNumber'))}</b></div><div><small>迭代决定</small><b>{_e(decision.get('iterationDecision'))}</b></div><div><small>质量门</small><b>{_e(decision.get('qualityGate'))}</b></div><div><small>可发布</small><b>{_e(decision.get('publicationReady'))}</b></div></div>
<h3>阻断原因与下一步</h3><ul class="{'danger' if blockers else ''}">{blocker_html}</ul></section>
<section><h2>2. 研究问题与可执行计划</h2><div class="kv"><b>假设</b><span>{_e(plan.get('hypothesis'))}</span><b>基线</b><span>{_e(plan.get('baseline'))}</span><b>干预</b><span>{_e(plan.get('intervention'))}</span><b>主指标</b><span>{_e(plan.get('primaryMetric'))}</span></div><h3>Guardrails</h3>{_items(plan.get('guardrails') or [])}<h3>停止条件</h3>{_items(plan.get('stopConditions') or [])}</section>
<section><h2>3. Plan Delta</h2><table><thead><tr><th>字段</th><th>旧值</th><th>新值</th><th>依据</th><th>影响节点</th></tr></thead><tbody>{change_rows}</tbody></table></section>
<section><h2>4. 实验与统计证据</h2><p><b>数据划分：</b>{_e(evidence.get('dataSplitPolicy'))}</p><table><thead><tr><th>指标</th><th>方向</th><th>基线</th><th>当前</th><th>差值</th><th>95% CI</th><th>门控</th><th>来源</th></tr></thead><tbody>{metric_rows}</tbody></table></section>
<section><h2>5. ReviewX finding 与处置</h2><table><thead><tr><th>严重度</th><th>编号</th><th>问题</th><th>建议</th></tr></thead><tbody>{finding_rows}</tbody></table></section>
<section><h2>6. 数据与来源</h2><p><b>数据来源：</b>{_e(', '.join(map(str, evidence.get('dataSource') or [])) or NOT_PROVIDED)}</p><p><b>Benchmark fingerprint：</b><span class="mono">{_e(provenance.get('benchmarkFingerprint'))}</span></p><p><b>Artifact：</b></p>{_items(f'{key}: {value}' for key,value in (provenance.get('sourceArtifacts') or {}).items())}<p><b>Qwen calls：</b>{_e(len(provenance.get('qwenCalls') or []))}</p></section>
<section><h2>7. 人工确认与审计</h2><table><thead><tr><th>阶段</th><th>状态</th><th>签核人</th><th>登录账号</th><th>认证强度</th><th>时间</th><th>证据哈希</th></tr></thead><tbody>{signoff_rows}</tbody></table><p><b>审计完整性：</b>{_e((provenance.get('auditIntegrity') or {}).get('valid'))}</p></section>
<section><h2>8. 限制与允许结论范围</h2>{_items(data.get('limitations') or [])}</section>
</main></body></html>"""
