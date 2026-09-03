"""Run a controlled Qwen-VL fault-injection benchmark for ReviewX figures."""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt

from app.modules.review.reviewx_models import Claim, Evidence, SourceSpan
from app.modules.review.visual_evidence import audit_visual_evidence


SCHEMA_VERSION = "reviewx-visual-cem/v1"
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class VisualCase:
    case_id: str
    chart_kind: str
    baseline: float
    proposed: float
    claim: str
    caption: str
    expected_fault: bool
    expected_types: tuple[str, ...] = ()
    show_uncertainty: bool = False
    narrow_axis: bool = False


CASES = (
    VisualCase(
        "clean_bar", "bar", 0.70, 0.82,
        "On the held-out set, the proposed method improves F1 from 0.70 to 0.82.",
        "Held-out F1 is 0.70 for Baseline and 0.82 for Proposed.",
        False,
    ),
    VisualCase(
        "numeric_swap", "bar", 0.82, 0.70,
        "On the held-out set, the proposed method improves F1 from 0.70 to 0.82.",
        "Held-out F1 is 0.70 for Baseline and 0.82 for Proposed.",
        True, ("numeric_mismatch", "trend_reversal", "claim_mismatch", "caption_mismatch"),
    ),
    VisualCase(
        "clean_ci", "bar", 0.70, 0.82,
        "The proposed method reaches F1 0.82 versus 0.70, with 95% confidence intervals shown.",
        "Error bars show 95% confidence intervals for held-out F1.",
        False, show_uncertainty=True,
    ),
    VisualCase(
        "missing_ci", "bar", 0.70, 0.82,
        "The proposed method reaches F1 0.82 versus 0.70, with 95% confidence intervals shown.",
        "Error bars show 95% confidence intervals for held-out F1.",
        True, ("uncertainty_missing", "caption_mismatch"), show_uncertainty=False,
    ),
    VisualCase(
        "wrong_value", "bar", 0.70, 0.76,
        "On the held-out set, the proposed method improves F1 from 0.70 to 0.82.",
        "Held-out F1 is 0.70 for Baseline and 0.82 for Proposed.",
        True, ("numeric_mismatch", "claim_mismatch", "caption_mismatch"),
    ),
    VisualCase(
        "truncated_axis", "bar", 0.80, 0.81,
        "The proposed method delivers a large held-out F1 improvement over the baseline.",
        "A large performance gain is visible for Proposed over Baseline.",
        True, ("axis_issue", "claim_mismatch", "caption_mismatch"), narrow_axis=True,
    ),
    VisualCase(
        "clean_legend", "line", 0.70, 0.82,
        "The orange Proposed curve finishes above the gray Baseline curve.",
        "Orange denotes Proposed and gray denotes Baseline; Proposed finishes higher.",
        False,
    ),
    VisualCase(
        "legend_conflict", "line_swapped_legend", 0.70, 0.82,
        "The orange Proposed curve finishes above the gray Baseline curve.",
        "Orange denotes Proposed and gray denotes Baseline; Proposed finishes higher.",
        True, ("legend_mismatch", "caption_mismatch", "claim_mismatch"),
    ),
)


