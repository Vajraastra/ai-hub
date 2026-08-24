r"""
Igual que proto_ref2v_t2v.py pero SIN turbo LoRA (20 pasos completos) -- mide
calidad/tiempo a full del checkpoint ref2va como T2V puro. NO toca pipeline.py
ni las rutas de produccion.

Uso:
  hub-webui\.venv\Scripts\python.exe apps\minimax_h3\tests\proto_ref2v_t2v_full.py
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
_OUT_DIR = _HERE.parent / "data" / "history"

_PROMPT = (
    "Static handheld documentary shot of a calm mountain lake at sunrise, mist "
    "drifting low over the still water, a wooden dock extending into frame. "
    "A light breeze moves the reeds at the shore. Audio: gentle water lapping, "
    "distant birdsong, soft wind. No text, no people, no camera movement."
)


def _name_of(specs, key: str) -> str:
    return next(s for s in specs if s.key == key).dest_name


async def main():
    missing = dl.missing_specs(_MODELS_ROOT, dl.REF2V_MODELS)
    if missing:
        print(f"Faltan modelos Ref2V: {[s.key for s in missing]}")
        return

    comfy = ComfyClient(port=8188)
    if not await comfy.health_check():
        print("ComfyUI no responde en :8188")
        return

    wf = load_workflow("h3_ref2v_t2v_full.json")
    params = {
        "video_vae_name": _name_of(dl.GATED_MODELS, "video_vae"),
        "audio_vae_name": _name_of(dl.GATED_MODELS, "audio_vae"),
        "unet_name": _name_of(dl.REF2V_MODELS, "ref2v_dit"),
        "clip_name": _name_of(dl.GATED_MODELS, "encoder"),
        "clip_device": "cuda:0",
        "prompt": _PROMPT,
        "width": 1344,
        "height": 768,
        "length": 124,   # 5s
        "steps": 20,      # sin turbo, pasos completos
        "seed": 42,
    }

    print(f"DiT={params['unet_name']} (ref2va, sin referencias, SIN turbo, 20 pasos)")

    def on_progress(step, total):
        print(f"   · sampling {step}/{total}", flush=True)

    t0 = time.time()
    try:
        data, filename = await comfy.run_workflow(wf, params, on_progress=on_progress)
    except ComfyError as e:
        print(f"✗ ComfyError tras {time.time()-t0:.1f}s: {e}")
        return
    dt = time.time() - t0

    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    run_id = time.strftime("%Y%m%d-%H%M%S") + "-ref2v-t2v-full-proto"
    ext = Path(filename).suffix.lstrip(".") or "mp4"
    out_path = _OUT_DIR / f"{run_id}.{ext}"
    out_path.write_bytes(data)
    print(f"\n✓ {dt:.1f}s · {out_path} ({out_path.stat().st_size/1e6:.1f} MB)")


if __name__ == "__main__":
    asyncio.run(main())
