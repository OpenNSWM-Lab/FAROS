import json
import math
from types import SimpleNamespace

from app.agents.codegen import kernel
from app.agents.codegen.kernel import AgentKernel, CodeGenSession
from app.agents.codegen.skills.registry import CompileCheckSkill, ExecutionSmokeCheckSkill


class _BatchClient:
    def __init__(self):
        self.calls = []

    def chat(self, *, messages, **kwargs):
        self.calls.append(kwargs)
        files_block = messages[0].content.split("**Files:**", 1)[1].split("**Output", 1)[0]
        paths = []
        for line in files_block.splitlines():
            if line.startswith("- "):
                paths.append(line[2:].split(":", 1)[0])
        return SimpleNamespace(text=json.dumps({
            "files": [
                {"path": path, "content": f'"""Generated {path}."""\n'}
                for path in paths
            ],
        }))


def _agent(client=None) -> AgentKernel:
    agent = AgentKernel.__new__(AgentKernel)
    agent.client = client or _BatchClient()
    agent.model = "test-model"
    agent.language = "python"
    return agent


def test_fallback_tree_is_focused_on_reproducible_experiments():
    tree = _agent()._default_file_tree()
    paths = {item["path"] for item in tree["files"]}

    assert 22 <= len(paths) <= 30
    assert "configs/experiment.yaml" in paths
    assert "src/provenance.py" in paths
    assert "scripts/run_smoke.py" in paths
    assert "tests/test_smoke.py" in paths
    assert not any(path.startswith("src/api/") for path in paths)
    assert not any(path.startswith("src/db/") for path in paths)


def test_code_synthesis_uses_bounded_structured_batches():
    client = _BatchClient()
    agent = _agent(client)
    session = CodeGenSession(
        id="cgs_batch_test",
        projectId="cproj_batch_test",
        planLinkId="ppkg_test",
        providerName="qwen",
        model="test-model",
    )
    session.memory.design_doc = "A frozen scientific protocol."
    session.memory.file_tree = {
        "projectName": "experiment",
        "description": "Reproducible experiment",
        "files": [
            {"path": f"src/module_{index}.py", "description": "module", "type": "source"}
            for index in range(kernel._GENERATION_BATCH_SIZE * 2 + 1)
        ],
    }
    session.steps.append(kernel.StepResult(name="code_synthesis_batch", status="running"))

    agent._step_synthesize_batch(session, session, "Experiment")

    assert len(session.memory.generated_files) == kernel._GENERATION_BATCH_SIZE * 2 + 1
    assert len(client.calls) == math.ceil(len(session.memory.generated_files) / kernel._GENERATION_BATCH_SIZE)
    assert all(call["structured_output"] is True for call in client.calls)
    assert all(call["max_tokens"] == 4500 for call in client.calls)
    assert all(call["request_max_retries"] == 0 for call in client.calls)


def test_compile_check_rejects_invalid_python_syntax():
    result = CompileCheckSkill().execute(
        language="python",
        files={
            "README.md": "# Experiment\n" * 60,
            "requirements.txt": "pytest\n",
            ".gitignore": "results/\n",
            "src/method.py": "def broken(:\n    return 1\n",
            "src/metrics.py": "def score() -> float:\n    return 1.0\n",
            "src/provenance.py": "def manifest() -> dict:\n    return {}\n",
            "tests/test_data.py": "def test_data():\n    assert True\n",
            "tests/test_metrics.py": "def test_metrics():\n    assert True\n",
            "tests/test_smoke.py": "def test_smoke():\n    assert True\n",
            ".github/workflows/ci.yml": "name: ci\n",
            "docs/protocol.md": "# Protocol\n",
            "docs/limitations.md": "# Limitations\n",
            **{f"src/extra_{index}.py": '"""Module."""\n' for index in range(8)},
        },
    )

    assert result.ok is False
    assert any("syntax error" in item["message"].lower() for item in result.data["issues"])


def test_compile_check_rejects_unresolved_internal_imports_and_stub_metadata():
    files = {
        "README.md": "word " * 120,
        "requirements.txt": "pytest\n",
        ".gitignore": "results/\n",
        "src/package/__init__.py": '"""Package."""\n',
        "src/package/method.py": "from src.package.missing import helper\n",
        "src/package/metrics.py": "def score() -> float:\n    return 1.0\n",
        "src/package/provenance.py": "def manifest() -> dict:\n    return {}\n",
        "tests/test_data.py": "def test_data():\n    assert True\n",
        "tests/test_metrics.py": "def test_metrics():\n    assert True\n",
        "tests/test_smoke.py": "def test_smoke():\n    assert True\n",
        ".github/workflows/ci.yml": "name: ci\n",
        "configs/experiment.yaml": "checksum: placeholder for actual checksum\n",
        "data/manifest.json": '{"sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"}',
        "docs/protocol.md": "# Protocol\n",
        "docs/limitations.md": "# Limitations\n",
        **{f"src/package/extra_{index}.py": '"""Module."""\n' for index in range(6)},
    }

    result = CompileCheckSkill().execute(language="python", files=files)
    messages = [item["message"] for item in result.data["issues"]]

    assert result.ok is False
    assert any("Internal import does not resolve" in message for message in messages)
    assert any("CI workflow has no jobs" in message for message in messages)
    assert any("placeholder or fabricated provenance" in message for message in messages)
    assert any("checksum of an empty file" in message for message in messages)


