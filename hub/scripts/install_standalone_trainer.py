#!/usr/bin/env python3
"""
Post-install para Anima Standalone Trainer — cross-platform (sin bash).
Port Windows-first del antiguo install_standalone_trainer.sh.
Lo ejecuta app_installer con el python del venv de la app (sys.executable = venv).

El repo es cross-platform (trae setup_env.bat). Aquí replicamos su instalación
integrada al hub: torch del guardian (cu13x), requirements filtrado, paquetes
locales (Python puro), config con rutas del hub y junction de outputs.

Env inyectadas por app_installer.py: APP_DIR, VENV_PYTHON, HUB_DIR, PLATFORM,
CUDA_TAG, TORCH_VERSION.
"""
import os
import re
import sys
import json
import subprocess

APP_DIR  = os.environ["APP_DIR"]
HUB_DIR  = os.environ["HUB_DIR"]
CUDA_TAG = os.environ.get("CUDA_TAG", "cu130")
TORCH_VERSION = os.environ.get("TORCH_VERSION", "2.10.0")
PY = sys.executable  # = venv de la app (app_installer lo ejecuta con get_app_python)

def pip(*args):
    subprocess.run([PY, "-m", "pip", "install", "--no-cache-dir", *args],
                   check=True, cwd=APP_DIR)

print("=== Anima Standalone Trainer — Post-Install ===")
print(f"  APP_DIR : {APP_DIR}")
print(f"  PLATFORM: {os.environ.get('PLATFORM', sys.platform)}")
print(f"  torch   : {TORCH_VERSION}+{CUDA_TAG}")

# ── torch (lo fuerza el guardian; el requirements pinea 2.7.0+cu128 → lo ignoramos) ──
print(f"\n[torch] Instalando torch {TORCH_VERSION} ({CUDA_TAG})...")
pip(f"torch=={TORCH_VERSION}", "torchvision", "torchaudio",
    "--index-url", f"https://download.pytorch.org/whl/{CUDA_TAG}")
print("[torch] OK")

# ── requirements.txt filtrado ───────────────────────────────────────────────
# Quitamos: comentarios/vacíos, --extra-index-url/--index-url (índice cu128 del
# repo), torch/torchvision/torchaudio (ya instalados), paquetes locales y -e .
reqs = os.path.join(APP_DIR, "requirements.txt")
SKIP = re.compile(
    r"^\s*($|#|--?extra-index-url|--?index-url|torch(vision|audio)?\b|"
    r"\./cuda_direct_pkg|\./wd_parallel_pkg|-e\s)", re.IGNORECASE)
filtered = []
if os.path.isfile(reqs):
    with open(reqs, encoding="utf-8") as f:
        for line in f:
            if not SKIP.match(line.strip()):
                filtered.append(line.strip())
if filtered:
    print(f"\n[pip] Instalando requirements.txt filtrado ({len(filtered)} paquetes)...")
    pip(*filtered)
    print("[pip] OK")

# ── Paquetes locales (Python puro: cuda_direct_pkg, wd_parallel_pkg, raíz) ────
print("\n[local] Instalando paquetes locales...")
for sub in ("cuda_direct_pkg", "wd_parallel_pkg"):
    if os.path.isdir(os.path.join(APP_DIR, sub)):
        pip(f"./{sub}")
        print(f"[local] OK {sub}")
if os.path.isfile(os.path.join(APP_DIR, "pyproject.toml")) or \
   os.path.isfile(os.path.join(APP_DIR, "setup.py")):
    pip("-e", ".")
    print("[local] OK paquete raíz (-e .)")

# ── bitsandbytes (AdamW8bit) ─────────────────────────────────────────────────
print("\n[bnb] Instalando bitsandbytes...")
pip("bitsandbytes")
print("[bnb] OK")

# ── global_config.toml con rutas del hub ─────────────────────────────────────
def hub_path(key, default=""):
    cfg = os.path.join(HUB_DIR, "hub_config.json")
    try:
        with open(cfg, encoding="utf-8") as f:
            return json.load(f).get("paths", {}).get(key, default).rstrip("/\\")
    except Exception:
        return default

models_base = hub_path("models") or "E:/Models"
venv_dir = os.path.join(APP_DIR, "venv")
dit = f"{models_base}/diffusion_models/anima/anima-base-v1.0.safetensors"
te  = f"{models_base}/clip/anima/qwen_3_06b_base.safetensors"
vae = f"{models_base}/vae/anima/qwen_image_vae.safetensors"

print("\n[config] Escribiendo training-ui/global_config.toml...")
cfg_toml = f'''venv_path = "{venv_dir}"

[model_paths]
dit_path = "{dit}"
qwen3_path = "{te}"
vae_path = "{vae}"
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
'''
os.makedirs(os.path.join(APP_DIR, "training-ui"), exist_ok=True)
with open(os.path.join(APP_DIR, "training-ui", "global_config.toml"), "w", encoding="utf-8") as f:
    f.write(cfg_toml)
print("[config] OK")

# ── junction outputs: training-ui/jobs → <outputs>/anima-standalone ──────────
# En Windows usamos junction (no symlink). Reutilizamos el helper del hub.
sys.path.insert(0, HUB_DIR)
try:
    from modules.storage_manager import _create_dir_link, _is_dir_link, _remove_dir_link
    outputs_base = hub_path("outputs") or os.path.join(os.path.dirname(HUB_DIR), "outputs")
    anima_out = os.path.join(outputs_base, "anima-standalone")
    jobs = os.path.join(APP_DIR, "training-ui", "jobs")
    os.makedirs(anima_out, exist_ok=True)
    print("\n[outputs] Configurando junction jobs -> outputs...")
    # OJO: el clon puede traer un `jobs` que es un symlink POSIX, o —en Windows
    # sin privilegios de symlink— un ARCHIVO de texto que Git deja con el target
    # dentro (islink/isdir/_is_dir_link dan False). Usar lexists y limpiar todo.
    if os.path.lexists(jobs):
        if os.path.isdir(jobs) and not _is_dir_link(jobs):
            for item in os.listdir(jobs):
                src, dst = os.path.join(jobs, item), os.path.join(anima_out, item)
                if not os.path.exists(dst):
                    os.rename(src, dst)
            os.rmdir(jobs)
        else:
            try:
                _remove_dir_link(jobs)
            except OSError:
                os.unlink(jobs)            # archivo-symlink de Git
    _create_dir_link(anima_out, jobs)
    if not _is_dir_link(jobs):
        raise RuntimeError("el junction no quedó como reparse point")
    print(f"[outputs] OK  {jobs} -> {anima_out}")
except Exception as e:
    print(f"[outputs] ! No se pudo crear el junction: {e} (no bloqueante)")

# ── Node.js / npm (UI web) ───────────────────────────────────────────────────
import shutil
node = os.environ.get("AI_HUB_NODE_DIR", "")
npm = None
if node and os.path.isfile(os.path.join(node, "npm.cmd")):
    npm = os.path.join(node, "npm.cmd")
elif shutil.which("npm"):
    npm = shutil.which("npm")

if npm:
    print("\n[node] npm install (training-ui)...")
    subprocess.run([npm, "install", "--no-audit", "--no-fund"],
                   cwd=os.path.join(APP_DIR, "training-ui"), check=True)
    print("[node] OK")
else:
    print("\n[node] ! Node.js no encontrado — UI web pendiente. "
          "Instala Node.js y corre 'npm install' en training-ui/. "
          "La parte de entrenamiento (Python) ya está lista.")

print("\n=== install_standalone_trainer.py completado ===")
