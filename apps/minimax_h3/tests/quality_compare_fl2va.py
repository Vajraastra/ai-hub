r"""
Comparacion de calidad de las 3 variantes de cuantizacion del DiT FL2VA (s96):
NVFP4 (actual, con el problema "VHS" identificado en s95), INT8 ConvRot puro
y Mixed INT4/INT8 ConvRot -- mismo prompt/seed/turbo para las 3, T2V puro
(sin imagen inicial) para aislar la variable que importa: la cuantizacion del
DiT. NO toca pipeline.py/rutas de produccion.

Guarda cada clip en data/quality_test/ con nombre EXPLICITO de la variante
(no el run_id opaco de _save) para que sea facil de identificar a ojo/en el
explorador de archivos.

Uso:
  hub-webui\.venv\Scripts\python.exe apps\minimax_h3\tests\quality_compare_fl2va.py
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
_DURATION_S = 5.0
_TURBO_STEPS = 8

# (etiqueta legible, ModelSpec-lista de donde sacar el dest_name)
# NOTA (post-s96): GATED_MODELS.dit YA ES la variante int8_convrot (ganadora,
# ver model_downloader.py) -- nvfp4/mixed quedaron en sus listas de deprecados.
_VARIANTS = [
    ("nvfp4",                     dl.FL2V_NVFP4_DEPRECATED, "fl2v_nvfp4_dit"),
    ("int8_convrot",              dl.GATED_MODELS,          "dit"),
    ("mixed_int4int8_convrot",    dl.FL2V_MIXED_REJECTED,   "fl2v_mixed_dit"),
]


def _name_of(specs, key: str) -> str:
    return next(s for s in specs if s.key == key).dest_name


async def _run_variant(comfy: ComfyClient, label: str, unet_name: str,
                       fl2v_turbo: str) -> Path | None:
    print(f"\n{'='*60}\n== variante: {label}  (DiT={unet_name})\n{'='*60}", flush=True)

    wf = load_workflow("h3_video.json")
    wf["5"] = {"class_type": "LoraLoaderModelOnly",
               "inputs": {"model": ["3", 0], "lora_name": fl2v_turbo, "strength_model": 1.0}}
    wf["9"]["inputs"]["model"] = ["5", 0]
    wf["10"]["inputs"]["model"] = ["5", 0]

    params = {
        "unet_name": unet_name,
        "clip_name": _name_of(dl.GATED_MODELS, "encoder"),
        "clip_device": "cuda:0",
        "video_vae_name": _name_of(dl.GATED_MODELS, "video_vae"),
        "audio_vae_name": _name_of(dl.GATED_MODELS, "audio_vae"),
        "prompt": _PROMPT, "width": _WIDTH, "height": _HEIGHT,
        "length": 124,   # 5s @ 24fps, grid 17n+5
        "seed": _SEED, "steps": _TURBO_STEPS,
    }

    def on_progress(step, total):
        print(f"   · {label} sampling {step}/{total}  ({time.time()-t0:.0f}s)", flush=True)

    t0 = time.time()
    try:
        data, filename = await comfy.run_workflow(wf, params, on_progress=on_progress)
    except ComfyError as e:
        print(f"✗ {label} falló tras {time.time()-t0:.1f}s: {e}")
        return None
    dt = time.time() - t0

    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    ext = Path(filename).suffix.lstrip(".") or "mp4"
    out_path = _OUT_DIR / f"fl2va_{label}.{ext}"
    out_path.write_bytes(data)
    print(f"✓ {label}: {dt:.1f}s · {out_path} ({out_path.stat().st_size/1e6:.1f} MB)")
    return out_path


async def main():
    missing = (dl.missing_specs(_MODELS_ROOT, dl.GATED_MODELS)
              + dl.missing_specs(_MODELS_ROOT, dl.FL2V_NVFP4_DEPRECATED)
              + dl.missing_specs(_MODELS_ROOT, dl.FL2V_MIXED_REJECTED))
    if missing:
        print(f"Faltan modelos: {[s.key for s in missing]} — esperar a que terminen las descargas.")
        return

    comfy = ComfyClient(port=8188)
    if not await comfy.health_check():
        print("ComfyUI no responde en :8188 — arráncalo primero.")
        return

    available_loras = await comfy.get_models("loras")
    fl2v_turbo = _resolve_lora(_name_of(dl.GATED_MODELS, "turbo_lora"), available_loras)

    results = []
    for label, specs, key in _VARIANTS:
        unet_name = _name_of(specs, key)
        out = await _run_variant(comfy, label, unet_name, fl2v_turbo)
        results.append((label, out))

    print(f"\n{'='*60}\nRESUMEN\n{'='*60}")
    for label, out in results:
        status = str(out) if out else "FALLÓ"
        print(f"  {label:28s} -> {status}")
    print(f"\nTodos los clips (los que salieron bien) están en: {_OUT_DIR}")
    print("Mismo prompt/seed/turbo/duración para los 3 — comparables 1:1.")


if __name__ == "__main__":
    asyncio.run(main())
