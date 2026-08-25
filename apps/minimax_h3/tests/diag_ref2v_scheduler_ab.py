r"""
A/B rapido y barato (s96): la plantilla oficial de ComfyUI para Ref2V
(video_minimax_h3_r2v.json) documenta textualmente "beta or normal scheduler
tends to outperform simple for reference-heavy prompts like this one" --
nosotros usamos "simple" (copiado del patron T2V/FL2VA) en las 4 plantillas
h3_ref2v*.json, sin notar la recomendacion especifica de Ref2V. Corre 2 clips
CORTOS (~2s) con la misma foto/seed/prompt, cambiando SOLO el scheduler
(beta, normal), para ver si cambia algo antes de invertir en un test largo.
NO toca pipeline.py ni las plantillas (mutacion del dict en memoria).

Uso:
  hub-webui\.venv\Scripts\python.exe apps\minimax_h3\tests\diag_ref2v_scheduler_ab.py
"""
import sys
import time
import base64
import asyncio
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
from minimax_h3.core.pipeline import _apply_ref_images

_MODELS_ROOT = Path("E:/Models")
_OUT_DIR = _HERE.parent / "data" / "quality_test"
_REF_IMAGE = _HERE.parent / "data" / "reference_samples" / "woman_hallway_photo.png"

_PROMPT = (
    "Use <Picture 1> as the reference for the person's identity, hairstyle, glasses and "
    "clothing. Static handheld documentary shot, the person from <Picture 1> standing in the "
    "same wood-trimmed home hallway, turning her head slightly toward the camera and giving a "
    "small natural smile, hands resting at her sides. Audio: quiet indoor room tone, a faint "
    "household hum."
)
_SEED = 9001
_WIDTH, _HEIGHT = 1344, 768
_STEPS = 20
_LENGTH = 56   # ~2.3s @ 24fps (grid 17n+5), corto a proposito -- solo para ver si cambia algo


def _name_of(specs, key: str) -> str:
    return next(s for s in specs if s.key == key).dest_name


async def run_one(comfy, scheduler: str):
    ref_b64 = base64.b64encode(_REF_IMAGE.read_bytes()).decode("ascii")
    wf = load_workflow("h3_ref2v_t2v_full.json")
    _apply_ref_images(wf, [ref_b64])
    wf["9"]["inputs"]["scheduler"] = scheduler   # override directo, sin tocar la plantilla

    params = {
        "unet_name": _name_of(dl.REF2V_MODELS, "ref2v_dit"),
        "clip_name": _name_of(dl.GATED_MODELS, "encoder"),
        "clip_device": "cuda:0",
        "video_vae_name": _name_of(dl.GATED_MODELS, "video_vae"),
        "audio_vae_name": _name_of(dl.GATED_MODELS, "audio_vae"),
        "prompt": _PROMPT, "width": _WIDTH, "height": _HEIGHT,
        "length": _LENGTH, "seed": _SEED, "steps": _STEPS,
        "ref_image_size": "max",
    }
    print(f"\n== scheduler={scheduler} ==")
    t0 = time.time()

    def on_progress(step, total):
        print(f"   · [{scheduler}] {step}/{total}  ({time.time()-t0:.0f}s)", flush=True)

    try:
        data, filename = await comfy.run_workflow(wf, params, on_progress=on_progress)
    except ComfyError as e:
        print(f"✗ [{scheduler}] falló tras {time.time()-t0:.1f}s: {e}")
        return None
    dt = time.time() - t0
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    ext = Path(filename).suffix.lstrip(".") or "mp4"
    out_path = _OUT_DIR / f"ref2v_scheduler_{scheduler}.{ext}"
    out_path.write_bytes(data)
    print(f"✓ [{scheduler}] {dt:.1f}s · {out_path} ({out_path.stat().st_size/1e6:.1f} MB)")
    return out_path


async def main():
    missing = dl.missing_specs(_MODELS_ROOT, dl.REF2V_MODELS)
    if missing:
        print(f"Faltan modelos Ref2V: {[s.key for s in missing]}"); return
    if not _REF_IMAGE.is_file():
        print(f"No está la imagen de referencia: {_REF_IMAGE}"); return

    comfy = ComfyClient(port=8188)
    if not await comfy.health_check():
        print("ComfyUI no responde en :8188 — arráncalo primero.")
        return

    for scheduler in ("beta", "normal"):
        await run_one(comfy, scheduler)

    print("\n✓ prueba completa")


if __name__ == "__main__":
    asyncio.run(main())
