r"""
Mismo test que quality_test_int8_noturbo.py pero para la variante Mixed
INT4/INT8 Convrot -- comparar si sin turbo (20 pasos) el ghosting desaparece
y qué tan cerca queda de INT8 puro, y comparar tiempos entre las dos
variantes a pasos completos. Mismo prompt/seed que el resto de la batería.
NO toca pipeline.py.

Uso:
  hub-webui\.venv\Scripts\python.exe apps\minimax_h3\tests\quality_test_mixed_noturbo.py
"""
import sys
import time
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

_MODELS_ROOT = Path("E:/Models")
_OUT_DIR = _HERE.parent / "data" / "quality_test"

_PROMPT = (
    "Static handheld documentary shot of an elderly woman with curly gray hair sitting at a "
    "wooden kitchen table, sunlight streaming through a window behind her, steam rising slowly "
    "from a ceramic cup of tea in front of her, dust particles visible in the light beams. "
    "She looks down at the cup and slowly wraps both hands around it. Audio: soft ambient room "
    "tone, a distant clock ticking, gentle steam sound."
)
_SEED = 4242
_WIDTH, _HEIGHT = 1344, 768
_STEPS = 20


def _name_of(specs, key: str) -> str:
    return next(s for s in specs if s.key == key).dest_name


async def main():
    missing = dl.missing_specs(_MODELS_ROOT, dl.FL2V_MIXED_REJECTED)
    if missing:
        print(f"Falta: {[s.key for s in missing]}"); return

    comfy = ComfyClient(port=8188)
    if not await comfy.health_check():
        print("ComfyUI no responde en :8188 — arráncalo primero.")
        return

    wf = load_workflow("h3_video.json")
    params = {
        "unet_name": _name_of(dl.FL2V_MIXED_REJECTED, "fl2v_mixed_dit"),
        "clip_name": _name_of(dl.GATED_MODELS, "encoder"),
        "clip_device": "cuda:0",
        "video_vae_name": _name_of(dl.GATED_MODELS, "video_vae"),
        "audio_vae_name": _name_of(dl.GATED_MODELS, "audio_vae"),
        "prompt": _PROMPT, "width": _WIDTH, "height": _HEIGHT,
        "length": 124,
        "seed": _SEED, "steps": _STEPS,
    }

    print(f"DiT={params['unet_name']} — SIN turbo, {_STEPS} pasos")

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
    out_path = _OUT_DIR / f"fl2va_mixed_int4int8_convrot_noturbo.{ext}"
    out_path.write_bytes(data)
    print(f"\n✓ {dt:.1f}s · {out_path} ({out_path.stat().st_size/1e6:.1f} MB)")
    print("✓ prueba completa")


if __name__ == "__main__":
    asyncio.run(main())
