r"""
Validación del fix de identidad Ref2V (s97): _apply_ref_images inyectaba
"ref_images" como dict anidado, pero el ejecutor de ComfyUI solo reconoce los
inputs Autogrow como claves planas punteadas ("ref_images.ref_image_0") — el
dict se descartaba en silencio y el nodo corría SIN referencia (causa del
"cero parecido" de toda s96). Este script corre el mismo baseline de s96
(misma foto, mismo prompt, seed 9001, ref_image_size=max, sin turbo, INT8
ConvRot, clip corto ~2.3s) pero con el pipeline ya corregido: si ahora la
mujer generada se parece a la foto, el bug queda confirmado y cerrado.

Señal secundaria: con la referencia realmente dentro del grafo, los tokens de
referencia viajan en cada paso de sampling — el tiempo por paso debe subir
notablemente respecto a las corridas "rotas" de s96.

Uso:
  hub-webui\.venv\Scripts\python.exe apps\minimax_h3\tests\diag_ref2v_fix_identity.py
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
_WIDTH, _HEIGHT = 1344, 768
_STEPS = 20
_LENGTH = 56   # ~2.3s @ 24fps, igual que los diag A/B de s96
_SEED = 9001   # el mismo seed del baseline "cero parecido" de s96


def _name_of(specs, key: str) -> str:
    return next(s for s in specs if s.key == key).dest_name


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

    ref_b64 = base64.b64encode(_REF_IMAGE.read_bytes()).decode("ascii")
    wf = load_workflow("h3_ref2v_t2v_full.json")
    _apply_ref_images(wf, [ref_b64])
    assert "ref_images.ref_image_0" in wf["6"]["inputs"], \
        "el fix de _apply_ref_images no está aplicado en pipeline.py"

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
    print(f"DiT={params['unet_name']} — Ref2V CON fix de ref_images, seed {_SEED}, "
          f"{_STEPS} pasos, length {_LENGTH}, ref_image_size=max")
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
    out_path = _OUT_DIR / f"ref2v_fix_identity_seed{_SEED}.{ext}"
    out_path.write_bytes(data)
    print(f"\n✓ {dt:.1f}s · {out_path} ({out_path.stat().st_size/1e6:.1f} MB)")
    print("✓ prueba completa — compara contra ref2v_photo_max de s96 (mismo seed, sin fix)")


if __name__ == "__main__":
    asyncio.run(main())
