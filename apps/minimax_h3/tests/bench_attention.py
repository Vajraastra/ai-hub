r"""
Banco de aceleración de atención para MiniMax H3 — A/B sobre el MISMO grafo.

Mide el costo por paso de sampling con y sin los parches de atención, contra
una instancia de ComfyUI de pruebas (por defecto :8189, ver BITACORA sección
"Aceleración y hardware de esta máquina"). NO toca producción.

  hub-webui\.venv\Scripts\python.exe apps\minimax_h3\tests\bench_attention.py ^
      --variants baseline sla --duration 5 --steps 6

El A/B es limpio porque el grafo es idéntico en las dos corridas: el nodo
H3SLAAttention se queda siempre cableado y solo cambia su flag `enabled`
(el propio nodo documenta ese flag como "like-for-like speed baseline without
rewiring"). Así ni la topología ni el orden de carga de modelos entran como
variable.

Qué mide: el delta entre eventos `progress` consecutivos del WebSocket, o sea
el tiempo de cada paso de sampling aislado — sin carga de modelos ni decode.
Reporta la mediana (robusta al primer paso, que arrastra la compilación JIT
del kernel) además de media y total.
"""
import sys
import json
import time
import uuid
import asyncio
import argparse
import statistics
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import aiohttp

_HERE = Path(__file__).resolve().parent
_MODULE = _HERE.parent
_APPS = _MODULE.parent
if str(_APPS) not in sys.path:
    sys.path.insert(0, str(_APPS))

from minimax_h3.core.comfy_client import ComfyClient, load_workflow

_OUT = _MODULE / "data" / "bench_attention"

# Config de referencia del módulo (BITACORA › MiniMax H3 › Calidad):
# INT8 ConvRot sin turbo. No se cambia acá: lo que se mide es la atención.
_UNET = "minimax_h3_fl2va_pruned_int8_convrot.safetensors"
_CLIP = "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
_VVAE = "minimax_h3_video_vae_fp16.safetensors"
_AVAE = "minimax_h3_audio_vae_fp32.safetensors"

_PROMPT = ("A woman with dark hair stands in a sunlit kitchen, turns toward "
           "the camera and says warmly: \"The coffee is ready.\" Natural light "
           "from the window, shallow depth of field.")


def _frame_length(seconds: float) -> int:
    """Grid nativo de H3 (17n+5 a 24fps). Copiado de core/pipeline.py."""
    base = max(5, round(seconds * 24))
    return base + (5 - (base % 17)) % 17


# ── Variantes ────────────────────────────────────────────────────────────────
# Cada variante describe qué parches se aplican. El grafo resultante es el mismo
# en todas: solo cambian los valores de los widgets.
# `vram` = los dos parches de KJNodes que trocean para bajar el pico de VRAM.
# Ambos se documentan como matemáticamente exactos ("output is identical to the
# unpatched model"), así que no entran como variable de calidad.
VARIANTS = {
    "baseline":  dict(sla=False, backend="pytorch attention", vram=False),
    "sla":       dict(sla=True,  backend="pytorch attention", vram=False),
    "kitchen":   dict(sla=False, backend="comfy kitchen attention", vram=False),
    "sla+kitchen": dict(sla=True, backend="comfy kitchen attention", vram=False),
    "vram":      dict(sla=False, backend="pytorch attention", vram=True),
    "sla+vram":  dict(sla=True,  backend="pytorch attention", vram=True),
}


def build_graph(cfg: dict, params: dict, sla_opts: dict,
                vram_opts: dict | None = None) -> dict:
    """h3_video.json + la cadena de parches entre UNETLoader y sus consumidores."""
    wf = ComfyClient.build_workflow(load_workflow("h3_video.json"), params)
    vram_opts = vram_opts or {}

    src = "3"                                    # UNETLoader
    wf["900"] = {"class_type": "ModelAttentionBackend",
                 "inputs": {"model": [src, 0], "attention": cfg["backend"]}}
    src = "900"
    if cfg.get("vram"):
        # Orden del workflow de referencia: atención troceada, luego la FFN.
        wf["902"] = {"class_type": "MiniMaxLowVRAMAttention",
                     "inputs": {"model": [src, 0],
                                "head_chunks": vram_opts.get("head_chunks", 2)}}
        wf["903"] = {"class_type": "MiniMaxChunkFeedForward",
                     "inputs": {"model": ["902", 0],
                                "chunks": vram_opts.get("ff_chunks", 2),
                                "seq_threshold": vram_opts.get("ff_seq_threshold", 4096)}}
        src = "903"
    wf["901"] = {"class_type": "H3SLAAttention",
                 "inputs": {"model": [src, 0],
                            "enabled": bool(cfg["sla"]),
                            **sla_opts}}
    src = "901"

    # Reapuntar los consumidores del modelo (BasicScheduler y BasicGuider).
    for nid in ("9", "10"):
        wf[nid]["inputs"]["model"] = [src, 0]
    return wf


