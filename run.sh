#!/usr/bin/env bash
# ============================================================
# AI Hub — Launcher principal
#
# Requisitos: bash, curl, driver NVIDIA
# Todo lo demás se provisiona automáticamente en hub/tools/
#
# Bootstrap:
#   1. Verificar GPU NVIDIA (nvidia-smi)
#   2. Descargar uv (package manager)
#   3. Descargar Python 3.13 portable
#   4. Verificar git y Node.js
#   5. Preparar entorno Python base (.ui_venv)
#   6. Preparar entorno WebUI y arrancar
# ============================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

HUB_DIR="${SCRIPT_DIR}/hub"
WEBUI_DIR="${SCRIPT_DIR}/hub-webui"
TOOLS_DIR="${HUB_DIR}/tools"
LOGS_DIR="${SCRIPT_DIR}/logs"

UV_BIN="${TOOLS_DIR}/uv"
PYTHON_DIR="${TOOLS_DIR}/python"
PYTHON_BIN=""

HUB_PYTHON_VERSION="3.13"

ARCH=$(uname -m)
OS=$(uname -s | tr '[:upper:]' '[:lower:]')

# ── Colores ───────────────────────────────────────────────────
RED='\033[91m'
GREEN='\033[92m'
YELLOW='\033[93m'
CYAN='\033[96m'
DIM='\033[2m'
BOLD='\033[1m'
RESET='\033[0m'

log_ok()   { echo -e "  ${GREEN}✓${RESET} $1"; }
log_warn() { echo -e "  ${YELLOW}⚠${RESET} $1"; }
log_err()  { echo -e "  ${RED}✗${RESET} $1"; }
log_info() { echo -e "  ${DIM}$1${RESET}"; }
log_step() { echo -e "  ${CYAN}${BOLD}[$1]${RESET} $2"; }

# ── Banner ────────────────────────────────────────────────────
echo ""
echo -e "${CYAN}${BOLD}╔══════════════════════════════════════════════╗${RESET}"
echo -e "${CYAN}${BOLD}║            🤖  AI Hub Launcher              ║${RESET}"
echo -e "${CYAN}${BOLD}╚══════════════════════════════════════════════╝${RESET}"
echo ""

# ============================================================
# Paso 0: curl o wget
# ============================================================
if command -v curl &> /dev/null; then
    DOWNLOADER="curl"
elif command -v wget &> /dev/null; then
    DOWNLOADER="wget"
else
    log_err "Se necesita curl o wget."
    log_info "Instalar: sudo apt install curl  (o)  sudo dnf install curl"
    echo ""; read -p "  Presiona Enter para cerrar..."; exit 1
fi

# ============================================================
# Paso 1: GPU NVIDIA
# ============================================================
log_step "1/6" "Verificando GPU NVIDIA..."

if command -v nvidia-smi &> /dev/null; then
    GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader,nounits 2>/dev/null | head -1)
    CUDA_VER=$(nvidia-smi 2>/dev/null | grep -oP "CUDA Version:\s*\K[\d.]+")
    if [ -n "$GPU_NAME" ]; then
        log_ok "GPU: ${GPU_NAME} (CUDA ${CUDA_VER})"
    else
        log_err "nvidia-smi ejecutó pero no detectó ninguna GPU."
        log_info "Posibles causas: drivers no instalados, GPU no NVIDIA, sin GPU dedicada."
        echo ""; read -p "  Presiona Enter para cerrar..."; exit 1
    fi
else
    log_err "nvidia-smi no encontrado. AI Hub requiere una GPU NVIDIA."
    log_info "  Ubuntu: sudo apt install nvidia-driver-XXX"
    log_info "  Fedora: sudo dnf install akmod-nvidia"
    echo ""; read -p "  Presiona Enter para cerrar..."; exit 1
fi

# ============================================================
# Paso 2: uv (package manager)
# ============================================================
log_step "2/6" "Verificando uv..."

mkdir -p "${TOOLS_DIR}"

if [ -x "${UV_BIN}" ]; then
    log_ok "uv: $("${UV_BIN}" --version 2>/dev/null)"
