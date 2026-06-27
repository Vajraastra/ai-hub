#!/usr/bin/env bash
# install_standalone_trainer.sh — Post-install para Anima Standalone Trainer
#
# Variables inyectadas por app_installer.py:
#   APP_DIR, VENV_DIR, VENV_PYTHON, HUB_DIR, PLATFORM
#   CUDA_TAG, TORCH_VERSION, GPU_ARCH, GPU_VRAM_MB

set -euo pipefail

echo "=== Anima Standalone Trainer — Post-Install ==="
echo "  APP_DIR      : $APP_DIR"
echo "  VENV_DIR     : $VENV_DIR"
echo "  TORCH_VERSION: $TORCH_VERSION"
echo "  CUDA_TAG     : $CUDA_TAG"

export PYTHONPATH=""
export PYTHONNOUSERSITE=1
PIP="$VENV_PYTHON -m pip install --no-cache-dir"
TORCH_INDEX="https://download.pytorch.org/whl/$CUDA_TAG"

# ── PyTorch ───────────────────────────────────────────────────────────────────
# requirements.txt tiene torch==2.7.0 hardcodeado — lo ignoramos e instalamos
# la versión correcta para cu130 / RTX 5060 Ti.
echo ""
echo "[torch] Instalando torch $TORCH_VERSION+$CUDA_TAG..."
$PIP torch==$TORCH_VERSION torchvision torchaudio \
    --index-url "$TORCH_INDEX"
echo "[torch] ✓"

# ── requirements.txt ──────────────────────────────────────────────────────────
# Filtramos:
#   --extra-index-url / --index-url → evita que pip use el índice cu128 del repo
#   torch/torchvision/torchaudio   → ya instalados con la versión correcta (cu130)
#   ./cuda_direct_pkg, ./wd_parallel_pkg, -e . → instalados separadamente desde APP_DIR
REQS="$APP_DIR/requirements.txt"
if [ -f "$REQS" ]; then
    echo ""
    echo "[pip] Instalando requirements.txt (filtrado)..."
    FILTERED=$(grep -v -E \
        "^\s*(#|$|--extra-index-url|--index-url|torch[^-]|torchvision|torchaudio|\.\/cuda_direct_pkg|\.\/wd_parallel_pkg|-e \s*\.)" \
        "$REQS") || true
    if [ -n "$FILTERED" ]; then
        # cwd=$APP_DIR por si quedan paths relativos residuales
        cd "$APP_DIR"
        echo "$FILTERED" | $PIP -r /dev/stdin
    else
        echo "[pip] Sin paquetes adicionales tras el filtrado"
    fi
    echo "[pip] ✓"
fi

# ── Paquetes locales ──────────────────────────────────────────────────────────
# Deben instalarse con CWD=$APP_DIR para que los paths relativos resuelvan.
echo ""
echo "[local] Instalando paquetes locales desde $APP_DIR..."
cd "$APP_DIR"

if [ -d "$APP_DIR/cuda_direct_pkg" ]; then
    $PIP ./cuda_direct_pkg
    echo "[local] ✓ cuda_direct_pkg"
fi

if [ -d "$APP_DIR/wd_parallel_pkg" ]; then
    $PIP ./wd_parallel_pkg
    echo "[local] ✓ wd_parallel_pkg"
fi

# -e . instala el paquete raíz en modo editable (si existe pyproject.toml o setup.py)
if [ -f "$APP_DIR/pyproject.toml" ] || [ -f "$APP_DIR/setup.py" ]; then
    $PIP -e .
    echo "[local] ✓ paquete raíz (-e .)"
fi

# ── bitsandbytes ──────────────────────────────────────────────────────────────
# Necesario para AdamW8bit optimizer. Instalar versión compatible cu130.
echo ""
echo "[bnb] Instalando bitsandbytes..."
$PIP bitsandbytes
echo "[bnb] ✓"

# ── Node.js dependencies ──────────────────────────────────────────────────────
# Buscar node/npm: primero portable del hub ($AI_HUB_NODE_DIR), luego sistema.
echo ""
echo "[node] Instalando dependencias Node.js (training-ui)..."
NODE_BIN=""
if [ -n "${AI_HUB_NODE_DIR:-}" ] && [ -x "$AI_HUB_NODE_DIR/node" ]; then
    NODE_BIN="$AI_HUB_NODE_DIR"
    echo "[node] Usando Node.js portable: $AI_HUB_NODE_DIR"
elif command -v node &>/dev/null; then
    NODE_BIN="$(dirname "$(command -v node)")"
    echo "[node] Usando Node.js del sistema: $(node --version)"
else
    echo "[node] ERROR: Node.js no encontrado. Instálalo con: sudo dnf install nodejs"
    exit 1
fi
NPM="$NODE_BIN/npm"
cd "$APP_DIR/training-ui"
"$NPM" install --no-audit --no-fund
echo "[node] ✓"