# ── Ejecución con cronometraje por paso ──────────────────────────────────────
async def _consume(ws, client, prompt_id, t0, marks, state):
    """Bucle de eventos del WS. Sale al terminar el prompt o al fallar."""
    last_val = -1
    async for msg in ws:
        if msg.type != aiohttp.WSMsgType.TEXT:
            continue
        d = json.loads(msg.data)
        typ, data = d.get("type"), d.get("data") or {}
        if data.get("prompt_id") not in (None, prompt_id):
            continue

        if typ in ("progress", "progress_state"):
            val = data.get("value")
            if val is None and typ == "progress_state":
                nodes = (data.get("nodes") or {}).values()
                vals = [n.get("value") for n in nodes
                        if n.get("state") == "running" and n.get("max", 0) > 1]
                val = max(vals) if vals else None
            if val is None or val == last_val:
                continue
            last_val = val
            now = time.time()
            if state["t_first"] is None:
                state["t_first"] = now
            marks.append(now)
            print(f"      paso {val:>3}  t+{now - t0:7.1f}s", flush=True)

        elif typ == "execution_error":
            state["err"] = data.get("exception_message") or str(data)
            return
        elif typ in ("execution_interrupted", "execution_cached"):
            if typ == "execution_interrupted":
                state["err"] = "interrumpido por ComfyUI"
                return
        elif typ == "executing" and data.get("node") is None:
            return
        elif typ == "execution_success":
            return