def test_compile_check_rejects_heavy_top_level_imports_and_empty_tests():
    files = {
        "README.md": "word " * 120,
        "requirements.txt": "pytest\ntorch\n",
        ".gitignore": "results/\n",
        "src/package/__init__.py": '\"\"\"Package.\"\"\"\n',
        "src/package/method.py": "import torch\n\ndef score():\n    return 1.0\n",
        "src/package/metrics.py": "def score() -> float:\n    return 1.0\n",
        "src/package/provenance.py": "def manifest() -> dict:\n    return {}\n",
        "tests/test_data.py": "def test_data():\n    pass\n",
        "tests/test_metrics.py": "def test_metrics():\n    assert 1 == 1\n",
        "tests/test_smoke.py": "import pytest\n\ndef test_smoke():\n    with pytest.raises(ValueError):\n        raise ValueError('expected')\n",
        ".github/workflows/ci.yml": "name: ci\njobs:\n  test:\n    runs-on: ubuntu-latest\n",
        "docs/protocol.md": "# Protocol\n",
        "docs/limitations.md": "# Limitations\n",
        **{f"src/package/extra_{index}.py": '\"\"\"Module.\"\"\"\n' for index in range(6)},
    }

    result = CompileCheckSkill().execute(language="python", files=files)
    messages = [item["message"] for item in result.data["issues"]]

    assert result.ok is False
    assert any("Heavy optional dependency 'torch'" in message for message in messages)
    assert any("test_data" in message and "no executable assertion" in message for message in messages)
    assert any("default install" in message for message in messages)


def test_execution_smoke_check_requires_a_smoke_entrypoint():
    result = ExecutionSmokeCheckSkill().execute(files={"README.md": "experiment"})

    assert result.ok is False
    assert result.data["status"] == "failed"
    assert result.data["errorCount"] == 1


def test_interface_index_includes_function_signatures_and_dataclass_fields():
    index = _agent()._interface_index({
        "src/contracts.py": (
            "from dataclasses import dataclass\n\n"
            "@dataclass\nclass Result:\n    score: float\n    count: int\n\n"
            "def evaluate(result: Result, alpha: float = 0.05):\n    return result.score\n"
        ),
    })

    assert "class Result(score, count)" in index
    assert "evaluate(result: Result, alpha: float=0.05)" in index


def test_compile_check_rejects_public_test_reimplementations():
    files = {
        "README.md": "word " * 120,
        "requirements.txt": "pytest\n",
        ".gitignore": "results/\n",
        "src/package/__init__.py": '\"\"\"Package.\"\"\"\n',
        "src/package/metrics.py": "def score() -> float:\n    return 1.0\n",
        "src/package/data.py": "def load():\n    return []\n",
        "src/package/provenance.py": "def manifest() -> dict:\n    return {}\n",
        "tests/test_data.py": (
            "from src.package.data import load\n\n"
            "def calculate_metric(values):\n    return len(values)\n\n"
            "def test_data():\n    assert calculate_metric(load()) == 0\n"
        ),
        "tests/test_metrics.py": "from src.package.metrics import score\n\ndef test_metrics():\n    assert score() == 1.0\n",
        "tests/test_smoke.py": "from src.package.provenance import manifest\n\ndef test_smoke():\n    assert manifest() == {}\n",
        ".github/workflows/ci.yml": "name: ci\njobs:\n  test:\n    runs-on: ubuntu-latest\n",
        "docs/protocol.md": "# Protocol\n",
        "docs/limitations.md": "# Limitations\n",
        **{f"src/package/extra_{index}.py": '\"\"\"Module.\"\"\"\n' for index in range(6)},
    }

    result = CompileCheckSkill().execute(language="python", files=files)

    assert result.ok is False
    assert any("looks like a reimplementation" in issue["message"] for issue in result.data["issues"])


def test_verify_step_keeps_findings_when_quality_gate_fails():
    agent = _agent()
    agent.skills = SimpleNamespace(execute=lambda *_args, **_kwargs: SimpleNamespace(
        ok=False,
        data={"issues": [{"file": "README.md", "severity": "error"}], "fileCount": 1, "errorCount": 1},
        error=None,
    ))
    session = CodeGenSession(
        id="cgs_verify_test",
        projectId="cproj_verify_test",
        planLinkId=None,
        providerName="qwen",
        model="test-model",
    )
    session.memory.generated_files = {"README.md": "short"}
    session.steps.append(kernel.StepResult(name="verify_structure", status="running"))

    agent._step_verify(session, session)

    assert session.memory.verification_results == [{"file": "README.md", "severity": "error"}]
    assert "1 errors" in session.steps[-1].detail
