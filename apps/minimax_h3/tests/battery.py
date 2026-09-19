r"""
Batería de pruebas del módulo MiniMax H3 — diagnóstico + generación real end-to-end.

Ejecuta con el python portable del hub (tiene aiohttp+requests):

  hub-webui\.venv\Scripts\python.exe apps\minimax_h3\tests\battery.py --phase all
  ...\python.exe apps\minimax_h3\tests\battery.py --phase diag     # solo diagnóstico
  ...\python.exe apps\minimax_h3\tests\battery.py --phase full     # 1 generación real
  ...\python.exe apps\minimax_h3\tests\battery.py --phase full --duration 15 --turbo

Requiere ComfyUI (:8188) arrancado y los modelos gated descargados
(`core/model_downloader.py`). NO arranca ni apaga ComfyUI: solo lo consume.

Todo lo que imprime queda además en <out_dir>/battery.log (texto plano) y
<out_dir>/results.json (estructurado) — para que un error se pueda leer después
sin haber visto la consola en vivo.
"""
import sys
import json
import time
import argparse
import asyncio
import traceback
from pathlib import Path

try:  # consola Windows suele ser cp1252; el reporte usa ✓/✗
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

_HERE = Path(__file__).resolve().parent
_MODULE = _HERE.parent             # apps/minimax_h3
_APPS = _MODULE.parent              # apps
if str(_APPS) not in sys.path:
    sys.path.insert(0, str(_APPS))

from minimax_h3.core.comfy_client import ComfyClient, ComfyError
from minimax_h3.core import model_downloader as dl
from minimax_h3.core import lora_presets as lp
from minimax_h3.core import pipeline
from minimax_h3.core.pipeline import GenParams

_OUT_ROOT = _MODULE / "data" / "battery"
_MODELS_ROOT = Path("E:/Models")

_REQUIRED_NODES = [
    "MiniMaxH3ImageToVideo", "UNETLoader", "CLIPLoader", "VAELoader",
    "VAEDecodeAudio", "CreateVideo", "SaveVideo", "RandomNoise",
    "KSamplerSelect", "BasicScheduler", "BasicGuider", "SamplerCustomAdvanced",
    "LoraLoaderModelOnly", "ModelAttentionBackend", "MiniMaxLowVRAMAttention",
    "MiniMaxChunkFeedForward", "H3SLAAttention",
]

_LOG_FILE = None