def _render_chart(case: VisualCase, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fig, axis = plt.subplots(figsize=(6.4, 4.2), dpi=150)
    if case.chart_kind == "bar":
        errors = [0.018, 0.014] if case.show_uncertainty else None
        bars = axis.bar(
            ["Baseline", "Proposed"],
            [case.baseline, case.proposed],
            yerr=errors,
            capsize=6 if errors else 0,
            color=["#4B5563", "#FFB300"],
            width=0.56,
        )
        axis.set_ylim((0.79, 0.82) if case.narrow_axis else (0.55, 0.90))
        for bar, value in zip(bars, (case.baseline, case.proposed)):
            axis.text(
                bar.get_x() + bar.get_width() / 2,
                value + (0.001 if case.narrow_axis else 0.008),
                f"{value:.2f}",
                ha="center",
                fontsize=11,
            )
    else:
        steps = [1, 2, 3, 4]
        baseline = [case.baseline - 0.05, case.baseline - 0.03, case.baseline - 0.01, case.baseline]
        proposed = [case.baseline - 0.04, case.baseline, case.proposed - 0.03, case.proposed]
        first_label = "Proposed" if case.chart_kind == "line_swapped_legend" else "Baseline"
        second_label = "Baseline" if case.chart_kind == "line_swapped_legend" else "Proposed"
        axis.plot(steps, baseline, color="#4B5563", marker="o", label=first_label)
        axis.plot(steps, proposed, color="#FFB300", marker="o", label=second_label)
        axis.legend(loc="lower right")
        axis.set_xlabel("Training checkpoint")
        axis.set_ylim(0.60, 0.86)
    axis.set_ylabel("F1 score")
    axis.set_title("Held-out F1 comparison")
    axis.grid(axis="y", alpha=0.2)
    fig.tight_layout()
    fig.savefig(target)
    plt.close(fig)


def _run_case(case: VisualCase, *, output_dir: Path, provider: str, model: str) -> dict[str, Any]:
    image_path = output_dir / "images" / f"{case.case_id}.png"
    _render_chart(case, image_path)
    claim = Claim(
        id="claim_001",
        paperId=f"paper_{case.case_id}",
        text=case.claim,
        claimType="performance",
        importance="high",
        requiresEvidence=True,
        sourceSpan=SourceSpan(file="main.tex", section="Results", line=42),
    )
    figure = {
        "id": f"figure_{case.case_id}",
        "source": "paper_latex",
        "sourcePath": f"figures/{case.case_id}.png",
        "absolutePath": str(image_path),
        "mimeType": "image/png",
        "caption": case.caption,
        "title": "Held-out F1 comparison",
    }
    evidence = Evidence(
        id="evidence_figure",
        paperId=claim.paperId,
        evidenceType="figure",
        sourceModule="paper",
        sourcePath=figure["sourcePath"],
        summary=case.caption,
        metadata={"figureId": figure["id"], "visualAuditEligible": True},
    )
    result = audit_visual_evidence(
        paper={"id": claim.paperId, "title": "Visual-CEM controlled benchmark"},
        claims=[claim],
        evidence=[evidence],
        links={claim.id: [evidence.id]},
        artifacts={"visualFigures": [figure]},
        provider_name=provider,
        visual_model=model,
        budget_mode="balanced",
        enabled=True,
        data_root=str(output_dir),
    )
    observed_types = sorted({
        str(item.riskType).removeprefix("visual_")
        for item in result.findings
    })
    rejected = bool(result.findings) or any(
        item.supportStatus in {"contradicted", "unsupported"}
        for item in result.verifications
    )
    localized = not case.expected_fault or bool(set(observed_types) & set(case.expected_types))
    return {
        "caseId": case.case_id,
        "expectedFault": case.expected_fault,
        "expectedTypes": list(case.expected_types),
        "decision": "reject" if rejected else "accept",
        "correct": rejected == case.expected_fault,
        "localized": localized,
        "observedTypes": observed_types,
        "trace": result.trace,
        "verifications": [item.to_dict() for item in result.verifications],
        "findings": [item.to_dict() for item in result.findings],
    }


def _summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    faulty = [item for item in records if item["expectedFault"]]
    clean = [item for item in records if not item["expectedFault"]]
    detected = sum(item["decision"] == "reject" for item in faulty)
    false_rejects = sum(item["decision"] == "reject" for item in clean)
    localized = sum(item["localized"] for item in faulty)
    completed = sum(item["trace"].get("status") == "completed" for item in records)
    return {
        "totalCases": len(records),
        "faultyCases": len(faulty),
        "cleanCases": len(clean),
        "completedCalls": completed,
        "faultDetectionRate": round(detected / len(faulty), 3) if faulty else 0,
        "normalFalseRejectRate": round(false_rejects / len(clean), 3) if clean else 0,
        "faultLocalizationRate": round(localized / len(faulty), 3) if faulty else 0,
        "decisionAccuracy": round(sum(item["correct"] for item in records) / len(records), 3),
        "totalTokens": sum(int(item["trace"].get("estimatedTokenCost") or 0) for item in records),
        "qualityGate": "passed" if (
            completed == len(records)
            and detected == len(faulty)
            and false_rejects == 0
            and localized == len(faulty)
        ) else "failed",
    }


def _write_report(output_dir: Path, payload: dict[str, Any]) -> None:
    summary = payload["summary"]
    lines = [
        "# ReviewX Visual-CEM controlled validation",
        "",
        f"- Provider/model: `{payload['provider']}` / `{payload['model']}`",
        f"- Run time: `{payload['generatedAt']}`",
        f"- Cases: {summary['totalCases']} ({summary['faultyCases']} faulty, {summary['cleanCases']} clean)",
        f"- Fault detection rate: **{summary['faultDetectionRate']:.1%}**",
        f"- Normal false reject rate: **{summary['normalFalseRejectRate']:.1%}**",
        f"- Fault localization rate: **{summary['faultLocalizationRate']:.1%}**",
        f"- Decision accuracy: **{summary['decisionAccuracy']:.1%}**",
        f"- Quality gate: **{summary['qualityGate']}**",
        "",
        "This is a controlled visual fault-injection test. It demonstrates figure/caption/claim audit behavior; it is not a real-world effectiveness estimate.",
        "",
        "| Case | Expected | Decision | Localized types | Correct |",
        "|---|---:|---:|---|---:|",
    ]
    for record in payload["cases"]:
        lines.append(
            f"| {record['caseId']} | {'fault' if record['expectedFault'] else 'clean'} | "
            f"{record['decision']} | {', '.join(record['observedTypes']) or '-'} | "
            f"{'yes' if record['correct'] else 'no'} |"
        )
    (output_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _render_pair_summary(output_dir: Path) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(10.8, 3.9), dpi=170)
    panels = (
        ("clean_bar.png", "(a) Clean control: ACCEPT", "Caption and visible values agree"),
        ("numeric_swap.png", "(b) Injected fault: REJECT", "Value swap and reversed direction localized"),
    )
    for axis, (name, title, note) in zip(axes, panels):
        axis.imshow(plt.imread(output_dir / "images" / name))
        axis.set_title(title, fontsize=12, fontweight="bold", loc="left")
        axis.text(0.5, -0.04, note, transform=axis.transAxes, ha="center", va="top", fontsize=10)
        axis.axis("off")
    fig.tight_layout(pad=1.2)
    fig.savefig(output_dir / "visual_cem_pair.png", bbox_inches="tight", facecolor="white")
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", default="qwen")
    parser.add_argument("--model", default="qwen3-vl-plus")
    parser.add_argument(
        "--output",
        type=Path,
        default=REPOSITORY_ROOT / "docs" / "tempdocs" / "0903ReviewX视觉证据实验",
    )
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    records = [
        _run_case(case, output_dir=args.output, provider=args.provider, model=args.model)
        for case in CASES
    ]
    payload = {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "provider": args.provider,
        "model": args.model,
        "summary": _summarize(records),
        "cases": records,
    }
    (args.output / "results.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _write_report(args.output, payload)
    _render_pair_summary(args.output)
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
