"""
Modo "medir filtro": corre solo el clasificador NSFW sobre una imagen y devuelve
el score, sin hacer ningún swap. Sirve para calibrar el umbral con datos reales
(cada tipo de prenda da un score distinto) sin meter la foto sensible en el
pipeline de face swap.

Se ejecuta en un subproceso con el venv de ComfyUI (que ya tiene transformers,
torch y el modelo descargado), así el hub no necesita esas dependencias pesadas.
"""
import os
import json
import tempfile
import subprocess
from pathlib import Path

_APP = Path(__file__).resolve().parents[1]        # apps/faceswap
_ROOT = _APP.parent.parent                          # raíz del repo
_PY = _ROOT / "apps" / "comfyui" / "venv" / "Scripts" / "python.exe"
_PROBE = _APP / "patches" / "nsfw_probe.py"
_MODEL = _ROOT / "apps" / "comfyui" / "models" / "nsfw_detector" / "vit-base-nsfw-detector"


class MeasureError(Exception):
    pass


def measure(data: bytes) -> dict:
    if not _PY.exists():
        raise MeasureError("no encuentro el venv de ComfyUI (apps/comfyui/venv)")
    if not _MODEL.exists():
        raise MeasureError("el modelo NSFW aún no está descargado; corré un swap "
                           "una vez (se auto-descarga) y reintentá")
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(data)
            tmp = f.name
        out = subprocess.run([str(_PY), str(_PROBE), tmp, str(_MODEL)],
                             capture_output=True, text=True, timeout=180)
        if out.returncode != 0:
            raise MeasureError(out.stderr.strip()[-500:] or "el probe falló sin mensaje")
        line = out.stdout.strip().splitlines()[-1]
        return json.loads(line)
    except subprocess.TimeoutExpired:
        raise MeasureError("el probe tardó demasiado (>180s)")
    except json.JSONDecodeError:
        raise MeasureError("respuesta inesperada del probe")
    finally:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)
