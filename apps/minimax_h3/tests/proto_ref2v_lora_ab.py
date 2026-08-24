r"""
Prueba A/B del LoRA Hearmeman HMBreasts sobre el checkpoint ref2va: misma
semilla/prompt, dos corridas -- una con el LoRA en fuerza 0.0 (no-op, mismo
grafo) y otra en fuerza 1.0 (activo) -- para confirmar si el efecto semantico
de decensurado realmente se transfiere entre checkpoints (arquitectura ya
confirmada compatible via headers safetensors, s95). NO toca pipeline.py ni
las rutas de produccion.

Prompt siguiendo la guia oficial de estructura de MiniMax H3 (fal.ai):
shot/composicion -> camara -> iluminacion -> accion/performance -> audio ->
trigger word al final (por convencion de lora_presets.py).

Uso:
  hub-webui\.venv\Scripts\python.exe apps\minimax_h3\tests\proto_ref2v_lora_ab.py
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
    "Static medium shot of a woman standing in a softly lit bedroom, warm "
    "natural window light through a curtain, shallow depth of field, soft "
    "film grain. No camera movement, locked-off tripod shot. "
    "She stands topless facing the camera with a calm, natural expression, "
    "large natural breasts, medium sized brown areolas, relaxed posture, "
    "gentle breathing motion. "
    "Audio: quiet indoor room tone, faint distant birdsong through a window, "
    "no dialogue, no music. "
    "HMBreasts"
)


def _name_of(specs, key: str) -> str:
    return next(s for s in specs if s.key == key).dest_name


async def _run(comfy, wf, params, label: str):
    print(f"\n=== {label} (hm_strength={params['hm_strength']}) ===")

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
    run_id = time.strftime("%Y%m%d-%H%M%S") + f"-ref2v-lora-{label}"
    ext = Path(filename).suffix.lstrip(".") or "mp4"
    out_path = _OUT_DIR / f"{run_id}.{ext}"
    out_path.write_bytes(data)
    print(f"✓ {dt:.1f}s · {out_path} ({out_path.stat().st_size/1e6:.1f} MB)")


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
    hm_lora_name = _resolve_lora("HMBreasts_085e0750_e40.safetensors", available_loras)

    wf = load_workflow("h3_ref2v_t2v_lora.json")
    base_params = {
        "video_vae_name": _name_of(dl.GATED_MODELS, "video_vae"),
        "audio_vae_name": _name_of(dl.GATED_MODELS, "audio_vae"),
        "unet_name": _name_of(dl.REF2V_MODELS, "ref2v_dit"),
        "clip_name": _name_of(dl.GATED_MODELS, "encoder"),
        "clip_device": "cuda:0",
        "turbo_lora_name": turbo_lora_name,
        "hm_lora_name": hm_lora_name,
        "prompt": _PROMPT,
        "width": 1344,
        "height": 768,
        "length": 124,   # 5s
        "steps": 4,       # turbo 4 pasos
        "seed": 777,
    }

    print(f"DiT={base_params['unet_name']}")
    print(f"turbo={turbo_lora_name}")
    print(f"HMBreasts={hm_lora_name}")

    for strength in (0.3, 0.5, 0.7):
        await _run(comfy, wf, {**base_params, "hm_strength": strength},
                  f"str{str(strength).replace('.', '')}")


if __name__ == "__main__":
    asyncio.run(main())
