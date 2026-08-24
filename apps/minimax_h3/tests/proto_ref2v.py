r"""
Prototipo standalone de Ref2V (ref2va) — NO toca pipeline.py ni las rutas de
produccion. Objetivo unico de esta sesion (s95): confirmar que el modo Ref2V
corre en este hardware y medir tiempo, antes de decidir si se integra de
verdad al modulo.

Requiere: modelos de core/model_downloader.REF2V_MODELS ya descargados, y
ComfyUI (:8188) arrancado.

Uso:
  hub-webui\.venv\Scripts\python.exe apps\minimax_h3\tests\proto_ref2v.py
"""
import sys
import time
import base64
import asyncio
from pathlib import Path

try:  # consola Windows suele ser cp1252; usamos ✓/✗
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
_REF_IMAGE = Path(
    "E:/githubs/ai-hub/apps/ai-toolkit/venv/Lib/site-packages/"
    "matplotlib/mpl-data/sample_data/grace_hopper.jpg"
)
_OUT_DIR = _HERE.parent / "data" / "history"


def _name_of(specs, key: str) -> str:
    return next(s for s in specs if s.key == key).dest_name


async def main():
    missing = dl.missing_specs(_MODELS_ROOT, dl.REF2V_MODELS)
    if missing:
        print(f"Faltan modelos Ref2V: {[s.key for s in missing]} — corre la descarga primero.")
        return

    ref_b64 = base64.b64encode(_REF_IMAGE.read_bytes()).decode("ascii")

    comfy = ComfyClient(port=8188)
    if not await comfy.health_check():
        print("ComfyUI no responde en :8188")
        return

    available_loras = await comfy.get_models("loras")
    turbo_lora_name = _resolve_lora(_name_of(dl.REF2V_MODELS, "ref2v_turbo"), available_loras)

    wf = load_workflow("h3_ref2v.json")
    params = {
        "video_vae_name": _name_of(dl.GATED_MODELS, "video_vae"),
        "audio_vae_name": _name_of(dl.GATED_MODELS, "audio_vae"),
        "unet_name": _name_of(dl.REF2V_MODELS, "ref2v_dit"),
        "clip_name": _name_of(dl.GATED_MODELS, "encoder"),
        "clip_device": "cuda:0",
        "turbo_lora_name": turbo_lora_name,
        "ref_image_b64": ref_b64,
        "prompt": (
            "Use <Picture 1> as the reference for the person's identity and clothing. "
            "Static handheld documentary shot, the person from <Picture 1> standing at "
            "a wooden desk, looking at the camera and giving a short nod. "
            "Audio: quiet indoor room tone, soft footsteps."
        ),
        "width": 1344,
        "height": 768,
        "length": 124,   # 5s
        "steps": 4,       # turbo de 4 pasos
        "seed": 42,
    }

    print(f"DiT={params['unet_name']}")
    print(f"turbo={params['turbo_lora_name']}")
    print(f"ref_image={_REF_IMAGE.name} ({_REF_IMAGE.stat().st_size/1e3:.0f}KB)")

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
    run_id = time.strftime("%Y%m%d-%H%M%S") + "-ref2v-proto"
    ext = Path(filename).suffix.lstrip(".") or "mp4"
    out_path = _OUT_DIR / f"{run_id}.{ext}"
    out_path.write_bytes(data)
    print(f"\n✓ {dt:.1f}s · {out_path} ({out_path.stat().st_size/1e6:.1f} MB)")


if __name__ == "__main__":
    asyncio.run(main())
