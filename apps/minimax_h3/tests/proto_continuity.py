r"""
Prototipo standalone (s96) -- compara si la imagen de referencia de Ref2V da
la MISMA continuidad literal que I2V (first_frame_b64) al encadenar clips.
NO toca pipeline.py ni las rutas de produccion.

Metodo:
  1. Genera un clip base (fl2va + turbo 8p) con movimiento direccional claro
     (persona caminando de izquierda a derecha) -- facil de juzgar a ojo si
     una continuacion arranca en la MISMA pose/posicion o en una distinta.
  2. Extrae el ULTIMO frame de ese clip (ffmpeg).
  3. Genera continuacion A (I2V): mismo motor fl2va, first_frame = ese frame.
  4. Genera continuacion B (Ref2V): motor ref2va, ref_image_0 = ese frame,
     prompt con tag <Picture 1> (misma convencion que proto_ref2v.py).
  5. Extrae el PRIMER frame de A y de B, calcula MAE de pixeles contra el
     frame base (senal cuantitativa) y deja las 3 imagenes en data/history/
     para inspeccion visual directa.

Uso:
  hub-webui\.venv\Scripts\python.exe apps\minimax_h3\tests\proto_continuity.py
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

_SCENE = (
    "a person walking briskly from left to right across an empty gray studio, "
    "mid-stride, arms swinging naturally, seen from the side, neutral flat "
    "lighting, no camera movement, no motion blur"
)
_BASE_PROMPT = (
    f"Static wide shot of {_SCENE}. Audio: footsteps on a hard floor, steady rhythm."
)
_CONTINUATION_PROMPT_I2V = (
    f"The person continues walking from left to right across the empty gray "
    f"studio, mid-stride, arms swinging naturally, same side view, same "
    f"lighting, static camera. Audio: footsteps on a hard floor continuing."
)
_CONTINUATION_PROMPT_REF2V = (
    "Use <Picture 1> as the reference for the person's identity, clothing and "
    "exact body pose. Static wide shot, the person from <Picture 1> continues "
    "walking briskly from left to right across the empty gray studio, mid-stride, "
    "arms swinging naturally, same side view, same neutral flat lighting, static "
    "camera, no motion blur. Audio: footsteps on a hard floor continuing."
)

_SEED = 12345
_LENGTH = 124   # ~5s a 24fps (grid 17n+5)
_WIDTH, _HEIGHT = 1344, 768


def _name_of(specs, key: str) -> str:
    return next(s for s in specs if s.key == key).dest_name


def _ffmpeg_last_frame(video: Path, out_png: Path):
    """Ultimo frame exacto: invertir el video y tomar el primer frame del resultado."""
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(video), "-vf", "reverse",
         "-frames:v", "1", "-q:v", "2", str(out_png)],
        check=True, capture_output=True,
    )


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


async def _run(comfy: ComfyClient, wf: dict, params: dict, label: str) -> Path:
    print(f"\n-> generando {label} ...")

    def on_progress(step, total):
        print(f"   · {label} sampling {step}/{total}", flush=True)

    t0 = time.time()
    data, filename = await comfy.run_workflow(wf, params, on_progress=on_progress)
    dt = time.time() - t0
    ext = Path(filename).suffix.lstrip(".") or "mp4"
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = _OUT_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}-continuity-{label}.{ext}"
    out_path.write_bytes(data)
    print(f"✓ {label}: {dt:.1f}s · {out_path} ({out_path.stat().st_size/1e6:.1f} MB)")
    return out_path


async def main():
    missing_gated = dl.missing_specs(_MODELS_ROOT, dl.GATED_MODELS)
    missing_ref2v = dl.missing_specs(_MODELS_ROOT, dl.REF2V_MODELS)
    if missing_gated or missing_ref2v:
        print(f"Faltan modelos: gated={[s.key for s in missing_gated]} "
              f"ref2v={[s.key for s in missing_ref2v]}")
        return

    comfy = ComfyClient(port=8188)
    if not await comfy.health_check():
        print("ComfyUI no responde en :8188 -- arrancalo primero.")
        return

    available_loras = await comfy.get_models("loras")
    fl2v_turbo = _resolve_lora(_name_of(dl.GATED_MODELS, "turbo_lora"), available_loras)
    ref2v_turbo = _resolve_lora(_name_of(dl.REF2V_MODELS, "ref2v_turbo"), available_loras)

    common = {
        "video_vae_name": _name_of(dl.GATED_MODELS, "video_vae"),
        "audio_vae_name": _name_of(dl.GATED_MODELS, "audio_vae"),
        "clip_name": _name_of(dl.GATED_MODELS, "encoder"),
        "clip_device": "cuda:0",
        "width": _WIDTH, "height": _HEIGHT, "length": _LENGTH, "seed": _SEED,
    }

    _FRAMES_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Clip base (fl2va + turbo 8p, T2V puro -- sin first_frame)
    wf_base = load_workflow("h3_video.json")
    wf_base["5"] = {"class_type": "LoraLoaderModelOnly",
                     "inputs": {"model": ["3", 0], "lora_name": fl2v_turbo, "strength_model": 1.0}}
    wf_base["9"]["inputs"]["model"] = ["5", 0]
    wf_base["10"]["inputs"]["model"] = ["5", 0]
    params_base = {**common, "unet_name": _name_of(dl.GATED_MODELS, "dit"),
                   "prompt": _BASE_PROMPT, "steps": 8}
    base_video = await _run(comfy, wf_base, params_base, "base")

    base_last = _FRAMES_DIR / "base_last_frame.png"
    _ffmpeg_last_frame(base_video, base_last)
    print(f"   frame base extraido: {base_last}")
    ref_b64 = base64.b64encode(base_last.read_bytes()).decode("ascii")

    # 2. Continuacion I2V (fl2va + turbo 8p, first_frame = ultimo frame del base)
    wf_i2v = load_workflow("h3_video.json")
    wf_i2v["5"] = {"class_type": "LoraLoaderModelOnly",
                    "inputs": {"model": ["3", 0], "lora_name": fl2v_turbo, "strength_model": 1.0}}
    wf_i2v["9"]["inputs"]["model"] = ["5", 0]
    wf_i2v["10"]["inputs"]["model"] = ["5", 0]
    wf_i2v["20"] = {"class_type": "ETN_LoadImageBase64", "inputs": {"image": "{{first_frame_b64}}"}}
    wf_i2v["6"]["inputs"]["first_frame"] = ["20", 0]
    params_i2v = {**common, "unet_name": _name_of(dl.GATED_MODELS, "dit"),
                  "prompt": _CONTINUATION_PROMPT_I2V, "steps": 8, "first_frame_b64": ref_b64,
                  "seed": _SEED + 1}
    i2v_video = await _run(comfy, wf_i2v, params_i2v, "i2v_continuation")
    i2v_first = _FRAMES_DIR / "i2v_continuation_first_frame.png"
    _ffmpeg_first_frame(i2v_video, i2v_first)

    # 3. Continuacion Ref2V (ref2va + turbo 4p, ref_image_0 = ultimo frame del base)
    wf_ref2v = load_workflow("h3_ref2v.json")
    params_ref2v = {**common, "unet_name": _name_of(dl.REF2V_MODELS, "ref2v_dit"),
                    "turbo_lora_name": ref2v_turbo, "prompt": _CONTINUATION_PROMPT_REF2V,
                    "steps": 4, "ref_image_b64": ref_b64, "seed": _SEED + 2}
    ref2v_video = await _run(comfy, wf_ref2v, params_ref2v, "ref2v_continuation")
    ref2v_first = _FRAMES_DIR / "ref2v_continuation_first_frame.png"
    _ffmpeg_first_frame(ref2v_video, ref2v_first)

    # 4. Comparacion cuantitativa (MAE de pixeles contra el frame base)
    mae_i2v = _mae(base_last, i2v_first)
    mae_ref2v = _mae(base_last, ref2v_first)
    print("\n" + "=" * 60)
    print(f"MAE base vs I2V primer frame:    {mae_i2v:8.2f}  (menor = mas literal)")
    print(f"MAE base vs Ref2V primer frame:  {mae_ref2v:8.2f}  (menor = mas literal)")
    print("=" * 60)
    print(f"\nFrames guardados en: {_FRAMES_DIR}")
    print(f"  base:   {base_last.name}")
    print(f"  i2v:    {i2v_first.name}")
    print(f"  ref2v:  {ref2v_first.name}")


if __name__ == "__main__":
    asyncio.run(main())
