r"""
Prototipo standalone: usa el checkpoint ref2va (INT8 ConvRot) como T2V puro,
SIN ninguna referencia conectada -- todos los inputs ref_* del nodo
MiniMaxH3ReferenceToVideo son opcionales (min=0). Si esto genera video de
calidad comparable o mejor que fl2va, ref2va podria reemplazarlo del todo y
consolidar en un solo DiT. NO toca pipeline.py ni las rutas de produccion.

Mismo prompt que las pruebas de battery.py (lago de montana) para poder
comparar directamente contra los resultados de fl2va/NVFP4 ya generados.

Uso:
  hub-webui\.venv\Scripts\python.exe apps\minimax_h3\tests\proto_ref2v_t2v.py
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
from minimax_h3.core.pipeline import _resolve_lora

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

    available_loras = await comfy.get_models("loras")
    turbo_lora_name = _resolve_lora(_name_of(dl.REF2V_MODELS, "ref2v_turbo"), available_loras)

    wf = load_workflow("h3_ref2v_t2v.json")
    params = {
        "video_vae_name": _name_of(dl.GATED_MODELS, "video_vae"),
        "audio_vae_name": _name_of(dl.GATED_MODELS, "audio_vae"),
        "unet_name": _name_of(dl.REF2V_MODELS, "ref2v_dit"),
        "clip_name": _name_of(dl.GATED_MODELS, "encoder"),
        "clip_device": "cuda:0",
        "turbo_lora_name": turbo_lora_name,
        "prompt": _PROMPT,
        "width": 1344,
        "height": 768,
        "length": 124,   # 5s
        "steps": 4,       # turbo de 4 pasos (ref2v)
        "seed": 42,
    }

    print(f"DiT={params['unet_name']} (ref2va, sin referencias -> T2V puro)")
    print(f"turbo={turbo_lora_name}")

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
    run_id = time.strftime("%Y%m%d-%H%M%S") + "-ref2v-t2v-proto"
    ext = Path(filename).suffix.lstrip(".") or "mp4"
    out_path = _OUT_DIR / f"{run_id}.{ext}"
    out_path.write_bytes(data)
    print(f"\n✓ {dt:.1f}s · {out_path} ({out_path.stat().st_size/1e6:.1f} MB)")


if __name__ == "__main__":
    asyncio.run(main())
