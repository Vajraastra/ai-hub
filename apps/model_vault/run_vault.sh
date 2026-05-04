#!/usr/bin/env bash
# ============================================================
# Model Vault - Standalone Launcher
# ============================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$(dirname "$SCRIPT_DIR")")"
HUB_DIR="${ROOT_DIR}/hub"
TOOLS_DIR="${HUB_DIR}/tools"
UV_BIN="${TOOLS_DIR}/uv"
UI_VENV="${HUB_DIR}/.ui_venv"
UI_PYTHON="${UI_VENV}/bin/python"

# Bootstrap: use the same venv as the Hub for efficiency
if [ ! -f "$UI_PYTHON" ]; then
    echo "Error: Hub GUI environment not found. Please run hub/run.sh first."
    exit 1
fi

# Launch Model Vault
echo "--- Starting Model Vault (Vault) ---"
export PYTHONPATH="${SCRIPT_DIR}:${PYTHONPATH}"
exec "$UI_PYTHON" "${SCRIPT_DIR}/main.py" "$@"
