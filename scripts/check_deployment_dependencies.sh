#!/usr/bin/env bash
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROLE="local"
PYTHON_BIN="${FAROS_PYTHON:-}"
REQUIRE_GPU="${FAROS_REQUIRE_GPU:-0}"
FAILURES=0
WARNINGS=0

usage() {
  cat <<'EOF'
Usage: ./scripts/check_deployment_dependencies.sh [options]

Options:
  --role local|compute|gateway|public|all
  --python PATH       Python interpreter used by the backend
  --require-gpu       Require the NVIDIA runtime and GPU sandbox image
  -h, --help          Show this message

Environment overrides:
  FAROS_PYTHON, DATA_DIR, MPLCONFIGDIR, FAROS_PDF_FONT
  SANDBOX_DOCKER_IMAGE, SANDBOX_GPU_IMAGE, FAROS_FRONTEND_ROOT
  FAROS_CADDYFILE, FAROS_REQUIRE_GPU
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --role)
      [[ $# -ge 2 ]] || { printf 'Missing value for --role\n' >&2; exit 2; }
      ROLE="$2"
      shift 2
      ;;
    --python)
      [[ $# -ge 2 ]] || { printf 'Missing value for --python\n' >&2; exit 2; }
      PYTHON_BIN="$2"
      shift 2
      ;;
    --require-gpu)
      REQUIRE_GPU=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'Unknown option: %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ "$ROLE" == "public" ]]; then
  ROLE="gateway"
fi
case "$ROLE" in
  local|compute|gateway|all) ;;
  *)
    printf 'Unsupported role: %s\n' "$ROLE" >&2
    exit 2
    ;;
esac

ok() {
  printf '[ok]   %s\n' "$1"
}

fail() {
  printf '[fail] %s\n' "$1"
  FAILURES=$((FAILURES + 1))
}

warn() {
  printf '[warn] %s\n' "$1"
  WARNINGS=$((WARNINGS + 1))
}

has_command() {
  command -v "$1" >/dev/null 2>&1
}

require_command() {
  local name="$1"
  local purpose="$2"
  if has_command "$name"; then
    ok "$name: $purpose"
  else
    fail "$name is required for $purpose"
  fi
}

optional_command() {
  local name="$1"
  local purpose="$2"
  if has_command "$name"; then
    ok "$name: $purpose"
  else
    warn "$name is not installed; $purpose is unavailable"
  fi
}

resolve_python() {
  if [[ -n "$PYTHON_BIN" ]]; then
    return
  fi
  if [[ -x "$ROOT_DIR/backend/.venv/bin/python" ]]; then
    PYTHON_BIN="$ROOT_DIR/backend/.venv/bin/python"
  elif [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
    PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
  elif has_command python3; then
    PYTHON_BIN="$(command -v python3)"
  elif has_command python; then
    PYTHON_BIN="$(command -v python)"
  fi
}

check_python_runtime() {
  resolve_python
  if [[ -z "$PYTHON_BIN" ]] || [[ ! -x "$PYTHON_BIN" ]]; then
    fail "Python interpreter not found; set FAROS_PYTHON or create backend/.venv"
    return
  fi

  local version
  version="$("$PYTHON_BIN" -c 'import platform; print(platform.python_version())' 2>/dev/null)"
  if "$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
    ok "Python $version ($PYTHON_BIN)"
  else
    fail "Python 3.11+ is required; selected interpreter is $version"
    return
  fi

  local package_report
  package_report="$("$PYTHON_BIN" - "$ROOT_DIR/backend/requirements.txt" <<'PY' 2>&1
import importlib
import importlib.metadata as metadata
import re
import sys

requirements_path = sys.argv[1]
aliases = {
    "python-multipart": "multipart",
    "python-dotenv": "dotenv",
    "fpdf2": "fpdf",
}
issues = []

try:
    from packaging.requirements import Requirement
except ImportError:
    Requirement = None

for raw_line in open(requirements_path, encoding="utf-8"):
    line = raw_line.split("#", 1)[0].strip()
    if not line or line.startswith("-"):
        continue
    if Requirement is not None:
        requirement = Requirement(line)
        name = requirement.name
        specifier = requirement.specifier
    else:
        name = re.split(r"[<>=!~;\[]", line, maxsplit=1)[0].strip()
        specifier = None
    try:
        installed = metadata.version(name)
    except metadata.PackageNotFoundError:
        issues.append(f"missing Python distribution: {name}")
        continue
    if specifier and not specifier.contains(installed, prereleases=True):
        issues.append(f"version mismatch: {name} {installed} does not satisfy {specifier}")
        continue
    module = aliases.get(name.lower(), name.replace("-", "_"))
    try:
        importlib.import_module(module)
    except Exception as exc:
        issues.append(f"cannot import {module} ({name}): {type(exc).__name__}: {exc}")

try:
    legacy_fpdf = metadata.version("fpdf")
except metadata.PackageNotFoundError:
    legacy_fpdf = None
if legacy_fpdf:
    issues.append(
        f"legacy fpdf {legacy_fpdf} is installed; remove it because it conflicts with fpdf2"
    )

if issues:
    print("\n".join(issues))
    raise SystemExit(1)
PY
)"
  if [[ $? -eq 0 ]]; then
    ok "backend Python distributions and imports match requirements.txt"
  else
    while IFS= read -r line; do
      [[ -n "$line" ]] && fail "$line"
    done <<< "$package_report"
  fi

  if "$PYTHON_BIN" -m pip --version >/dev/null 2>&1; then
    local pip_report
    pip_report="$($PYTHON_BIN -m pip check 2>&1)"
    if [[ $? -eq 0 ]]; then
      ok "pip dependency graph has no conflicts"
    else
      fail "pip reports dependency conflicts: $pip_report"
    fi
  else
    warn "pip is absent from the selected environment; package presence was checked via importlib metadata"
  fi

  if PYTHONPATH="$ROOT_DIR/backend${PYTHONPATH:+:$PYTHONPATH}" "$PYTHON_BIN" -c '
import numpy as np
from experiments.reviewx_oscillator.run import analytic_free_decay, simulate_trajectory
t = np.linspace(0.0, 2.0, 9)
error = np.max(np.abs(simulate_trajectory(1.2, 0.1, 1.0, t, amplitude=0.0, initial_state=(1.0, 0.0)) - analytic_free_decay(1.2, 0.1, t)))
raise SystemExit(0 if error < 1e-8 else 1)
' >/dev/null 2>&1; then
    ok "SciPy adaptive-oscillator solver import and analytic smoke check"
  else
    fail "adaptive-oscillator solver smoke check failed"
  fi
}

