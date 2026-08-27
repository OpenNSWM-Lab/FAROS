"""Standalone acceptance cases for the seven scientific execution classes."""

EXECUTION_CLASS_CASES = [
    {
        "expected": "computational_ready",
        "source": {
            "runId": "run_compute",
            "questionId": "q_compute",
            "researchQuestion": "Analyze a benchmark dataset with a Python algorithm.",
            "availableInputs": ["versioned benchmark dataset"],
            "researchPlan": {
                "requiredData": ["benchmark dataset"],
                "steps": [{
                    "id": "step-1", "title": "Analyze", "method": ["Python analysis"],
                    "metrics": ["accuracy"],
                    "stopConditions": ["Stop after fixed seed evaluation"],
                }],
            },
        },
    },
    {
        "expected": "simulation_ready",
        "source": {
            "runId": "run_sim", "questionId": "q_sim",
            "researchQuestion": "Run a Monte Carlo simulation and parameter sweep.",
            "availableInputs": ["fixed simulation configuration"],
            "researchPlan": {"steps": [{
                "id": "step-1", "title": "Simulate", "metrics": ["mean_error"],
                "stopConditions": ["Stop after 1000 trials"],
            }]},
        },
    },
    {
        "expected": "data_required",
        "source": {
            "runId": "run_data", "questionId": "q_data",
            "researchQuestion": "Analyze a clinical dataset that has not been provided.",
            "researchPlan": {
                "requiredData": ["clinical dataset"],
                "steps": [{
                    "id": "step-1", "title": "Analyze", "metrics": ["auc"],
                    "stopConditions": ["Stop on invalid schema"],
                }],
            },
        },
    },
    {
        "expected": "instrument_required",
        "source": {
            "runId": "run_instrument", "questionId": "q_instrument",
            "researchQuestion": "Collect spectra with a calibrated spectrometer in a wet lab.",
            "researchPlan": {"steps": [{
                "id": "step-1", "title": "Measure", "metrics": ["signal_to_noise"],
                "stopConditions": ["Stop if calibration fails"],
            }]},
        },
    },
    {
        "expected": "ethics_review_required",
        "source": {
            "runId": "run_ethics", "questionId": "q_ethics",
            "researchQuestion": "Run a clinical trial with patient personal data and informed consent.",
            "researchPlan": {"steps": [{
                "id": "step-1", "title": "Recruit", "metrics": ["response_rate"],
                "stopConditions": ["Stop on adverse event"],
            }]},
        },
    },
    {
        "expected": "proof_required",
        "source": {
            "runId": "run_proof", "questionId": "q_proof",
            "researchQuestion": "Prove that the theorem holds using a formal proof.",
            "researchPlan": {"steps": [{
                "id": "step-1", "title": "Prove", "metrics": ["proof_checked"],
                "stopConditions": ["Stop when the proof assistant accepts"],
            }]},
        },
    },
    {
        "expected": "protocol_only",
        "source": {
            "runId": "run_protocol", "questionId": "q_protocol",
            "researchQuestion": "Describe how this broad scientific question might be investigated.",
            "researchPlan": {"steps": [{
                "id": "step-1", "title": "Draft protocol", "metrics": ["review_complete"],
                "stopConditions": ["Stop after expert review"],
            }]},
        },
    },
]
