from app.services.plan_package_llm_schema import validate_llm_plan_output


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
