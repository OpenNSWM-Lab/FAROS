import json
import re
from pathlib import Path

from packaging.requirements import Requirement


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _backend_requirement_names() -> set[str]:
    names: set[str] = set()
    requirements = PROJECT_ROOT / "backend" / "requirements.txt"
    for raw_line in requirements.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if line:
            names.add(Requirement(line).name.lower())
    return names


def test_runtime_direct_dependencies_are_explicitly_declared() -> None:
    declared = _backend_requirement_names()
    direct_runtime_dependencies = {
        "aiofiles",
        "certifi",
        "cryptography",
        "docker",
        "fastapi",
        "fpdf2",
        "httpx",
        "litellm",
        "matplotlib",
        "numpy",
        "openai",
        "pydantic",
        "python-dotenv",
        "python-multipart",
        "sqlalchemy",
        "sqlmodel",
        "starlette",
        "uvicorn",
    }

    assert direct_runtime_dependencies <= declared
    assert "fpdf" not in declared


def test_frontend_node_requirement_matches_vite_runtime() -> None:
    package = json.loads(
        (PROJECT_ROOT / "frontend" / "package.json").read_text(encoding="utf-8")
    )
    package_lock = json.loads(
        (PROJECT_ROOT / "frontend" / "package-lock.json").read_text(encoding="utf-8")
    )
    engine = package["engines"]["node"]
    match = re.search(r">=\s*(\d+)", engine)

    assert match is not None and int(match.group(1)) >= 18
    assert package_lock["packages"][""]["engines"]["node"] == engine


def test_gateway_template_forwards_authenticated_user_identity() -> None:
    caddyfile = (
        PROJECT_ROOT / "deploy" / "caddy" / "Caddyfile.example"
    ).read_text(encoding="utf-8")
    credentials = (
        PROJECT_ROOT / "deploy" / "systemd" / "faros-credentials.env.example"
    ).read_text(encoding="utf-8")

    assert "header_up X-Faros-User {http.auth.user.id}" in caddyfile
    assert "FAROS_CREDENTIAL_KEY=REPLACE_WITH_A_FERNET_KEY" in credentials
    assert "QWEN_API_KEY=" in credentials
