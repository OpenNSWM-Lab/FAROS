from app.services.plan_package_llm_schema import (
    validate_llm_plan_core_output,
    validate_llm_plan_output,
    validate_llm_plan_stage_output,
)


def test_validate_llm_plan_core_output_rejects_stage_write():
    parsed, issues = validate_llm_plan_core_output(
        {
            "researchQuestion": "Can verification improve citation faithfulness?",
            "hypothesis": (
                "Verification increases citation faithfulness; "
                "reject if mean delta is not positive."
            ),
            "constants": {"baseline": "vanilla RAG"},
            "stages": [],
        }
    )

    assert parsed is None
    assert issues == ["Core output contains forbidden keys: stages"]


def test_validate_llm_plan_stage_output_accepts_one_existing_stage_shape():
    parsed, issues = validate_llm_plan_stage_output(
        {
            "stage": {
                "title": "Controlled evaluation",
                "goal": "Compare verification with vanilla RAG.",
                "method": "Use the preregistered split and fixed retrieval corpus.",
                "steps": [
                    {
                        "title": "Measure citation faithfulness",
                        "desc": "Run both methods on the same questions.",
                        "method": "Score claim-level attribution with a frozen evaluator.",
                        "outputs": [
                            {"type": "metrics", "name": "citation_metrics.json"}
                        ],
                        "expected": [
                            {
                                "metric": "citation faithfulness",
                                "target": (
                                    "mean_delta > 0 with 95% confidence interval excluding 0"
                                ),
                            }
                        ],
                    }
                ],
            }
        }
    )

    assert issues == []
    assert parsed is not None
    assert parsed["stage"]["title"] == "Controlled evaluation"


def test_validate_llm_plan_output_ignores_recoverable_expected_metrics_top_level():
    parsed, issues = validate_llm_plan_output(
        {
            "researchQuestion": "Can citation-aware RAG improve high-risk QA?",
            "hypothesis": "Citation verification improves refusal and traceability.",
            "expectedMetrics": ["citation faithfulness", "refusal accuracy"],
            "stages": [
                {
                    "title": "Validation design",
                    "goal": "Define validation",
                    "method": "Plan metrics",
                    "steps": [
                        {
                            "title": "Specify metrics",
                            "desc": "Define planned metrics",
                            "method": "Map metrics to hypothesis",
                            "outputs": [{"type": "metrics", "name": "metrics.json"}],
                            "expected": [
                                {
                                    "metric": "citation faithfulness",
                                    "target": "specified before implementation",
                                }
                            ],
                        }
                    ],
                }
            ],
        }
    )

    assert issues == []
    assert parsed is not None
    assert "expectedMetrics" not in parsed


def test_validate_llm_plan_output_coerces_common_llm_list_shape_errors():
    parsed, issues = validate_llm_plan_output(
        {
            "researchQuestion": "Can citation-aware RAG improve high-risk QA?",
            "hypothesis": "Citation verification improves refusal and traceability.",
            "stages": {
                "baseline": {
                    "id": "stage-1",
                    "title": "Baseline",
                    "goal": "Establish baselines",
                    "method": "Compare against retrieval-only and vanilla RAG",
                    "dependsOn": "stage-0",
                    "steps": {
                        "id": "s1-baseline",
                        "title": "Run baseline evaluation",
                        "desc": None,
                        "method": "Evaluate citation faithfulness and refusal",
                        "inputFrom": "retrieved_docs",
                        "outputs": {"type": "metrics", "name": "baseline_metrics.json"},
                        "expected": {
                            "metric": "citation faithfulness",
                            "target": "specified before implementation",
                        },
                    },
                }
            },
        }
    )

    assert issues == []
    assert parsed is not None
    assert isinstance(parsed["stages"], list)
    assert parsed["stages"][0]["dependsOn"] == ["stage-0"]
    assert isinstance(parsed["stages"][0]["steps"], list)
    assert parsed["stages"][0]["steps"][0]["desc"] == ""
    assert parsed["stages"][0]["steps"][0]["inputFrom"] == ["retrieved_docs"]
    assert parsed["stages"][0]["steps"][0]["outputs"][0]["name"] == "baseline_metrics.json"
    assert parsed["stages"][0]["steps"][0]["expected"][0]["metric"] == "citation faithfulness"


def test_validate_llm_plan_output_ignores_misplaced_nested_only_top_level_keys():
    evidence_refs = [{"type": "gap", "id": "gap-1"}]
    expected = [{"metric": "recovery rate", "target": "specified before implementation"}]
    parsed, issues = validate_llm_plan_output(
        {
            "researchQuestion": "Can self-review improve research-agent reliability?",
            "hypothesis": "Evidence checks reduce unrecovered planning errors.",
            "stages": [
                {
                    "title": "Reliability evaluation",
                    "steps": [
                        {
                            "title": "Measure recovery",
                            "desc": "Measure recovery against a no-review baseline.",
                            "method": "Compare self-review with no-review baselines.",
                            "outputs": [{"type": "metrics", "name": "recovery.json"}],
                            "expected": expected,
                            "evidenceRefs": evidence_refs,
                        }
                    ],
                }
            ],
            "evidenceRefs": evidence_refs,
            "expected": expected,
            "desc": "Measure recovery against a no-review baseline.",
            "metric": "recovery rate",
            "target": "specified before implementation",
        }
    )

    assert issues == []
    assert parsed is not None
    assert "evidenceRefs" not in parsed
    assert "expected" not in parsed
    assert "desc" not in parsed
    assert "metric" not in parsed
    assert "target" not in parsed


def test_validate_llm_plan_output_rejects_orphan_nested_only_top_level_key():
    parsed, issues = validate_llm_plan_output(
        {
            "stages": [{"title": "Reliability evaluation", "steps": []}],
            "metric": "orphan metric not present in any step",
        }
    )

    assert parsed is None
    assert issues == ["LLM output contains forbidden top-level keys: metric"]


def test_validate_llm_plan_output_rejects_empty_structural_pseudo_duplicate():
    parsed, issues = validate_llm_plan_output(
        {
            "stages": [
                {
                    "title": "Reliability evaluation",
                    "steps": [
                        {
                            "title": "Measure recovery",
                            "expected": [{"metric": "recovery rate", "target": "higher"}],
                        }
                    ],
                }
            ],
            "expected": [{}],
        }
    )

    assert parsed is None
    assert issues == ["LLM output contains forbidden top-level keys: expected"]


def test_validate_llm_plan_output_rejects_partial_object_without_required_sections():
    parsed, issues = validate_llm_plan_output(
        {
            "researchQuestion": "Can self-review improve reliability?",
        }
    )

    assert parsed is None
    assert issues == ["LLM output is missing required writable sections: stages"]
