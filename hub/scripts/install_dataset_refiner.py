#!/usr/bin/env python3
"""
Post-install para IMG Dataset Refiner — cross-platform (sin bash).
Lo ejecuta app_installer con el python del venv de la app (sys.executable = venv).

Variables inyectadas por app_installer.py (env):
    APP_DIR, VENV_DIR, VENV_PYTHON, HUB_DIR, PLATFORM, CUDA_TAG, ...
"""
import os
import re
import sys
import subprocess

APP_DIR = os.environ["APP_DIR"]
PLATFORM = os.environ.get("PLATFORM", sys.platform)

print("=== Dataset Refiner — Post-Install ===")
print(f"  APP_DIR : {APP_DIR}")
print(f"  PLATFORM: {PLATFORM}")

# ── opencv-python-headless ──────────────────────────────────────────────────
# Variante headless: bundlea sus libs y no requiere nada del sistema.
# opencv-python normal fue excluido por pip_exclude_packages en el registry.
print("\n[opencv] Instalando opencv-python-headless...")
subprocess.run(
    [sys.executable, "-m", "pip", "install", "--no-cache-dir", "opencv-python-headless"],
    check=True,
)
print("[opencv] OK opencv-python-headless instalado")

# ── Patch lora_manager.py ───────────────────────────────────────────────────
# El script upstream no acepta --port ni configura server_port en Gradio.
# Inyectamos: 1) _get_hub_port(), 2) server_port en launch_kwargs,
# 3) inbrowser=False (el hub abre el browser vía auto_open_browser).
print("\n[patch] Parcheando lora_manager.py para soporte de --port...")
target = os.path.join(APP_DIR, "lora_manager.py")

with open(target, "r", encoding="utf-8") as f:
    content = f.read()

if "_get_hub_port" in content:
    print("[patch] lora_manager.py ya está parchado — nada que hacer")
    sys.exit(0)

PORT_READER = '''
# Hub port support — inyectado por install_dataset_refiner.py
import sys as _sys, os as _os

def _get_hub_port(default=7875):
    """Lee --port de sys.argv o COMMANDLINE_ARGS (hub port_override)."""
    args = _sys.argv[1:] + _os.environ.get("COMMANDLINE_ARGS", "").split()
    for i, a in enumerate(args):
        if a == "--port" and i + 1 < len(args):
            try: return int(args[i + 1])
            except ValueError: pass
        if a.startswith("--port="):
            try: return int(a.split("=", 1)[1])
            except ValueError: pass
    return default

_hub_port = _get_hub_port()
# ── fin hub port support ──────────────────────────────────────────────────────
'''

# 1. Inyectar antes de "if __name__ == '__main__':" (nivel módulo, sin indent)
content, n = re.subn(
    r"(?m)^(if\s+__name__\s*==\s*['\"]__main__['\"])",
    PORT_READER.rstrip("\n") + "\n" + r"\1",
    content,
    count=1,
)
if n == 0:
    print("[patch] ERROR: no se encontró 'if __name__ == \"__main__\"' en lora_manager.py")
    sys.exit(1)

# 2. Añadir server_port tras "server_name" en launch_kwargs
content, n = re.subn(
    r'("server_name"\s*:\s*"[^"]*")',
    r'\1,\n        "server_port": _hub_port',
    content,
    count=1,
)
if n == 0:
    print("[patch] WARN: no se encontró 'server_name' en launch_kwargs — server_port no inyectado")

# 3. Desactivar apertura automática de browser (el hub lo maneja)
content, n = re.subn(r'"inbrowser"\s*:\s*True', '"inbrowser": False', content, count=1)
if n == 0:
    print("[patch] WARN: no se encontró 'inbrowser: True' — puede que ya esté desactivado")

with open(target, "w", encoding="utf-8") as f:
    f.write(content)

print("[patch] OK lora_manager.py parchado correctamente")
print("\n=== install_dataset_refiner.py completado ===")
