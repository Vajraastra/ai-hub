r"""
Re-prueba de continuidad Ref2V (s97) tras el fix de ref_images: la medición de
s96 (proto_continuity.py, MAE 58 vs 2.98 de I2V) se hizo con h3_ref2v.json
ROTO (dict anidado "ref_images" descartado en silencio) -- la continuación
Ref2V de s96 corrió SIN la imagen de referencia, así que el descarte de Ref2V
para encadenar queda invalidado. Este script reusa el MISMO último frame base
de s96 (data/continuity_frames/base_last_frame.png) y corre SOLO la
continuación Ref2V con la plantilla corregida, misma config que s96 (turbo 4
pasos, seed 12347, length 124): la única variable cambiada es el fix, el MAE
resultante es comparable 1:1 contra los números de s96.

Uso:
  hub-webui\.venv\Scripts\python.exe apps\minimax_h3\tests\diag_continuity_ref2v_fix.py
"""
import sys
import time
import base64
import asyncio
import subprocess
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

_HERE = Path(__file__).resolve().parent
_APPS = _HERE.parent.parent
if str(_APPS) not in sys.path:
    sys.path.insert(0, str(_APPS))

from minimax_h3.core.comfy_client import ComfyClient, load_workflow, ComfyError
from minimax_h3.core import model_downloader as dl
from minimax_h3.core.pipeline import _resolve_lora

_MODELS_ROOT = Path("E:/Models")
_OUT_DIR = _HERE.parent / "data" / "history"
_FRAMES_DIR = _HERE.parent / "data" / "continuity_frames"
_BASE_LAST = _FRAMES_DIR / "base_last_frame.png"

# Idéntico a proto_continuity.py (s96)
_CONTINUATION_PROMPT_REF2V = (
    "Use <Picture 1> as the reference for the person's identity, clothing and "
    "exact body pose. Static wide shot, the person from <Picture 1> continues "
    "walking briskly from left to right across the empty gray studio, mid-stride, "
    "arms swinging naturally, same side view, same neutral flat lighting, static "
    "camera, no motion blur. Audio: footsteps on a hard floor continuing."
)
_SEED = 12345 + 2   # el mismo seed de la continuación Ref2V de s96
_LENGTH = 124
_WIDTH, _HEIGHT = 1344, 768

# Números de s96 para referencia directa en la salida
_S96_MAE_I2V = 2.98
_S96_MAE_REF2V = 58.0


def _name_of(specs, key: str) -> str:
    return next(s for s in specs if s.key == key).dest_name


def _ffmpeg_first_frame(video: Path, out_png: Path):
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(video), "-frames:v", "1", "-q:v", "2", str(out_png)],
        check=True, capture_output=True,
    )


def _mae(a: Path, b: Path) -> float:
    from PIL import Image
    import numpy as np
    ia = np.asarray(Image.open(a).convert("RGB"), dtype=np.float32)
    ib = np.asarray(Image.open(b).convert("RGB"), dtype=np.float32)
    if ia.shape != ib.shape:
        ib = np.asarray(Image.open(b).convert("RGB").resize(ia.shape[1::-1]), dtype=np.float32)
    return float(np.abs(ia - ib).mean())


async def main():
    if not _BASE_LAST.is_file():
        print(f"No está el frame base de s96: {_BASE_LAST} — corre proto_continuity.py completo.")
        return
    missing = dl.missing_specs(_MODELS_ROOT, dl.REF2V_MODELS)
    if missing:
        print(f"Faltan modelos Ref2V: {[s.key for s in missing]}"); return

    comfy = ComfyClient(port=8188)
    if not await comfy.health_check():
        print("ComfyUI no responde en :8188 — arráncalo primero.")
        return

    wf = load_workflow("h3_ref2v.json")
    assert "ref_images.ref_image_0" in wf["6"]["inputs"], \
        "h3_ref2v.json no tiene el fix de clave punteada"

    available_loras = await comfy.get_models("loras")
    ref2v_turbo = _resolve_lora(_name_of(dl.REF2V_MODELS, "ref2v_turbo"), available_loras)
    ref_b64 = base64.b64encode(_BASE_LAST.read_bytes()).decode("ascii")

    params = {
        "unet_name": _name_of(dl.REF2V_MODELS, "ref2v_dit"),
        "clip_name": _name_of(dl.GATED_MODELS, "encoder"),
        "clip_device": "cuda:0",
        "video_vae_name": _name_of(dl.GATED_MODELS, "video_vae"),
        "audio_vae_name": _name_of(dl.GATED_MODELS, "audio_vae"),
        "turbo_lora_name": ref2v_turbo,
        "prompt": _CONTINUATION_PROMPT_REF2V,
        "width": _WIDTH, "height": _HEIGHT, "length": _LENGTH,
        "seed": _SEED, "steps": 4, "ref_image_b64": ref_b64,
        "ref_image_size": "max",
    }
    print(f"Ref2V continuación CON fix — seed {_SEED}, turbo 4 pasos, ref = último frame base de s96")
    t0 = time.time()

    def on_progress(step, total):
        print(f"   · sampling {step}/{total}  ({time.time()-t0:.0f}s)", flush=True)

    try:
        data, filename = await comfy.run_workflow(wf, params, on_progress=on_progress)
    except ComfyError as e:
        print(f"✗ falló tras {time.time()-t0:.1f}s: {e}")
        return
    dt = time.time() - t0

    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    ext = Path(filename).suffix.lstrip(".") or "mp4"
    out_path = _OUT_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}-continuity-ref2v_fix.{ext}"
    out_path.write_bytes(data)
    print(f"✓ {dt:.1f}s · {out_path} ({out_path.stat().st_size/1e6:.1f} MB)")

    first = _FRAMES_DIR / "ref2v_fix_continuation_first_frame.png"
    _ffmpeg_first_frame(out_path, first)
    mae = _mae(_BASE_LAST, first)

    print("\n" + "=" * 64)
    print(f"MAE base vs Ref2V CON fix:   {mae:8.2f}  (menor = más literal)")
    print(f"  referencia s96 — I2V:      {_S96_MAE_I2V:8.2f}")
    print(f"  referencia s96 — Ref2V roto:{_S96_MAE_REF2V:7.2f}")
    print("=" * 64)
    print(f"\nFrames: {_BASE_LAST.name} vs {first.name} en {_FRAMES_DIR}")


if __name__ == "__main__":
    asyncio.run(main())