# ── global_config.toml ────────────────────────────────────────────────────────
# Leer la ruta de modelos desde hub_config.json si está disponible.
MODELS_BASE=$("$VENV_PYTHON" -c "
import json, os
cfg = os.path.join('$HUB_DIR', 'hub_config.json')
if os.path.isfile(cfg):
    d = json.load(open(cfg))
    print(d.get('paths', {}).get('models', '').rstrip('/'))
" 2>/dev/null || echo "/run/media/system/Kilaya/Models")

DIT_PATH="$MODELS_BASE/diffusion_models/anima/anima-base-v1.0.safetensors"
TE_PATH="$MODELS_BASE/clip/anima/qwen_3_06b_base.safetensors"
VAE_PATH="$MODELS_BASE/vae/anima/qwen_image_vae.safetensors"

echo ""
echo "[config] Configurando global_config.toml..."
echo "  DiT : $DIT_PATH"
echo "  TE  : $TE_PATH"
echo "  VAE : $VAE_PATH"
echo "  VENV: $VENV_DIR"

GLOBAL_CONFIG="$APP_DIR/training-ui/global_config.toml"

cat > "$GLOBAL_CONFIG" << TOML
venv_path = "$VENV_DIR"

[model_paths]
dit_path = "$DIT_PATH"
qwen3_path = "$TE_PATH"
vae_path = "$VAE_PATH"
lumina_dit_path = ""
gemma2_path = ""
lumina_vae_path = ""

[ui]
background = ""
background_position = "center"
dim_level = 0.5
brightness_level = 1.0
blur_level = 8
text_shadow_size = 2
theme = "dark"
TOML

echo "[config] ✓ global_config.toml configurado"

# ── Symlink outputs → hub outputs ─────────────────────────────────────────────
# training-ui/jobs/ se redirige al hub outputs para que safetensors e imágenes
# de muestra queden en un lugar centralizado y fácil de localizar.
echo ""
echo "[outputs] Configurando symlink de jobs → hub outputs..."

OUTPUTS_BASE=$("$VENV_PYTHON" -c "
import json, os
cfg = os.path.join('$HUB_DIR', 'hub_config.json')
if os.path.isfile(cfg):
    d = json.load(open(cfg))
    print(d.get('paths', {}).get('outputs', '').rstrip('/'))
" 2>/dev/null || echo "")

if [ -z "$OUTPUTS_BASE" ]; then
    # Fallback: outputs/ junto al hub
    OUTPUTS_BASE="$(dirname "$HUB_DIR")/outputs"
fi

ANIMA_OUTPUTS="$OUTPUTS_BASE/anima-standalone"
JOBS_DIR="$APP_DIR/training-ui/jobs"

mkdir -p "$ANIMA_OUTPUTS"

if [ -L "$JOBS_DIR" ]; then
    # Ya es un symlink — verificar si apunta al destino correcto
    CURRENT_TARGET="$(readlink "$JOBS_DIR")"
    if [ "$CURRENT_TARGET" = "$ANIMA_OUTPUTS" ]; then
        echo "[outputs] ✓ Symlink ya existe y es correcto"
    else
        rm "$JOBS_DIR"
        ln -s "$ANIMA_OUTPUTS" "$JOBS_DIR"
        echo "[outputs] ✓ Symlink reemplazado: $JOBS_DIR → $ANIMA_OUTPUTS"
    fi
elif [ -d "$JOBS_DIR" ]; then
    # Directorio real con posible contenido — migrar antes de reemplazar
    echo "[outputs] Migrando jobs existentes a $ANIMA_OUTPUTS..."
    shopt -s dotglob nullglob
    for item in "$JOBS_DIR"/*; do
        name="$(basename "$item")"
        if [ ! -e "$ANIMA_OUTPUTS/$name" ]; then
            mv "$item" "$ANIMA_OUTPUTS/"
            echo "[outputs]   movido: $name"
        else
            echo "[outputs]   omitido (ya existe): $name"
        fi
    done
    shopt -u dotglob nullglob
    rmdir "$JOBS_DIR" 2>/dev/null || rm -rf "$JOBS_DIR"
    ln -s "$ANIMA_OUTPUTS" "$JOBS_DIR"
    echo "[outputs] ✓ Symlink creado: $JOBS_DIR → $ANIMA_OUTPUTS"
else
    ln -s "$ANIMA_OUTPUTS" "$JOBS_DIR"
    echo "[outputs] ✓ Symlink creado: $JOBS_DIR → $ANIMA_OUTPUTS"
fi

echo "[outputs] Estructura: outputs/anima-standalone/<job>/output/{*.safetensors,sample/}"

# ── launch.sh ─────────────────────────────────────────────────────────────────
echo ""
echo "[launch] Creando launch.sh..."

cat > "$APP_DIR/launch.sh" << 'LAUNCH'
#!/usr/bin/env bash
# launch.sh — Anima Standalone Trainer
# Generado por install_standalone_trainer.sh
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec node "$SCRIPT_DIR/training-ui/server.js" --port=7880 "$@"
LAUNCH

chmod +x "$APP_DIR/launch.sh"
echo "[launch] ✓ launch.sh creado"

echo ""
echo "=== install_standalone_trainer.sh completado ==="
