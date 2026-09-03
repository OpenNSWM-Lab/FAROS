import json
from pathlib import Path

from app.llm.provider_client import ChatResponse
from app.modules.review import artifact_collector
from app.modules.review.reviewx_models import Claim, Evidence, SourceSpan
from app.modules.review.visual_evidence import audit_visual_evidence


PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"reviewx-visual-fixture"


class FakeVisionClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        text = self.payload if isinstance(self.payload, str) else json.dumps(self.payload)
        return ChatResponse(
            text=text,
            usage={"prompt_tokens": 120, "completion_tokens": 80, "total_tokens": 200},
            latency_ms=25,
            raw_provider="qwen",
            model="qwen3-vl-plus",
            finish_reason="stop",
        )


def _claim() -> Claim:
    return Claim(
        id="claim_001",
        paperId="paper_visual",
        text="The proposed method improves F1 from 0.70 to 0.82.",
        claimType="performance",
        importance="high",
        requiresEvidence=True,
        sourceSpan=SourceSpan(file="main.tex", section="Results", line=42),
    )


def _visual_fixture(tmp_path: Path):
    image = tmp_path / "papers" / "paper_visual" / "latex" / "figures" / "result.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(PNG_BYTES)
    figure = {
        "id": "fig_result",
        "source": "paper_latex",
        "sourcePath": "data/papers/paper_visual/latex/figures/result.png",
        "absolutePath": str(image),
        "mimeType": "image/png",
        "caption": "F1 improves from 0.70 to 0.82.",
        "title": "F1 comparison",
    }
    evidence = Evidence(
        id="evidence_figure",
        paperId="paper_visual",
        evidenceType="figure",
        sourceModule="paper",
        sourcePath=figure["sourcePath"],
        summary=figure["caption"],
        metadata={"figureId": "fig_result", "visualAuditEligible": True},
    )
    return figure, evidence


def _run(tmp_path: Path, payload):
    claim = _claim()
    figure, evidence = _visual_fixture(tmp_path)
    client = FakeVisionClient(payload)
    result = audit_visual_evidence(
        paper={"id": "paper_visual", "title": "Visual audit fixture"},
        claims=[claim],
        evidence=[evidence],
        links={claim.id: [evidence.id]},
        artifacts={"visualFigures": [figure]},
        provider_name="qwen",
        visual_model="qwen3-vl-plus",
        budget_mode="balanced",
        enabled=True,
        client=client,
        data_root=str(tmp_path),
    )
    return result, client


def test_visual_audit_detects_trend_mismatch_and_sends_image_bytes(tmp_path: Path):
    result, client = _run(tmp_path, {
        "chartType": "line",
        "readable": True,
        "observations": ["The method line ends below the baseline line."],
        "captionStatus": "contradicted",
        "captionRationale": "The visible trend is opposite to the caption.",
        "claimAssessments": [{
            "claimId": "claim_001",
            "status": "contradicted",
            "verdict": "The plotted method does not improve F1.",
            "confidence": 0.93,
        }],
        "anomalies": [{
            "type": "trend_reversal",
            "claimId": "claim_001",
            "severity": "major",
            "description": "The plotted trend reverses the claimed improvement.",
            "suggestedFix": "Regenerate the chart from the audited metrics.",
            "acceptanceCriterion": "Plotted values and the claim agree.",
            "confidence": 0.91,
        }],
    })

    assert result.trace["status"] == "completed"
    assert result.trace["auditedFigureCount"] == 1
    assert result.verifications[0].supportStatus == "contradicted"
    assert result.findings[0].riskType == "visual_trend_reversal"
    assert result.findings[0].severity == "major"
    assert result.risk_nodes[0].category == "visual_evidence"
    content = client.calls[0]["messages"][0].content
    assert content[0]["type"] == "text"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert "data:image" not in json.dumps(result.trace)


def test_clean_visual_audit_adds_support_without_a_finding(tmp_path: Path):
    result, _client = _run(tmp_path, {
        "chartType": "bar",
        "readable": True,
        "observations": ["The method bar is labelled 0.82 and the baseline 0.70."],
        "captionStatus": "consistent",
        "captionRationale": "Visible labels match the caption.",
        "claimAssessments": [{
            "claimId": "claim_001",
            "status": "supported",
            "verdict": "The visible values support the claim.",
            "confidence": 0.9,
        }],
        "anomalies": [],
    })

    assert len(result.verifications) == 1
    assert result.verifications[0].supportStatus == "supported"
    assert result.findings == []
    assert result.trace["anomalyCount"] == 0


def test_related_visual_mismatches_are_collapsed_into_one_action(tmp_path: Path):
    result, _client = _run(tmp_path, {
        "chartType": "bar",
        "readable": True,
        "observations": ["Baseline is 0.82 and Proposed is 0.70."],
        "captionStatus": "contradicted",
        "captionRationale": "Visible values are reversed.",
        "claimAssessments": [{
            "claimId": "claim_001",
            "status": "contradicted",
            "verdict": "The values and direction contradict the claim.",
            "confidence": 0.95,
        }],
        "anomalies": [
            {
                "type": "numeric_mismatch",
                "claimId": "claim_001",
                "severity": "blocker",
                "description": "The two values are swapped.",
                "suggestedFix": "Regenerate from metrics.",
                "acceptanceCriterion": "Values match metrics.",
                "confidence": 0.95,
            },
            {
                "type": "trend_reversal",
                "claimId": "claim_001",
                "severity": "blocker",
                "description": "The direction is reversed.",
                "suggestedFix": "Align the direction.",
                "acceptanceCriterion": "Direction matches the claim.",
                "confidence": 0.93,
            },
        ],
    })

    assert len(result.findings) == 1
    assert result.findings[0].riskType == "visual_numeric_mismatch"
    assert "values are swapped" in result.findings[0].description
    assert "direction is reversed" in result.findings[0].description


