#!/usr/bin/env bash
# ============================================================
# AI Hub - Self-Contained Portable Launcher
# 
# Requirements: bash, curl, NVIDIA driver (nvidia-smi)
# Everything else is self-provisioned into hub/tools/
#
# Bootstrap chain:
#   1. Verify nvidia-smi (confirms GPU-capable system)
#   2. Download uv binary → hub/tools/ (if not present)
#   3. Download Python → hub/tools/ via uv (if not present)
#   4. Run hub.py with portable Python
# ============================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOLS_DIR="${SCRIPT_DIR}/tools"
HUB_SCRIPT="${SCRIPT_DIR}/hub.py"

# Platform detection
ARCH=$(uname -m)
OS=$(uname -s | tr '[:upper:]' '[:lower:]')

# Tool paths
UV_BIN="${TOOLS_DIR}/uv"
PYTHON_DIR="${TOOLS_DIR}/python"
PYTHON_BIN=""  # Set after provisioning

# Target Python version for the hub
HUB_PYTHON_VERSION="3.12"

# Colors
RED='\033[91m'
GREEN='\033[92m'
YELLOW='\033[93m'
CYAN='\033[96m'
DIM='\033[2m'
BOLD='\033[1m'
RESET='\033[0m'

# ============================================================
# Helper functions
# ============================================================
log_ok()   { echo -e "  ${GREEN}✓${RESET} $1"; }
log_warn() { echo -e "  ${YELLOW}⚠${RESET} $1"; }
log_err()  { echo -e "  ${RED}✗${RESET} $1"; }
log_info() { echo -e "  ${DIM}$1${RESET}"; }
log_step() { echo -e "  ${CYAN}${BOLD}[$1]${RESET} $2"; }

# ============================================================
# Banner
# ============================================================
echo ""
echo -e "${CYAN}${BOLD}╔══════════════════════════════════════════════╗${RESET}"
echo -e "${CYAN}${BOLD}║            🤖  AI Hub Launcher              ║${RESET}"
echo -e "${CYAN}${BOLD}╚══════════════════════════════════════════════╝${RESET}"
echo ""

# ============================================================
# Step 0: Check basic tools (bash is implicit, check curl)
# ============================================================
if ! command -v curl &> /dev/null; then
    if command -v wget &> /dev/null; then
        # We can work with wget as fallback
        DOWNLOADER="wget"
    else
        log_err "Se necesita curl o wget para descargar herramientas."
        log_info "Instalar: sudo apt install curl  (o)  sudo dnf install curl"
        echo ""
        read -p "  Presiona Enter para cerrar..."
        exit 1
    fi
else
    DOWNLOADER="curl"
fi

# ============================================================
# Step 1: Check NVIDIA driver
# ============================================================
log_step "1/4" "Verificando GPU NVIDIA..."

if command -v nvidia-smi &> /dev/null; then
    GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader,nounits 2>/dev/null | head -1)
    CUDA_VER=$(nvidia-smi 2>/dev/null | grep -oP "CUDA Version:\s*\K[\d.]+")
    if [ -n "$GPU_NAME" ]; then
        log_ok "GPU: ${GPU_NAME} (CUDA ${CUDA_VER})"
    else
        echo ""
        log_err "nvidia-smi ejecutó pero no detectó ninguna GPU."
        echo ""
        log_info "Las aplicaciones de AI generativa requieren una GPU NVIDIA"
        log_info "con soporte CUDA para funcionar correctamente."
        log_info ""
        log_info "Posibles causas:"
        log_info "  • Los drivers NVIDIA no están instalados"
        log_info "  • La GPU no es NVIDIA (AMD/Intel no soportado)"
        log_info "  • El sistema no tiene GPU dedicada"
        log_info ""
        log_info "Solución: Instalar drivers NVIDIA para tu sistema operativo"
        log_info "  Ubuntu:  sudo apt install nvidia-driver-XXX"
        log_info "  Fedora:  sudo dnf install akmod-nvidia"
        log_info "  Bazzite: Los drivers vienen pre-instalados"
        echo ""
        read -p "  Presiona Enter para cerrar..."
        exit 1
    fi
else
    echo ""
    log_err "nvidia-smi no encontrado."
    echo ""
    log_info "AI Hub requiere una GPU NVIDIA con drivers instalados."
    log_info "Las aplicaciones de AI generativa (Stable Diffusion, etc.)"
    log_info "necesitan aceleración GPU — en CPU tardarían horas por imagen."
    log_info ""
    log_info "Requisitos mínimos:"
    log_info "  • GPU NVIDIA (GTX 1060+ / RTX recomendado)"
    log_info "  • Drivers NVIDIA instalados"
    log_info "  • Mínimo 6GB VRAM (8GB+ recomendado)"
    log_info ""
    log_info "Instalar drivers NVIDIA:"
    log_info "  Ubuntu:  sudo apt install nvidia-driver-XXX"
    log_info "  Fedora:  sudo dnf install akmod-nvidia"
    log_info "  Bazzite: Los drivers vienen pre-instalados"
    log_info "  Windows: https://www.nvidia.com/drivers"
    echo ""
    read -p "  Presiona Enter para cerrar..."
    exit 1