def _p(msg=""):
    print(msg, flush=True)
    if _LOG_FILE:
        with open(_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(str(msg) + "\n")


def _hr(t=""):
    _p("\n" + "═" * 70)
    if t:
        _p(t)
        _p("═" * 70)


# ── Fase DIAG ────────────────────────────────────────────────────────────────
async def phase_diag(comfy: ComfyClient) -> dict:
    _hr("FASE 1 · DIAGNÓSTICO")
    rep = {}
    comfy_up = await comfy.health_check()
    _p(f"ComfyUI :8188   {'✓' if comfy_up else '✗ NO RESPONDE'}")
    rep["comfyui"] = comfy_up
    if not comfy_up:
        return rep

    _p("\nNodos nativos:")
    node_res = {}
    for n in _REQUIRED_NODES:
        ok = await comfy.probe_node(n)
        node_res[n] = ok
        _p(f"  {'✓' if ok else '✗'} {n}")
    rep["nodes"] = node_res

    _p("\nModelos gated (core/model_downloader.GATED_MODELS):")
    missing = dl.missing_specs(_MODELS_ROOT, dl.GATED_MODELS)
    for s in dl.GATED_MODELS:
        present = s not in missing
        p = dl.dest_path(_MODELS_ROOT, s)
        size = f"{p.stat().st_size/1e9:.2f}GB" if present else "—"
        _p(f"  {'✓' if present else '✗ FALTA'} {s.key:12} {s.dest_name}  {size}")
    rep["missing_models"] = [s.key for s in missing]

    _p("\nLoRAs preservadas (core/model_downloader.PRESERVED_LORAS):")
    missing_loras = dl.missing_specs(_MODELS_ROOT, dl.PRESERVED_LORAS)
    for s in dl.PRESERVED_LORAS:
        present = s not in missing_loras
        _p(f"  {'✓' if present else '✗ FALTA'} {s.dest_name}")
    rep["missing_loras"] = [s.key for s in missing_loras]

    _p("\nPresets de LoRA (matching contra ComfyUI /models/loras):")
    all_loras = await comfy.get_models("loras")
    presets = lp.presets_for_files(all_loras)
    extra = lp.preset_extra_files(all_loras)
    _p(f"  {len(presets)}/{len(lp.HEARMEMAN_PRESETS)} presets detectados: "
       f"{[p['id'] for p in presets]}")
    _p(f"  extra sin preset: {extra}")
    rep["presets_ok"] = len(presets)
    rep["presets_total"] = len(lp.HEARMEMAN_PRESETS)

    return rep


# ── Fase FULL (generación real end-to-end) ────────────────────────────────────
async def phase_full(comfy: ComfyClient, duration: float, turbo: bool,
                     width: int, height: int, prompt: str) -> dict:
    _hr(f"FASE 2 · GENERACIÓN REAL ({duration}s, turbo={turbo}, {width}x{height})")

    if dl.missing_specs(_MODELS_ROOT, dl.GATED_MODELS):
        _p("✗ Faltan modelos gated — corre la fase diag primero.")
        return {"ok": False, "error": "modelos gated incompletos"}

    def name_of(key: str) -> str:
        spec = next(s for s in dl.GATED_MODELS if s.key == key)
        return spec.dest_name

    params = GenParams(
        prompt=prompt,
        unet_name=name_of("dit"),
        clip_name=name_of("encoder"),
        video_vae_name=name_of("video_vae"),
        audio_vae_name=name_of("audio_vae"),
        width=width, height=height, duration_s=duration,
        turbo=turbo,
        turbo_lora_name=name_of("turbo_lora") if turbo else "",
        turbo_steps=8, steps=20, seed=42,
    )
    _p(f"DiT={params.unet_name}")
    _p(f"encoder={params.clip_name}")
    _p(f"prompt={prompt!r}")

    t0 = time.time()
    last_step_print = 0

    def on_phase(name, extra):
        _p(f"   · fase: {name} {extra if extra else ''}")

    def on_step(step, total):
        nonlocal last_step_print
        now = time.time()
        if now - last_step_print > 5 or step == total:
            _p(f"   · sampling {step}/{total}")
            last_step_print = now

    try:
        manifest = await pipeline.generate(comfy, params, on_phase=on_phase, on_step=on_step)
        dt = time.time() - t0
        video_path = pipeline.history_video(manifest["id"])
        size_mb = video_path.stat().st_size / 1e6
        _p(f"\n   ✓ {dt:.1f}s · run {manifest['id']} · {video_path.name} ({size_mb:.1f} MB)")
        _p(f"   duración real: {manifest['duration_s']}s ({manifest['length_frames']} frames)")
        return {"ok": True, "seconds": round(dt, 1), "run_id": manifest["id"],
                "video_file": str(video_path), "size_mb": round(size_mb, 1),
                "duration_s": manifest["duration_s"]}
    except ComfyError as e:
        dt = time.time() - t0
        _p(f"\n   ✗ ComfyError tras {dt:.1f}s: {e}")
        return {"ok": False, "seconds": round(dt, 1), "error": str(e)}
    except Exception as e:
        dt = time.time() - t0
        _p(f"\n   ✗ {type(e).__name__} tras {dt:.1f}s: {e}")
        traceback.print_exc()
        if _LOG_FILE:
            with open(_LOG_FILE, "a", encoding="utf-8") as f:
                traceback.print_exc(file=f)
        return {"ok": False, "seconds": round(dt, 1), "error": f"{type(e).__name__}: {e}"}


# ═══════════════════════════════════════════════════════════════════════════
async def main():
    global _LOG_FILE
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", default="all", choices=["all", "diag", "full"])
    ap.add_argument("--port", type=int, default=8188,
                    help="Puerto de la instancia de ComfyUI a usar (para A/B contra una "
                         "instancia de prueba en otro puerto, p. ej. venv clonado con flags "
                         "distintos, sin tocar la producción)")
    ap.add_argument("--duration", type=float, default=15.0)
    ap.add_argument("--turbo", action="store_true")
    ap.add_argument("--width", type=int, default=1344)
    ap.add_argument("--height", type=int, default=768)
    ap.add_argument("--prompt", default=(
        "Static handheld documentary shot of a calm mountain lake at sunrise, mist "
        "drifting low over the still water, a wooden dock extending into frame. "
        "A light breeze moves the reeds at the shore. Audio: gentle water lapping, "
        "distant birdsong, soft wind. No text, no people, no camera movement."
    ))
    args = ap.parse_args()

    comfy = ComfyClient(port=args.port)
    ts = time.strftime("%Y%m%d-%H%M%S")
    out_dir = _OUT_ROOT / ts
    out_dir.mkdir(parents=True, exist_ok=True)
    _LOG_FILE = out_dir / "battery.log"
    _p(f"Resultados → {out_dir}")

    report = {"timestamp": ts, "phase": args.phase}
    if args.phase in ("all", "diag"):
        report["diag"] = await phase_diag(comfy)
    if args.phase in ("all", "full"):
        report["full"] = await phase_full(comfy, args.duration, args.turbo,
                                          args.width, args.height, args.prompt)

    (out_dir / "results.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    _p(f"\nresults.json → {out_dir / 'results.json'}")
    _p(f"battery.log  → {_LOG_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
