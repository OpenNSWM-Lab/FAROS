"""Compute-node inventory and conservative workload profile selection."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.paths import get_data_dir


_SNAPSHOT_TTL_SECONDS = 15.0
_snapshot_lock = threading.Lock()
_snapshot_cache: tuple[float, dict[str, Any]] | None = None


def _positive_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def _positive_float(name: str, default: float) -> float:
    try:
        return max(0.5, float(os.getenv(name, str(default))))
    except ValueError:
        return default


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def execution_role() -> str:
    return os.getenv("FAROS_EXECUTION_ROLE", "development").strip().lower() or "development"


def execution_is_allowed() -> bool:
    return execution_role() != "control"


def host_execution_is_allowed() -> bool:
    return _bool_env("FAROS_ALLOW_HOST_EXECUTION", execution_role() == "development")


def _memory_bytes() -> tuple[int, int]:
    values: dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
            key, raw = line.split(":", 1)
            values[key] = int(raw.strip().split()[0]) * 1024
    except (OSError, ValueError, IndexError):
        return 0, 0
    return values.get("MemTotal", 0), values.get("MemAvailable", 0)


def _gpu_inventory() -> list[dict[str, Any]]:
    command = [
        "nvidia-smi",
        "--query-gpu=index,name,memory.total,memory.free,utilization.gpu",
        "--format=csv,noheader,nounits",
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=3,
            check=True,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return []

    gpus: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        parts = [item.strip() for item in line.split(",")]
        if len(parts) != 5:
            continue
        try:
            gpus.append({
                "index": int(parts[0]),
                "name": parts[1],
                "memoryTotalMiB": int(parts[2]),
                "memoryFreeMiB": int(parts[3]),
                "utilizationPercent": int(parts[4]),
            })
        except ValueError:
            continue
    return gpus


def _docker_inventory() -> tuple[bool, bool]:
    if not shutil.which("docker"):
        return False, False
    try:
        result = subprocess.run(
            ["docker", "info", "--format", "{{json .Runtimes}}"],
            capture_output=True,
            text=True,
            timeout=4,
            check=True,
        )
        runtimes = json.loads(result.stdout or "{}")
        return True, "nvidia" in runtimes
    except (json.JSONDecodeError, subprocess.SubprocessError):
        return False, False


def _gib(value: int) -> float:
    return round(value / (1024 ** 3), 1) if value else 0.0


def get_compute_snapshot(*, refresh: bool = False) -> dict[str, Any]:
    """Return a cached, secret-free inventory suitable for the frontend."""

    global _snapshot_cache
    now = time.monotonic()
    with _snapshot_lock:
        if not refresh and _snapshot_cache and now - _snapshot_cache[0] < _SNAPSHOT_TTL_SECONDS:
            return dict(_snapshot_cache[1])

        total_memory, available_memory = _memory_bytes()
        try:
            disk = shutil.disk_usage(get_data_dir())
        except OSError:
            disk = shutil.disk_usage("/")
        gpus = _gpu_inventory()
        docker_available, nvidia_runtime = _docker_inventory()
        cpu_count = os.cpu_count() or 1
        disk_free_ratio = disk.free / disk.total if disk.total else 0.0
        warnings: list[str] = []
        if disk_free_ratio < 0.15:
            warnings.append("Compute storage has less than 15% free space; archive old runs before large experiments.")
        if execution_role() == "control":
            warnings.append("This node is control-only and rejects experiment execution.")
        if not docker_available:
            warnings.append("Docker isolation is unavailable.")

        snapshot = {
            "nodeName": os.getenv("FAROS_EXECUTION_NODE_NAME", socket.gethostname()),
            "role": execution_role(),
            "location": os.getenv("FAROS_EXECUTION_LOCATION", "local"),
            "acceptingJobs": execution_is_allowed(),
            "isolationRequired": not host_execution_is_allowed(),
            "runtime": {
                "dockerAvailable": docker_available,
                "nvidiaContainerRuntime": nvidia_runtime,
                "defaultBackend": os.getenv("SANDBOX_DEFAULT_BACKEND", "subprocess"),
                "maxConcurrent": _positive_int("SANDBOX_MAX_CONCURRENT", 2),
            },
            "cpu": {"logicalCores": cpu_count},
            "memory": {
                "totalGiB": _gib(total_memory),
                "availableGiB": _gib(available_memory),
            },
            "storage": {
                "totalGiB": _gib(disk.total),
                "freeGiB": _gib(disk.free),
                "freePercent": round(disk_free_ratio * 100, 1),
            },
            "gpus": gpus,
            "warnings": warnings,
        }
        _snapshot_cache = (now, snapshot)
        return dict(snapshot)


@dataclass(frozen=True)
class ExecutionProfile:
    """Resolved resource limits for one isolated workspace."""

    name: str
    cpu_limit: float
    memory_limit: str
    pids_limit: int
    timeout_seconds: int
    gpu_count: int
    image: str
    reason: str

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "cpuLimit": self.cpu_limit,
            "memoryLimit": self.memory_limit,
            "pidsLimit": self.pids_limit,
            "timeoutSeconds": self.timeout_seconds,
            "gpuCount": self.gpu_count,
            "image": self.image,
            "reason": self.reason,
        }


def _project_signals(repo_dir: str | Path) -> tuple[bool, bool, list[str]]:
    root = Path(repo_dir)
    gpu_tokens = ("torch", "tensorflow", "cupy", "cuda", "jax", "transformers")
    heavy_tokens = ("scikit-learn", "sklearn", "pandas", "numpy", "train.py")
    text_parts: list[str] = []
    requirements = root / "requirements.txt"
    if requirements.is_file():
        try:
            text_parts.append(requirements.read_text(encoding="utf-8", errors="ignore")[:200_000])
        except OSError:
            pass
    source_paths = list((root / "src").glob("*.py")) if (root / "src").is_dir() else []
    source_paths.extend(path for path in (root / "main.py", root / "train.py") if path.is_file())
    for path in source_paths[:30]:
        try:
            text_parts.append(path.read_text(encoding="utf-8", errors="ignore")[:80_000])
        except OSError:
            continue
    combined = "\n".join(text_parts).lower()
    gpu = any(token in combined for token in gpu_tokens)
    heavy = gpu or (root / "train.py").is_file() or (root / "src" / "train.py").is_file()
    heavy = heavy or any(token in combined for token in heavy_tokens)
    matched = [token for token in (*gpu_tokens, *heavy_tokens) if token in combined]
    return gpu, heavy, sorted(set(matched))


def resolve_execution_profile(
    repo_dir: str | Path,
    requested: str = "auto",
) -> ExecutionProfile:
    """Resolve an explicit or inferred profile without overcommitting the host."""

    requested = (requested or "auto").strip().lower()
    if requested not in {"auto", "light", "standard", "gpu"}:
        raise ValueError("Execution profile must be auto, light, standard, or gpu")
    snapshot = get_compute_snapshot()
    gpu_signal, heavy_signal, signals = _project_signals(repo_dir)
    gpu_ready = bool(snapshot["gpus"]) and bool(snapshot["runtime"]["nvidiaContainerRuntime"])

    if requested == "auto":
        if gpu_signal and gpu_ready:
            selected = "gpu"
            reason = f"GPU libraries detected ({', '.join(signals[:4])})."
        elif heavy_signal:
            selected = "standard"
            reason = f"Training or numeric workload detected ({', '.join(signals[:4]) or 'project structure'})."
        else:
            selected = "light"
            reason = "No training or GPU dependency was detected."
    else:
        selected = requested
        reason = f"Profile explicitly selected by the user: {selected}."

    if selected == "gpu" and not gpu_ready:
        raise ValueError("GPU profile requested, but no GPU-enabled Docker runtime is available on this execution node")

    cpu_count = int(snapshot["cpu"]["logicalCores"] or 1)
    cpu_defaults = {
        "light": min(2, cpu_count),
        "standard": min(8, cpu_count),
        "gpu": min(8, cpu_count),
    }
    cpu_limit = _positive_float(
        f"FAROS_PROFILE_{selected.upper()}_CPUS",
        float(cpu_defaults[selected]),
    )
    memory_defaults = {"light": "2g", "standard": "16g", "gpu": "24g"}
    timeout_defaults = {"light": 600, "standard": 1800, "gpu": 3600}
    pids_defaults = {"light": 128, "standard": 512, "gpu": 768}
    cpu_image = os.getenv("SANDBOX_DOCKER_IMAGE", "faros/codegen-test:3.12")
    gpu_image = os.getenv("SANDBOX_GPU_IMAGE", cpu_image)
    return ExecutionProfile(
        name=selected,
        cpu_limit=cpu_limit,
        memory_limit=os.getenv(
            f"FAROS_PROFILE_{selected.upper()}_MEMORY",
            memory_defaults[selected],
        ),
        pids_limit=_positive_int(
            f"FAROS_PROFILE_{selected.upper()}_PIDS",
            pids_defaults[selected],
        ),
        timeout_seconds=_positive_int(
            f"FAROS_PROFILE_{selected.upper()}_TIMEOUT",
            timeout_defaults[selected],
        ),
        gpu_count=1 if selected == "gpu" else 0,
        image=gpu_image if selected == "gpu" else cpu_image,
        reason=reason,
    )