fi

# ============================================================
# Step 2: Provision uv
# ============================================================
log_step "2/4" "Verificando uv (package manager)..."

mkdir -p "${TOOLS_DIR}"

if [ -x "${UV_BIN}" ]; then
    UV_VER=$("${UV_BIN}" --version 2>/dev/null)
    log_ok "uv ya disponible: ${UV_VER}"
else
    log_info "Descargando uv..."

    # Determine platform for uv download
    case "${ARCH}" in
        x86_64)  UV_ARCH="x86_64" ;;
        aarch64) UV_ARCH="aarch64" ;;
        arm64)   UV_ARCH="aarch64" ;;
        *)
            log_err "Arquitectura no soportada: ${ARCH}"
            exit 1
            ;;
    esac

    UV_URL="https://github.com/astral-sh/uv/releases/latest/download/uv-${UV_ARCH}-unknown-linux-gnu.tar.gz"

    if [ "$DOWNLOADER" = "curl" ]; then
        curl -LsSf "${UV_URL}" | tar xzf - -C "${TOOLS_DIR}" --strip-components=1 2>/dev/null
    else
        wget -qO- "${UV_URL}" | tar xzf - -C "${TOOLS_DIR}" --strip-components=1 2>/dev/null
    fi

    if [ -x "${UV_BIN}" ]; then
        UV_VER=$("${UV_BIN}" --version 2>/dev/null)
        log_ok "uv descargado: ${UV_VER}"
    else
        log_err "Error descargando uv"
        log_info "URL: ${UV_URL}"
        echo ""
        read -p "  Presiona Enter para cerrar..."
        exit 1
    fi
fi

# ============================================================
# Step 3: Provision Python
# ============================================================
log_step "3/4" "Verificando Python ${HUB_PYTHON_VERSION}..."

# Find the Python binary that uv installed
find_uv_python() {
    # uv stores Python in its managed directory
    local managed_dir="${TOOLS_DIR}/python"
    
    # Check if we have a Python in our tools directory
    if [ -d "${managed_dir}" ]; then
        local py_bin=$(find "${managed_dir}" -name "python3" -type f 2>/dev/null | head -1)
        if [ -x "$py_bin" ]; then
            echo "$py_bin"
            return 0
        fi
    fi
    return 1
}

# First check if system Python works (bonus optimization)
SYS_PYTHON=""
for candidate in python3 python; do
    if command -v "$candidate" &> /dev/null; then
        py_major=$("$candidate" -c "import sys; print(sys.version_info.major)" 2>/dev/null)
        py_minor=$("$candidate" -c "import sys; print(sys.version_info.minor)" 2>/dev/null)
        if [ "$py_major" = "3" ] && [ "$py_minor" -ge 8 ]; then
            SYS_PYTHON="$candidate"
            break
        fi
    fi
done