else
    log_info "Descargando uv..."
    case "${ARCH}" in
        x86_64)  UV_ARCH="x86_64" ;;
        aarch64|arm64) UV_ARCH="aarch64" ;;
        *) log_err "Arquitectura no soportada: ${ARCH}"; exit 1 ;;
    esac

    UV_URL="https://github.com/astral-sh/uv/releases/latest/download/uv-${UV_ARCH}-unknown-linux-gnu.tar.gz"
    if [ "$DOWNLOADER" = "curl" ]; then
        curl -LsSf "${UV_URL}" | tar xzf - -C "${TOOLS_DIR}" --strip-components=1 2>/dev/null
    else
        wget -qO- "${UV_URL}" | tar xzf - -C "${TOOLS_DIR}" --strip-components=1 2>/dev/null
    fi

    if [ -x "${UV_BIN}" ]; then
        log_ok "uv descargado: $("${UV_BIN}" --version 2>/dev/null)"
    else
        log_err "Error descargando uv. URL: ${UV_URL}"
        echo ""; read -p "  Presiona Enter para cerrar..."; exit 1
    fi
fi

# ============================================================
# Paso 3: Python 3.13
# ============================================================
log_step "3/6" "Verificando Python ${HUB_PYTHON_VERSION}..."

find_uv_python() {
    local py_bin
    py_bin=$(find "${PYTHON_DIR}" -name "python3" -type f 2>/dev/null | head -1)
    [ -x "$py_bin" ] && echo "$py_bin"
}

# Intentar Python del sistema primero
for candidate in python3 python; do
    if command -v "$candidate" &> /dev/null; then
        minor=$("$candidate" -c "import sys; print(sys.version_info.minor)" 2>/dev/null)
        major=$("$candidate" -c "import sys; print(sys.version_info.major)" 2>/dev/null)
        if [ "$major" = "3" ] && [ "${minor:-0}" -ge 8 ]; then
            PYTHON_BIN="$candidate"
            break
        fi
    fi
done

if [ -n "$PYTHON_BIN" ]; then
    log_ok "Python del sistema: $("$PYTHON_BIN" --version 2>/dev/null)"
else
    PYTHON_BIN=$(find_uv_python)
    if [ -n "$PYTHON_BIN" ]; then
        log_ok "Python portable: $("$PYTHON_BIN" --version 2>/dev/null)"
    else
        log_info "Descargando Python ${HUB_PYTHON_VERSION}..."
        export UV_PYTHON_INSTALL_DIR="${PYTHON_DIR}"
        "${UV_BIN}" python install "${HUB_PYTHON_VERSION}" 2>&1 | while read -r line; do
            echo -e "    ${DIM}${line}${RESET}"
        done
        PYTHON_BIN=$(find_uv_python)
        if [ -n "$PYTHON_BIN" ]; then
            log_ok "Python descargado: $("$PYTHON_BIN" --version 2>/dev/null)"
        else
            log_err "Error instalando Python"; echo ""; read -rp "  Presiona Enter..."; exit 1
        fi
    fi
fi

# ============================================================
# Paso 4: git y Node.js
# ============================================================
log_step "4/6" "Verificando herramientas..."

if command -v git &> /dev/null; then
    log_ok "git: $(git --version 2>/dev/null)"
else
    log_warn "git no encontrado (necesario para instalar/actualizar apps)"
fi

NODE_DIR="${TOOLS_DIR}/node"
NODE_BIN=""

if command -v node &> /dev/null && command -v npm &> /dev/null; then
    log_ok "Node.js del sistema: $(node --version 2>/dev/null)"
    NODE_BIN="$(dirname "$(command -v node)")"
elif [ -d "${NODE_DIR}" ]; then
    portable_node=$(find "${NODE_DIR}" -name "node" -type f 2>/dev/null | head -1)
    if [ -x "$portable_node" ]; then
        NODE_BIN="$(dirname "$portable_node")"
        log_ok "Node.js portable: $("$portable_node" --version 2>/dev/null)"
    fi
fi

if [ -z "$NODE_BIN" ]; then
    log_info "Descargando Node.js..."
    mkdir -p "${NODE_DIR}"
    NODE_VERSION="22.17.0"
    case "${ARCH}" in
        x86_64)        NODE_ARCH="x64" ;;
        aarch64|arm64) NODE_ARCH="arm64" ;;
        *) log_warn "Node.js: arquitectura no soportada (${ARCH})"; NODE_VERSION="" ;;
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
            log_ok "Node.js descargado: $("$portable_node" --version 2>/dev/null)"
        else
            log_warn "Error descargando Node.js (UIs con npm no funcionarán)"
        fi
    fi
fi

# ============================================================
# Paso 5: Entorno base Python 3.13 (.ui_venv)
# ============================================================
log_step "5/6" "Verificando entorno base..."

UI_VENV="${HUB_DIR}/.ui_venv"
UI_PYTHON="${UI_VENV}/bin/python"

if [ ! -f "$UI_PYTHON" ]; then
    log_info "Creando entorno base (Python 3.13)..."
    "${UV_BIN}" venv --python 3.13 "$UI_VENV" 2>/dev/null \
        || "${UV_BIN}" venv --python "${PYTHON_BIN}" "$UI_VENV" 2>/dev/null