async def run_once(port: int, wf: dict, label: str, timeout_s: float = 1800.0
                   ) -> dict:
    client = ComfyClient(port=port)
    client_id = str(uuid.uuid4())
    marks: list[float] = []          # timestamp de cada evento de progreso
    t0 = time.time()
    t_first_step = None
    err = None

    session = aiohttp.ClientSession()
    try:
        ws = await session.ws_connect(f"{client.ws_url}?clientId={client_id}",
                                      heartbeat=30)
        prompt_id, _ = await client.queue_prompt(wf, client_id=client_id)
        print(f"    encolado {prompt_id[:8]}…", flush=True)

        state = {"err": None, "t_first": None}
        try:
            await asyncio.wait_for(
                _consume(ws, client, prompt_id, t0, marks, state), timeout_s)
        except asyncio.TimeoutError:
            state["err"] = (f"timeout de {timeout_s:.0f}s sin terminar "
                            f"(¿ComfyUI colgado o muerto?)")
            await client.interrupt()
        err, t_first_step = state["err"], state["t_first"]

        await ws.close()
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
    finally:
        await session.close()

    total = time.time() - t0
    deltas = [b - a for a, b in zip(marks, marks[1:])]
    return {
        "variant": label,
        "error": err,
        "total_s": round(total, 1),
        "load_s": round((t_first_step - t0), 1) if t_first_step else None,
        "n_steps_timed": len(deltas),
        "step_median_s": round(statistics.median(deltas), 2) if deltas else None,
        "step_mean_s": round(statistics.fmean(deltas), 2) if deltas else None,
        "step_first_s": round(deltas[0], 2) if deltas else None,
        "step_last_s": round(deltas[-1], 2) if deltas else None,
        "deltas_s": [round(d, 2) for d in deltas],
    }


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8189)
    ap.add_argument("--variants", nargs="+", default=["baseline", "sla"],
                    choices=list(VARIANTS))
    ap.add_argument("--duration", type=float, default=5.0)
    ap.add_argument("--steps", type=int, default=6)
    ap.add_argument("--width", type=int, default=1344)
    ap.add_argument("--height", type=int, default=768)
    ap.add_argument("--seed", type=int, default=777000111)
    ap.add_argument("--sparsity", type=float, default=0.90)
    ap.add_argument("--block-size", default="64")
    ap.add_argument("--dense-last-steps", type=int, default=1)
    ap.add_argument("--min-seq-len", type=int, default=8192)
    ap.add_argument("--timeout", type=float, default=1800.0,
                    help="corte por variante, en segundos")
    ap.add_argument("--stabilize-motion", action="store_true",
                    help="SLA: sesga la selección de bloques hacia el paso "
                         "anterior; contra la doble exposición en movimiento")
    ap.add_argument("--head-chunks", type=int, default=2)
    ap.add_argument("--ff-chunks", type=int, default=2)
    ap.add_argument("--ff-seq-threshold", type=int, default=4096)
    args = ap.parse_args()

    comfy = ComfyClient(port=args.port)
    if not await comfy.health_check():
        print(f"✗ ComfyUI no responde en :{args.port}")
        return 1
    for node in ("H3SLAAttention", "ModelAttentionBackend"):
        if not await comfy.probe_node(node):
            print(f"✗ falta el nodo {node} en :{args.port}")
            return 1

    length = _frame_length(args.duration)
    params = {
        "unet_name": _UNET, "clip_name": _CLIP, "clip_device": "cuda:0",
        "video_vae_name": _VVAE, "audio_vae_name": _AVAE,
        "prompt": _PROMPT, "width": args.width, "height": args.height,
        "length": length, "seed": args.seed, "steps": args.steps,
    }
    sla_opts = {
        "sparsity_ratio": args.sparsity,
        "block_size": args.block_size,
        "min_seq_len": args.min_seq_len,
        "dense_last_steps": args.dense_last_steps,
        "protect_audio": True,
        "dense_backend": "comfy_kitchen",
        "disable_fp16_accum": True,
        # Sesga la selección de bloques hacia la del paso anterior. El nodo lo
        # documenta como el arreglo de UN síntoma concreto: doble exposición
        # tenue en movimiento rápido. Cuesta VRAM.
        "stabilize_motion": args.stabilize_motion,
    }
    vram_opts = {
        "head_chunks": args.head_chunks,
        "ff_chunks": args.ff_chunks,
        "ff_seq_threshold": args.ff_seq_threshold,
    }

    print("═" * 70)
    print(f"BANCO DE ATENCIÓN · :{args.port}")
    print(f"  {args.width}×{args.height} · {args.duration}s ({length} frames) · "
          f"{args.steps} pasos · seed {args.seed}")
    print(f"  SLA: sparsity {args.sparsity} · block {args.block_size} · "
          f"dense_last {args.dense_last_steps} · min_seq_len {args.min_seq_len}")
    print("═" * 70)

    results = []
    for v in args.variants:
        cfg = VARIANTS[v]
        print(f"\n▶ {v}  (backend={cfg['backend']}, sla={cfg['sla']})")
        # Descargar modelos entre variantes: que la carga no contamine el reparto.
        await comfy.free_memory()
        wf = build_graph(cfg, params, sla_opts, vram_opts)
        r = await run_once(args.port, wf, v, timeout_s=args.timeout)
        results.append(r)
        if r["error"]:
            # Un OOM deja el contexto CUDA roto: lo que midan las variantes
            # siguientes no vale nada. Cortar acá.
            print(f"    ✗ ERROR: {r['error'][:300]}")
            print("    (se abortan las variantes restantes: el contexto CUDA "
                  "queda comprometido tras un fallo)")
            break
        else:
            print(f"    carga {r['load_s']}s · paso mediana {r['step_median_s']}s "
                  f"· total {r['total_s']}s")

    print("\n" + "═" * 70)
    print(f"{'variante':<14}{'paso mediana':>14}{'paso 1º':>10}{'paso último':>13}"
          f"{'total':>10}")
    print("─" * 70)
    base = next((r for r in results if r["variant"] == "baseline"
                 and r["step_median_s"]), None)
    for r in results:
        if r["error"]:
            print(f"{r['variant']:<14}  ERROR: {r['error'][:45]}")
            continue
        speed = ""
        if base and r is not base:
            speed = f"   ×{base['step_median_s'] / r['step_median_s']:.2f}"
        print(f"{r['variant']:<14}{r['step_median_s']:>14}{r['step_first_s']:>10}"
              f"{r['step_last_s']:>13}{r['total_s']:>10}{speed}")

    _OUT.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out = _OUT / f"bench_{stamp}.json"
    out.write_text(json.dumps(
        {"args": vars(args), "length_frames": length, "results": results},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n→ {out}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