check_node_runtime() {
  require_command node "building and running the frontend toolchain"
  require_command npm "installing the locked frontend dependencies"
  if ! has_command node || ! has_command npm; then
    return
  fi

  local node_version node_major
  node_version="$(node -p 'process.versions.node' 2>/dev/null)"
  node_major="${node_version%%.*}"
  if [[ "$node_major" =~ ^[0-9]+$ ]] && (( node_major >= 18 )); then
    ok "Node.js $node_version"
  else
    fail "Node.js 18+ is required; found ${node_version:-unknown}"
  fi

  if [[ ! -d "$ROOT_DIR/frontend/node_modules" ]]; then
    fail "frontend/node_modules is absent; run 'cd frontend && npm ci'"
  elif (cd "$ROOT_DIR/frontend" && npm ls --depth=0 >/dev/null 2>&1); then
    ok "frontend dependencies match package-lock.json"
  else
    fail "frontend dependency tree is incomplete; run 'cd frontend && npm ci'"
  fi
}

check_tex_runtime() {
  local required="$1"
  local missing=0
  local command_name
  for command_name in latexmk xelatex pdflatex bibtex kpsewhich; do
    if ! has_command "$command_name"; then
      if [[ "$required" == "required" ]]; then
        fail "$command_name is required for formal paper PDF compilation"
      else
        warn "$command_name is absent; formal paper PDF compilation is unavailable"
      fi
      missing=1
    fi
  done
  if [[ "$missing" -eq 0 ]]; then
    ok "LaTeX command chain is installed"

    local tex_file
    for tex_file in ctexart.cls geometry.sty booktabs.sty longtable.sty multirow.sty natbib.sty; do
      if kpsewhich "$tex_file" >/dev/null 2>&1; then
        ok "TeX package: $tex_file"
      elif [[ "$required" == "required" ]]; then
        fail "TeX package is missing: $tex_file"
      else
        warn "TeX package is missing: $tex_file"
      fi
    done
    if kpsewhich algorithm2e.sty >/dev/null 2>&1; then
      ok "optional TeX package: algorithm2e.sty"
    else
      warn "algorithm2e.sty is absent; FAROS will use its built-in algorithm fallback"
    fi
  fi

  local font_path="${FAROS_PDF_FONT:-}"
  if [[ -n "$font_path" ]]; then
    if [[ -f "$font_path" ]]; then
      ok "CJK fallback font: $font_path"
    else
      fail "FAROS_PDF_FONT does not exist: $font_path"
    fi
    return
  fi
  for font_path in \
    /usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc \
    /usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc \
    /usr/share/fonts/truetype/arphic/uming.ttc; do
    if [[ -f "$font_path" ]]; then
      ok "CJK fallback font: $font_path"
      return
    fi
  done
  if [[ -f /usr/share/fonts/truetype/dejavu/DejaVuSans.ttf ]] && \
     [[ -f /usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf ]]; then
    ok "composite Latin/CJK fallback fonts: DejaVu Sans + Droid Sans Fallback"
    return
  fi
  if [[ "$required" == "required" ]]; then
    fail "no supported CJK font was found for the fpdf2 fallback renderer"
  else
    warn "no supported CJK font was found for the fpdf2 fallback renderer"
  fi
}