def test_malformed_visual_response_fails_soft(tmp_path: Path):
    result, _client = _run(tmp_path, "not valid json")

    assert result.verifications == []
    assert result.findings == []
    assert result.trace["status"] == "failed_soft"
    assert result.trace["skipped"] is True
    assert result.trace["calls"][0]["status"] == "failed_soft"


def test_distant_unrelated_claim_cannot_become_visual_finding(tmp_path: Path):
    claim = _claim()
    claim.sourceSpan.file = "SI.tex"
    claim.sourceSpan.line = 276
    figure, evidence = _visual_fixture(tmp_path)
    figure["sourceTexPath"] = "SI.tex"
    figure["sourceLine"] = 260
    figure["caption"] = "PaperQA2 precision ablations on LitQA2 with 95% confidence intervals."
    client = FakeVisionClient({
        "chartType": "bar",
        "readable": True,
        "observations": ["The chart is readable."],
        "captionStatus": "consistent",
        "captionRationale": "The chart agrees with its caption.",
        "claimAssessments": [{
            "claimId": claim.id,
            "status": "contradicted",
            "verdict": "This unrelated claim is not present in the figure.",
            "confidence": 0.9,
        }],
        "anomalies": [],
    })

    result = audit_visual_evidence(
        paper={"id": "paper_visual", "title": "Visual audit fixture"},
        claims=[claim],
        evidence=[evidence],
        links={claim.id: [evidence.id]},
        artifacts={"visualFigures": [figure]},
        provider_name="qwen",
        visual_model="qwen3-vl-plus",
        budget_mode="balanced",
        enabled=True,
        client=client,
        data_root=str(tmp_path),
    )

    assert claim.text not in client.calls[0]["messages"][0].content[0]["text"]
    assert result.verifications == []
    assert result.findings == []
    assert result.trace["captionCheckCount"] == 1
    assert result.trace["checkCount"] == 1


def test_visual_audit_rejects_path_outside_data_root(tmp_path: Path):
    outside = tmp_path.parent / "outside-reviewx.png"
    outside.write_bytes(PNG_BYTES)
    client = FakeVisionClient({"captionStatus": "consistent"})
    try:
        result = audit_visual_evidence(
            paper={"id": "paper_visual", "title": "Traversal fixture"},
            claims=[_claim()],
            evidence=[],
            links={},
            artifacts={"visualFigures": [{
                "id": "outside",
                "absolutePath": str(outside),
                "sourcePath": "../outside-reviewx.png",
                "mimeType": "image/png",
            }]},
            provider_name="qwen",
            visual_model="qwen3-vl-plus",
            budget_mode="balanced",
            enabled=True,
            client=client,
            data_root=str(tmp_path),
        )
    finally:
        outside.unlink(missing_ok=True)

    assert result.trace["status"] == "skipped"
    assert result.trace["selectedFigureCount"] == 0
    assert client.calls == []


def test_artifact_collector_discovers_latex_figure_and_rejects_fake_image(monkeypatch, tmp_path: Path):
    paper_id = "paper_visual"
    latex_root = tmp_path / "papers" / paper_id / "latex"
    figures_dir = latex_root / "Figures"
    figures_dir.mkdir(parents=True)
    (figures_dir / "result.png").write_bytes(PNG_BYTES)
    (figures_dir / "fake.png").write_text("not an image", encoding="utf-8")
    tex = r"""
    \begin{figure}
      \includegraphics{Figures/result.png}
      \caption{F1 comparison on the held-out set.}
    \end{figure}
    \includegraphics{Figures/fake.png}
    """

    monkeypatch.setattr(artifact_collector, "_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(artifact_collector, "_BASE_DIR", str(tmp_path.parent))
    monkeypatch.setattr(artifact_collector, "get_paper", lambda _paper_id: {
        "id": paper_id,
        "experimentIds": [],
        "selectedFigures": [],
    })
    monkeypatch.setattr(artifact_collector, "list_paper_files", lambda _paper_id: [{
        "path": "main.tex",
        "name": "main.tex",
        "isDir": False,
    }])
    monkeypatch.setattr(artifact_collector, "read_paper_file", lambda _paper_id, _path: tex)

    artifacts = artifact_collector.collect_reviewx_artifacts(paper_id)

    assert len(artifacts["visualFigures"]) == 1
    visual = artifacts["visualFigures"][0]
    assert visual["source"] == "paper_latex"
    assert visual["mimeType"] == "image/png"
    assert visual["caption"] == "F1 comparison on the held-out set."
    assert visual["sourcePath"].endswith("Figures/result.png")
