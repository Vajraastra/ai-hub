r"""
Prueba A/B del encoder "Heretic" (Qwen3-VL-32B ablacionado) vs el encoder
estandar: mismo prompt/seed/checkpoint (ref2va, T2V puro, turbo 4 pasos), solo
cambia el text encoder. SIN ningun LoRA de por medio -- el objetivo es ver si
el encoder ablacionado por si solo mejora la fidelidad anatomica que, segun el
propio README de Hearmeman, el modelo base "aproxima" (una forma generica,
ignora lo que se le pida sobre ella). Retoma la investigacion de s93 (que
descarto esta via solo por un analisis de coseno de terceros, gap 0.0023) para
confirmarlo en vivo. NO toca pipeline.py ni las rutas de produccion.

Uso:
  hub-webui\.venv\Scripts\python.exe apps\minimax_h3\tests\proto_heretic_ab.py
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

# Sin trigger de LoRA -- solo prompt describiendo la anatomia que el modelo
# base "aproxima" segun Hearmeman/minimax-h3-loras README.
_PROMPT = (
    "Static medium shot of a woman standing in a softly lit bedroom, warm "
    "natural window light through a curtain, shallow depth of field, soft "
    "film grain. No camera movement, locked-off tripod shot. "
    "She stands topless facing the camera with a calm, natural expression, "
    "large natural breasts, medium sized brown areolas, relaxed posture, "
    "gentle breathing motion. "
    "Audio: quiet indoor room tone, faint distant birdsong through a window, "
    "no dialogue, no music."
)


def _name_of(specs, key: str) -> str:
    return next(s for s in specs if s.key == key).dest_name


async def _run(comfy, wf, params, label: str):
    print(f"\n=== {label} (encoder={params['clip_name']}) ===")

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
    run_id = time.strftime("%Y%m%d-%H%M%S") + f"-heretic-{label}"
    ext = Path(filename).suffix.lstrip(".") or "mp4"
    out_path = _OUT_DIR / f"{run_id}.{ext}"
    out_path.write_bytes(data)
    print(f"✓ {dt:.1f}s · {out_path} ({out_path.stat().st_size/1e6:.1f} MB)")


async def main():
    missing = dl.missing_specs(_MODELS_ROOT, dl.HERETIC_MODELS)
    if missing:
        print(f"Falta el encoder Heretic: {[s.key for s in missing]}")
        return

    comfy = ComfyClient(port=8188)
    if not await comfy.health_check():
        print("ComfyUI no responde en :8188")
        return

    available_loras = await comfy.get_models("loras")
    turbo_lora_name = _resolve_lora(_name_of(dl.REF2V_MODELS, "ref2v_turbo"), available_loras)

    wf = load_workflow("h3_ref2v_t2v.json")
    base_params = {
        "video_vae_name": _name_of(dl.GATED_MODELS, "video_vae"),
        "audio_vae_name": _name_of(dl.GATED_MODELS, "audio_vae"),
        "unet_name": _name_of(dl.REF2V_MODELS, "ref2v_dit"),
        "clip_device": "cuda:0",
        "turbo_lora_name": turbo_lora_name,
        "prompt": _PROMPT,
        "width": 1344,
        "height": 768,
        "length": 124,   # 5s
        "steps": 4,       # turbo 4 pasos
        "seed": 999,
    }

    await _run(comfy, wf, {**base_params, "clip_name": _name_of(dl.GATED_MODELS, "encoder")},
              "estandar")
    await _run(comfy, wf, {**base_params, "clip_name": _name_of(dl.HERETIC_MODELS, "heretic_encoder")},
              "heretic")


if __name__ == "__main__":
    asyncio.run(main())