check_docker_runtime() {
  require_command docker "isolated Code/Experiment execution"
  if ! has_command docker; then
    return
  fi
  if docker info >/dev/null 2>&1; then
    ok "Docker daemon is reachable by the current user"
  else
    fail "Docker daemon is unavailable or the current user lacks permission"
    return
  fi

  local cpu_image="${SANDBOX_DOCKER_IMAGE:-faros/codegen-test:3.12}"
  local gpu_image="${SANDBOX_GPU_IMAGE:-faros/codegen-gpu:cuda12.4}"
  if docker image inspect "$cpu_image" >/dev/null 2>&1; then
    ok "CPU sandbox image: $cpu_image"
  else
    fail "CPU sandbox image is missing: $cpu_image"
  fi

  if [[ "$REQUIRE_GPU" == "1" ]]; then
    require_command nvidia-smi "GPU experiment scheduling"
    if ! docker info --format '{{json .Runtimes}}' 2>/dev/null | grep -q 'nvidia'; then
      fail "Docker NVIDIA runtime is not configured"
    else
      ok "Docker NVIDIA runtime is configured"
    fi
    if docker image inspect "$gpu_image" >/dev/null 2>&1; then
      ok "GPU sandbox image: $gpu_image"
    else
      fail "GPU sandbox image is missing: $gpu_image"
    fi
  elif has_command nvidia-smi; then
    if docker image inspect "$gpu_image" >/dev/null 2>&1; then
      ok "optional GPU sandbox image: $gpu_image"
    else
      warn "a GPU is visible but the GPU sandbox image is missing: $gpu_image"
    fi
  else
    warn "GPU runtime was not requested; GPU experiments will be unavailable"
  fi
}

check_compute_paths() {
  local path_value
  for path_value in "${DATA_DIR:-}" "${MPLCONFIGDIR:-}"; do
    [[ -n "$path_value" ]] || continue
    if [[ -d "$path_value" && -w "$path_value" ]]; then
      ok "runtime path is writable: $path_value"
    else
      fail "runtime path is missing or not writable: $path_value"
    fi
  done
}

check_gateway_runtime() {
  require_command caddy "HTTPS, authentication, API proxying, and static files"
  require_command sshd "receiving the private-node reverse tunnel"
  require_command curl "gateway health checks"
  optional_command rsync "atomic frontend release upload"

  local caddyfile="${FAROS_CADDYFILE:-/etc/caddy/Caddyfile}"
  if [[ -f "$caddyfile" ]]; then
    if has_command caddy && caddy validate --config "$caddyfile" >/dev/null 2>&1; then
      ok "Caddy configuration is valid: $caddyfile"
    elif has_command caddy; then
      fail "Caddy configuration is invalid: $caddyfile"
    fi
  else
    fail "Caddy configuration is missing: $caddyfile"
  fi

  local frontend_root="${FAROS_FRONTEND_ROOT:-/opt/faros/frontend-current}"
  if [[ -r "$frontend_root/index.html" ]]; then
    ok "frontend release is readable: $frontend_root/index.html"
  else
    fail "frontend release is missing: $frontend_root/index.html"
  fi
}

printf 'FAROS dependency preflight (role=%s)\n' "$ROLE"

if [[ "$ROLE" == "local" || "$ROLE" == "compute" || "$ROLE" == "all" ]]; then
  check_python_runtime
fi

if [[ "$ROLE" == "local" || "$ROLE" == "all" ]]; then
  require_command git "source checkout and provenance"
  check_node_runtime
  if has_command docker; then
    if docker info >/dev/null 2>&1; then
      ok "optional local Docker daemon is available"
    else
      warn "Docker is installed but the local daemon is unavailable"
    fi
  else
    warn "Docker is absent; local Code/Experiment runs use the less isolated subprocess backend"
  fi
  check_tex_runtime optional
fi

if [[ "$ROLE" == "compute" || "$ROLE" == "all" ]]; then
  require_command git "source checkout and run provenance"
  require_command ssh "the reverse tunnel to the public gateway"
  optional_command rsync "release synchronization"
  check_docker_runtime
  check_tex_runtime required
  check_compute_paths
fi

if [[ "$ROLE" == "gateway" || "$ROLE" == "all" ]]; then
  check_gateway_runtime
fi

printf '\nSummary: %d required failure(s), %d warning(s).\n' "$FAILURES" "$WARNINGS"
if (( FAILURES > 0 )); then
  exit 1
fi
