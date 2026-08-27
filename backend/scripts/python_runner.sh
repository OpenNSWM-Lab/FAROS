#!/usr/bin/env bash

# Run backend Python commands through an explicit interpreter when available.
run_py() {
  if [ -n "${FAROS_PYTHON:-}" ]; then
    if [ ! -x "$FAROS_PYTHON" ]; then
      printf 'FAROS_PYTHON is not executable: %s\n' "$FAROS_PYTHON" >&2
      return 127
    fi
    if [ "$1" = "python" ]; then
      shift
    fi
    "$FAROS_PYTHON" "$@"
  elif command -v conda >/dev/null 2>&1 && { [ -d "$HOME/anaconda3/envs/aist" ] || [ -d "$HOME/miniconda3/envs/aist" ]; }; then
    conda run --no-capture-output -n aist "$@"
  elif [ -x "$BACKEND_DIR/.venv/bin/python" ] && [ "$1" = "python" ]; then
    shift
    "$BACKEND_DIR/.venv/bin/python" "$@"
  elif command -v python >/dev/null 2>&1; then
    "$@"
  elif command -v python3 >/dev/null 2>&1 && [ "$1" = "python" ]; then
    shift
    python3 "$@"
  else
    printf 'Python interpreter not found. Create backend/.venv, set FAROS_PYTHON, or set up the aist conda environment.\n' >&2
    return 127
  fi
}
