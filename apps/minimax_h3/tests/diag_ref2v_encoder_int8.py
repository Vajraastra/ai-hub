r"""
Variante de diag_ref2v_photo_max.py con el encoder Qwen3-VL-32B en
INT8 ConvRot en vez de NVFP4/AWQ (s96) -- aisla si la cuantizacion agresiva
del encoder de texto/vision (el que procesa la imagen de referencia via
clip.tokenize()) contribuye a la perdida de identidad en Ref2V. IMPORTANTE:
el repo oficial de MiniMax-AI confirma que la fidelidad de identidad de las
plataformas comerciales depende de H3-Context-IR, un sistema de
preprocesamiento HOSTEADO nunca liberado ("critical to the quality of the
final output") -- este test probablemente da como maximo una mejora
marginal, no cierra esa brecha arquitectonica. Se corre para descartarlo
formalmente. Mismo prompt/seed/steps/ref_image_size=max que el test anterior
(con NVFP4), comparable 1:1 -- solo cambia clip_name.

Uso:
  hub-webui\.venv\Scripts\python.exe apps\minimax_h3\tests\diag_ref2v_encoder_int8.py
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

# Escrito a partir de lo que se ve en la foto: mujer con lentes, flequillo
# recogido, camisa azul holgada de cuello abierto, jeans blancos de tiro
# alto, de pie en un pasillo tipo casa japonesa (puertas correderas de
# madera, intercomunicador en la pared, escalera con barandal de madera
# visible por una puerta, refrigerador visible por otra). Expresion neutra,
# de frente a camara.
_PROMPT = (
    "Use <Picture 1> as the reference for the person's identity, hairstyle, glasses and "
    "clothing. Static handheld documentary shot, the person from <Picture 1> standing in the "
    "same wood-trimmed home hallway, turning her head slightly toward the camera and giving a "
    "small natural smile, hands resting at her sides. Audio: quiet indoor room tone, a faint "
    "household hum."
)
_SEED = 9001
_WIDTH, _HEIGHT = 1344, 768
_STEPS = 20   # sin turbo


def _name_of(specs, key: str) -> str:
    return next(s for s in specs if s.key == key).dest_name


async def main():
    missing = (dl.missing_specs(_MODELS_ROOT, dl.REF2V_MODELS)
              + dl.missing_specs(_MODELS_ROOT, dl.TEXT_ENCODER_INT8_CONVROT))
    if missing:
        print(f"Faltan modelos: {[s.key for s in missing]}"); return
    if not _REF_IMAGE.is_file():
        print(f"No está la imagen de referencia: {_REF_IMAGE}"); return

    comfy = ComfyClient(port=8188)
    if not await comfy.health_check():
        print("ComfyUI no responde en :8188 — arráncalo primero.")
        return

    ref_b64 = base64.b64encode(_REF_IMAGE.read_bytes()).decode("ascii")

    wf = load_workflow("h3_ref2v_t2v_full.json")   # sin nodo de turbo (a diferencia de h3_ref2v_t2v.json)
    _apply_ref_images(wf, [ref_b64])

    params = {
        "unet_name": _name_of(dl.REF2V_MODELS, "ref2v_dit"),
        "clip_name": _name_of(dl.TEXT_ENCODER_INT8_CONVROT, "encoder_int8_convrot"),
        "clip_device": "cuda:0",
        "video_vae_name": _name_of(dl.GATED_MODELS, "video_vae"),
        "audio_vae_name": _name_of(dl.GATED_MODELS, "audio_vae"),
        "prompt": _PROMPT, "width": _WIDTH, "height": _HEIGHT,
        "length": 124,   # 5s @ 24fps
        "seed": _SEED, "steps": _STEPS,
        "ref_image_size": "max",
    }

    print(f"DiT={params['unet_name']} clip={params['clip_name']} — Ref2V SIN turbo, "
          f"{_STEPS} pasos, 1 imagen de referencia, ref_image_size=max, encoder INT8 ConvRot")

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
    out_path = _OUT_DIR / f"ref2v_photo_encoder_int8.{ext}"
    out_path.write_bytes(data)
    print(f"\n✓ {dt:.1f}s · {out_path} ({out_path.stat().st_size/1e6:.1f} MB)")
    print("✓ prueba completa")


if __name__ == "__main__":
    asyncio.run(main())