if [ -n "$SYS_PYTHON" ]; then
    PYTHON_BIN="$SYS_PYTHON"
    py_ver=$("$PYTHON_BIN" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')")
    log_ok "Python del sistema: ${py_ver}"
else
    # Try to find uv-managed Python
    PYTHON_BIN=$(find_uv_python)
    
    if [ -n "$PYTHON_BIN" ] && [ -x "$PYTHON_BIN" ]; then
        py_ver=$("$PYTHON_BIN" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')")
        log_ok "Python portable: ${py_ver}"
    else
        log_info "Descargando Python ${HUB_PYTHON_VERSION}..."
        
        # Use uv to install Python into our tools directory
        export UV_PYTHON_INSTALL_DIR="${PYTHON_DIR}"
        "${UV_BIN}" python install "${HUB_PYTHON_VERSION}" 2>&1 | while read -r line; do
            echo -e "    ${DIM}${line}${RESET}"
        done

        PYTHON_BIN=$(find_uv_python)
        
        if [ -n "$PYTHON_BIN" ] && [ -x "$PYTHON_BIN" ]; then
            py_ver=$("$PYTHON_BIN" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')")
            log_ok "Python descargado: ${py_ver}"
        else
            log_err "Error instalando Python"
            echo ""
            read -p "  Presiona Enter para cerrar..."
            exit 1
        fi
    fi
fi

# ============================================================
# Step 4: Check git (optional - warn only)
# ============================================================
if command -v git &> /dev/null; then
    GIT_VER=$(git --version 2>/dev/null)
    log_ok "git: ${GIT_VER}"
else
    log_warn "git no encontrado (necesario para instalar/actualizar apps)"
    log_info "Instalar: sudo apt install git  (o)  sudo dnf install git"
fi

# ============================================================
# Step 4b: Check/Provision Node.js (needed for some app UIs)
# ============================================================
NODE_DIR="${TOOLS_DIR}/node"
NODE_BIN=""

# Check system node first
if command -v node &> /dev/null && command -v npm &> /dev/null; then
    NODE_VER=$(node --version 2>/dev/null)
    log_ok "Node.js del sistema: ${NODE_VER}"
    NODE_BIN="$(dirname $(command -v node))"
elif [ -d "${NODE_DIR}" ]; then
    # Check portable node
    portable_node=$(find "${NODE_DIR}" -name "node" -type f 2>/dev/null | head -1)
    if [ -x "$portable_node" ]; then
        NODE_BIN="$(dirname "$portable_node")"
        NODE_VER=$("$portable_node" --version 2>/dev/null)
        log_ok "Node.js portable: ${NODE_VER}"
    fi
fi

if [ -z "$NODE_BIN" ]; then
    log_info "Descargando Node.js (necesario para algunas UIs)..."  
    mkdir -p "${NODE_DIR}"

    # Download Node.js LTS
    NODE_VERSION="22.17.0"
    case "${ARCH}" in
        x86_64)  NODE_ARCH="x64" ;;
        aarch64) NODE_ARCH="arm64" ;;
        arm64)   NODE_ARCH="arm64" ;;
        *)
            log_warn "Node.js: arquitectura no soportada (${ARCH}), UIs con npm no funcionarán"
            NODE_VERSION=""
            ;;
    esac

    if [ -n "$NODE_VERSION" ]; then
        NODE_URL="https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-${NODE_ARCH}.tar.xz"
        
        if [ "$DOWNLOADER" = "curl" ]; then
            curl -LsSf "${NODE_URL}" | tar xJf - -C "${NODE_DIR}" --strip-components=1 2>/dev/null
        else
            wget -qO- "${NODE_URL}" | tar xJf - -C "${NODE_DIR}" --strip-components=1 2>/dev/null
        fi

        portable_node="${NODE_DIR}/bin/node"
        if [ -x "$portable_node" ]; then
            NODE_BIN="${NODE_DIR}/bin"
            NODE_VER=$("$portable_node" --version 2>/dev/null)
            log_ok "Node.js descargado: ${NODE_VER}"
        else
            log_warn "Error descargando Node.js (UIs con npm no funcionarán)"
        fi
    fi
fi

# ============================================================
# Step 5: Launch (CLI or GUI)
# ============================================================
echo ""

# Export tools path so hub.py knows where to find uv
export AI_HUB_UV_PATH="${UV_BIN}"
export AI_HUB_TOOLS_DIR="${TOOLS_DIR}"

# Export Node.js path if available
if [ -n "$NODE_BIN" ]; then
    export AI_HUB_NODE_DIR="${NODE_BIN}"
    export PATH="${NODE_BIN}:${PATH}"
fi

# Keep uv cache on same drive for hard-link dedup (saves ~8GB across apps)
export UV_CACHE_DIR="${SCRIPT_DIR}/.cache/uv"
export UV_LINK_MODE="hardlink"

# Check for --gui flag
GUI_MODE=false
REMAINING_ARGS=()
for arg in "$@"; do
    if [ "$arg" = "--gui" ]; then
        GUI_MODE=true
    else
        REMAINING_ARGS+=("$arg")
    fi
done

if [ "$GUI_MODE" = true ]; then
    log_step "5/5" "Iniciando AI Hub (GUI)..."
    echo ""

    # Provision GUI venv if needed
    UI_VENV="${SCRIPT_DIR}/.ui_venv"
    UI_PYTHON="${UI_VENV}/bin/python"
    UI_SCRIPT="${SCRIPT_DIR}/gui/main.py"

    if [ ! -f "$UI_PYTHON" ]; then
        log_info "Instalando interfaz gráfica (primera vez)..."
        "$UV_BIN" venv --python 3.13 "$UI_VENV" 2>/dev/null
        if [ $? -ne 0 ]; then
            log_error "Error creando venv para GUI"
            log_info "Cayendo a modo terminal..."
            exec "$PYTHON_BIN" "$HUB_SCRIPT" "${REMAINING_ARGS[@]}"
        fi
    fi

    # Check if all UI dependencies are installed
    "$UI_PYTHON" -c "import PySide6, requests, numpy, safetensors" 2>/dev/null
    if [ $? -ne 0 ]; then
        log_info "Instalando dependencias de la UI (PySide6, requests, numpy, safetensors)..."
        "$UV_BIN" pip install --python "$UI_PYTHON" PySide6 requests numpy safetensors 2>/dev/null
        if [ $? -ne 0 ]; then
            log_error "Error instalando dependencias de la UI"
            log_info "Cayendo a modo terminal..."
            exec "$PYTHON_BIN" "$HUB_SCRIPT" "${REMAINING_ARGS[@]}"
        fi
        log_ok "Dependencias de la UI instaladas"
    fi

    exec "$UI_PYTHON" "$UI_SCRIPT" "${REMAINING_ARGS[@]}"
else
    log_step "4/4" "Iniciando AI Hub..."
    echo ""
    exec "$PYTHON_BIN" "$HUB_SCRIPT" "$@"
fi