fi

# PySide6 en .ui_venv: lo usa pywebview para mostrar la WebUI en ventana nativa.
# No es crítico — si falta, el webui abre en el browser del sistema.
"$UI_PYTHON" -c "import PySide6" 2>/dev/null || {
    log_info "Instalando PySide6 (backend ventana nativa)..."
    "${UV_BIN}" pip install --python "$UI_PYTHON" PySide6 --quiet 2>/dev/null \
        && log_ok "PySide6 instalado" \
        || log_info "PySide6 no disponible — se usará el browser del sistema"
}

if [ ! -f "$UI_PYTHON" ]; then
    log_err "No se pudo preparar el entorno base."
    read -rp "  Presiona Enter para cerrar..."; exit 1
fi

# ============================================================
# Paso 6: WebUI — venv + dependencias
# ============================================================
log_step "6/6" "Verificando WebUI..."

WEBUI_VENV="${WEBUI_DIR}/.venv"
WEBUI_PY="${WEBUI_VENV}/bin/python3"
WEBUI_APP="${WEBUI_DIR}/app.py"

if [ ! -f "$WEBUI_PY" ]; then
    log_info "Creando entorno de la WebUI..."
    "$UI_PYTHON" -m venv "$WEBUI_VENV" 2>/dev/null \
        || "${UV_BIN}" venv --python "$UI_PYTHON" "$WEBUI_VENV" 2>/dev/null
fi

"$WEBUI_PY" -c "import fastapi, uvicorn, requests, numpy" 2>/dev/null
if [ $? -ne 0 ]; then
    log_info "Instalando dependencias de la WebUI..."
    "${UV_BIN}" pip install --python "$WEBUI_PY" \
        -r "${WEBUI_DIR}/requirements.txt" --quiet 2>&1 \
        | grep -v "^$\|already sat\|notice\|new release" || true
    log_ok "Dependencias instaladas"
else
    log_ok "WebUI listo"
fi

# pywebview (opcional — para ventana nativa)
"$WEBUI_PY" -c "import webview" 2>/dev/null || \
    "${UV_BIN}" pip install --python "$WEBUI_PY" "pywebview>=5.0" --quiet 2>/dev/null || true

# ── Variables de entorno ──────────────────────────────────────
export AI_HUB_UV_PATH="${UV_BIN}"
export AI_HUB_TOOLS_DIR="${TOOLS_DIR}"
export UV_CACHE_DIR="${SCRIPT_DIR}/.cache/uv"
export UV_LINK_MODE="hardlink"
export PYTHONUNBUFFERED="1"

if [ -n "$NODE_BIN" ]; then
    export AI_HUB_NODE_DIR="${NODE_BIN}"
    export PATH="${NODE_BIN}:${PATH}"
fi

# PySide6 de .ui_venv disponible para pywebview como backend Qt
UI_SITE="${UI_VENV}/lib/python3.13/site-packages"
if [ -d "$UI_SITE" ]; then
    export PYTHONPATH="${UI_SITE}${PYTHONPATH:+:$PYTHONPATH}"
fi

# ── Log de sesión ─────────────────────────────────────────────
mkdir -p "${LOGS_DIR}"
SESSION_LOG="${LOGS_DIR}/session_$(date +%Y-%m-%d_%H-%M-%S).log"
ls -t "${LOGS_DIR}"/session_*.log 2>/dev/null | tail -n +11 | xargs -r rm --

# ── Arrancar WebUI ────────────────────────────────────────────
echo ""
echo -e "  ${CYAN}${BOLD}Iniciando AI Hub...${RESET}"
echo -e "  ${DIM}Log: ${SESSION_LOG}${RESET}"
echo ""
echo -e "  ${DIM}══════════════════════════════════════════════════════${RESET}"
echo ""

"$WEBUI_PY" "$WEBUI_APP" "$@" 2>&1 | tee "${SESSION_LOG}"
EXIT_CODE="${PIPESTATUS[0]}"

echo ""
echo -e "  ${DIM}══════════════════════════════════════════════════════${RESET}"
echo ""

if [ "$EXIT_CODE" -ne 0 ]; then
    echo -e "  ${RED}${BOLD}AI Hub terminó con error (código: ${EXIT_CODE})${RESET}"
    echo -e "  ${YELLOW}Log guardado en:${RESET} ${DIM}${SESSION_LOG}${RESET}"
else
    echo -e "  ${GREEN}AI Hub cerrado correctamente.${RESET}"
fi

echo ""
read -rp "  Presiona Enter para cerrar la terminal..."
