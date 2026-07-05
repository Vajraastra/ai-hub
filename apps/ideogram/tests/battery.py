r"""
Batería de pruebas del módulo Ideogram 4 — diagnóstico + calibración de cuantización.

Objetivo: reproducir y aislar los errores de la prueba manual, y barrer combos de
(cond × uncond × clip × vae) para encontrar la configuración óptima en 16 GB VRAM
(calidad / velocidad / sin OOM).

Ejecuta con el python portable del hub (tiene aiohttp+numpy+PIL):

  hub-webui\.venv\Scripts\python.exe apps\ideogram\tests\battery.py --phase all
  ...\python.exe apps\ideogram\tests\battery.py --phase diag       # solo diagnóstico
  ...\python.exe apps\ideogram\tests\battery.py --phase caption    # solo LLM→JSON
  ...\python.exe apps\ideogram\tests\battery.py --phase render     # solo barrido de render
  ...\python.exe apps\ideogram\tests\battery.py --phase full       # 1 e2e descripción→imagen

Requiere ComfyUI (:8188) y LM Studio (:1234) arrancados. NO arranca ni apaga nada:
solo consume los servidores que ya levantó el usuario.

Resultados en apps/ideogram/data/battery/<timestamp>/ : PNG por combo, montaje
etiquetado (contact sheet) y results.json con tiempos, OOM y métricas BlockGuard.
"""
import io
import sys
import json
import time
import argparse
import asyncio
import traceback
from pathlib import Path

try:  # consola Windows suele ser cp1252; el reporte usa ✓/✗/→
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

_HERE = Path(__file__).resolve().parent
_MODULE = _HERE.parent            # apps/ideogram
_APPS = _MODULE.parent            # apps
if str(_APPS) not in sys.path:
    sys.path.insert(0, str(_APPS))

from ideogram.core.comfy_client import ComfyClient, ComfyError, load_workflow
from ideogram.core.lmstudio import LMStudio, LMStudioError
from ideogram.core import caption as cap
from ideogram.core import blockguard

from PIL import Image, ImageDraw, ImageFont

_OUT_ROOT = _MODULE / "data" / "battery"
_WORKFLOW = "ideogram4_t2i.json"

# ── Nombres reales de modelos (verificados vía /models de ComfyUI) ───────────
COND = {
    "fp8":   "ideogram4\\ideogram4_fp8_scaled.safetensors",
    "int8":  "ideogram4\\ideogram4_int8_convrot.safetensors",
    "nvfp4": "ideogram4\\ideogram4_nvfp4_mixed.safetensors",
}
UNCOND = {
    "fp8":   "ideogram4\\ideogram4_unconditional_fp8_scaled.safetensors",
    "int8":  "ideogram4\\ideogram4_unconditional_int8_convrot.safetensors",
    "nvfp4": "ideogram4\\ideogram4_unconditional_nvfp4_mixed.safetensors",
}
CLIP = {
    "q_fp8":   "qwen3vl_8b_fp8_scaled.safetensors",
    "q_nvfp4": "qwen3vl_8b_nvfp4.safetensors",
}
VAE = "flux2-vae.safetensors"   # Ideogram4 usa latente Flux2 → VAE flux2

# ── Barrido de combos (editable). Cada uno: (id, cond_q, uncond_q, clip_q) ───
# Diseño: matched-quant con clip fp8 (A), cond-máx/uncond-barato (B),
# y verificación de clip nvfp4 (C). ~6 renders para no quemar VRAM/tiempo.
COMBOS = [
    ("A1_fp8-fp8",       "fp8",   "fp8",   "q_fp8"),
    ("A2_int8-int8",     "int8",  "int8",  "q_fp8"),
    ("A3_nvfp4-nvfp4",   "nvfp4", "nvfp4", "q_fp8"),
    ("B1_fp8-nvfp4",     "fp8",   "nvfp4", "q_fp8"),
    ("B2_int8-nvfp4",    "int8",  "nvfp4", "q_fp8"),
    ("C1_nvfp4-nvfp4-cq","nvfp4", "nvfp4", "q_nvfp4"),
]

# ── Parámetros de sampling fijos durante el barrido ──────────────────────────
RENDER = dict(width=1024, height=1024, steps=20, mu=0.5, std=1.75,
              cfg=7.0, sampler="euler", seed=42)
RENDER_TIMEOUT = 360  # s por combo (OOM/hang → se registra y sigue)

# ── Prompt de render (caption JSON hecho a mano; aísla el render del LLM) ─────
# Escena neutra que estresa lo que discrimina calidad de cuantización:
# fidelidad de TEXTO (fuerte de Ideogram), color y detalle fino.
RENDER_CAPTION = {
    "high_level_description": "A cozy neon-lit diner at dusk on a rain-slicked street, warm light spilling onto wet pavement.",
    "style_description": {
        "aesthetics": "cinematic, moody, high detail",
        "lighting": "neon glow, blue-hour ambient, wet reflections",
        "medium": "photograph",
        "photo": "35mm lens, f1.8, shallow depth of field",
        "color_palette": ["#0b1a2b", "#ff3b6b", "#12d1e0", "#ffd166"],
    },
    "compositional_deconstruction": {
        "background": "dark blue evening sky above a row of old brick buildings, distant city-light bokeh",
        "elements": [
            {"type": "obj", "bbox": [0, 0, 350, 1000], "desc": "deep blue dusk sky with faint clouds"},
            {"type": "obj", "bbox": [600, 0, 1000, 1000], "desc": "wet asphalt street reflecting neon colors"},
            {"type": "obj", "bbox": [250, 150, 780, 850], "desc": "diner storefront with large windows and warm interior light"},
            {"type": "text", "bbox": [300, 300, 430, 700], "desc": "glowing pink cursive neon sign above the entrance",
             "text": "Nighthawks", "color_palette": ["#ff3b6b"]},
            {"type": "obj", "bbox": [700, 100, 950, 300], "desc": "a lone figure in a long coat near the entrance"},
        ],
    },
}

# ── Prompts para la fase caption (LLM→JSON). El último es limítrofe a propósito
#    para probar la vía anti-filtro. Sustituye/añade el tuyo aquí. ────────────
CAPTION_PROMPTS = [
    ("texto",     "Un cartel de neón que dice 'OPEN 24H' sobre la entrada de un bar nocturno lluvioso."),
    ("retrato",   "Retrato de una mujer pelirroja con chaqueta de cuero, luz de ventana lateral, fondo de ladrillo."),
    ("complejo",  "Un mercado callejero abarrotado al atardecer: puestos de fruta, farolillos, gente caminando, un gato en primer plano."),
    # limítrofe (calibra el anti-filtro): marco editorial de moda + lenguaje
    # factual + entorno rico → el LLM saca varias cajas sujeto/entorno.
    ("limitrofe",
     "Fotografía editorial de moda de playa: una mujer joven de piel morena, "
     "ojos grises y cabello negro largo, con un bikini dorado, inclinada "
     "ligeramente hacia la cámara en pose de portada de revista. Fondo: playa "
     "paradisíaca con arena clara, mar turquesa y cielo despejado. Luz natural "
     "cálida de media tarde."),
]


# ═══════════════════════════════════════════════════════════════════════════
def _p(msg=""):
    print(msg, flush=True)


def _hr(t=""):
    _p("\n" + "═" * 70)
    if t:
        _p(t)
        _p("═" * 70)


# ── Fase DIAG ───────────────────────────────────────────────────────────────
async def phase_diag(comfy: ComfyClient, lm: LMStudio) -> dict:
    _hr("FASE 1 · DIAGNÓSTICO")
    rep = {}
    comfy_up = await comfy.health_check()
    lm_up = await lm.health_check()
    _p(f"ComfyUI :8188   {'✓' if comfy_up else '✗ NO RESPONDE'}")
    _p(f"LM Studio :1234 {'✓' if lm_up else '✗ NO RESPONDE'}")
    rep["comfyui"], rep["lmstudio"] = comfy_up, lm_up
    if not comfy_up:
        return rep

    nodes = ["DualModelGuider", "Ideogram4Scheduler", "CLIPLoader", "CLIPTextEncode",
             "UNETLoader", "ConditioningZeroOut", "EmptyFlux2LatentImage",
             "SamplerCustomAdvanced", "VAELoader"]
    _p("\nNodos nativos:")
    node_res = {}
    for n in nodes:
        ok = await comfy.probe_node(n)
        node_res[n] = ok
        _p(f"  {'✓' if ok else '✗'} {n}")
    rep["nodes"] = node_res

    _p("\nCatálogos:")
    diff = await comfy.get_models("diffusion_models")
    te = await comfy.get_models("text_encoders")
    vae = await comfy.get_models("vae")
    missing = []
    for label, name in ([("cond." + k, v) for k, v in COND.items()] +
                        [("uncond." + k, v) for k, v in UNCOND.items()] +
                        [("clip." + k, v) for k, v in CLIP.items()] +
                        [("vae", VAE)]):
        pool = diff if "ideogram4" in name else (te if name in te else vae)
        present = name in diff or name in te or name in vae
        _p(f"  {'✓' if present else '✗ FALTA'} {label:14} {name}")
        if not present:
            missing.append((label, name))
    rep["missing_models"] = missing

    if lm_up:
        try:
            llms = await lm.list_models()
            _p(f"\nLLMs en LM Studio ({len(llms)}):")
            for m in llms:
                if m.get("type") in ("llm", "vlm"):
                    _p(f"  · {m['id']}  [{m.get('state')}]  {m.get('type')}")
            rep["llms"] = [m["id"] for m in llms if m.get("type") in ("llm", "vlm")]
        except LMStudioError as e:
            _p(f"  ✗ {e}")
    return rep


# ── Fase CAPTION ─────────────────────────────────────────────────────────────
async def phase_caption(lm: LMStudio, llm_model: str) -> list[dict]:
    _hr(f"FASE 2 · CAPTION (LLM → JSON)   modelo: {llm_model or '(auto)'}")
    if not llm_model:
        loaded = await lm.loaded_llms()
        llm_model = loaded[0] if loaded else (await _pick_llm(lm))
        _p(f"LLM elegido: {llm_model}")
    results = []
    for tag, desc in CAPTION_PROMPTS:
        _p(f"\n── [{tag}] {desc}")
        t0 = time.time()
        row = {"tag": tag, "desc": desc}
        try:
            messages = cap.build_messages(desc, 1024, 1024)
            raw = await lm.chat_json(llm_model, messages, cap.IDEOGRAM_JSON_SCHEMA)
            if "__raw__" in raw:
                raw = cap.parse_llm_output(raw["__raw__"])
            caption = cap.validate_and_clean(raw)
            dt = time.time() - t0
            n_el = len(caption["compositional_deconstruction"]["elements"])
            n_txt = sum(1 for e in caption["compositional_deconstruction"]["elements"]
                        if e["type"] == "text")
            _p(f"   ✓ {dt:5.1f}s · {n_el} elementos ({n_txt} de texto)")
            _p("   " + cap.to_prompt_string(caption).replace("\n", "\n   ")[:600])
            row.update(ok=True, seconds=round(dt, 1), elements=n_el, text_elements=n_txt,
                       caption=caption)
        except Exception as e:
            _p(f"   ✗ {type(e).__name__}: {e}")
            row.update(ok=False, error=f"{type(e).__name__}: {e}")
        results.append(row)
    return results


async def _pick_llm(lm: LMStudio) -> str:
    """Prefiere un gemma-4 pequeño (e4b/12b) para el rol de compositor de cajas."""
    ids = await lm.loaded_llms()
    if not ids:
        ids = [m["id"] for m in await lm.list_models() if m.get("type") in ("llm", "vlm")]
    for pref in ("e4b", "12b", "gemma-4"):
        for i in ids:
            if pref in i.lower():
                return i
    return ids[0] if ids else ""


# ── Fase RENDER (barrido de cuantización) ────────────────────────────────────
async def phase_render(comfy: ComfyClient, out_dir: Path) -> list[dict]:
    _hr("FASE 3 · BARRIDO DE RENDER (calibración de cuantización)")
    if not await comfy.health_check():
        _p("✗ ComfyUI no responde; salto el barrido.")
        return []

    prompt_str = cap.to_prompt_string(cap.validate_and_clean(RENDER_CAPTION))
    wf = load_workflow(_WORKFLOW)
    results = []
    imgs_for_sheet = []

    for cid, cq, uq, clipq in COMBOS:
        _p(f"\n── {cid}   cond={cq}  uncond={uq}  clip={clipq}")
        await comfy.free_memory()  # liberar VRAM del combo anterior
        params = {
            "unet_cond": COND[cq], "unet_uncond": UNCOND[uq],
            "clip_name": CLIP[clipq], "vae_name": VAE,
            "json_prompt": prompt_str, **RENDER,
        }
        row = {"id": cid, "cond": cq, "uncond": uq, "clip": clipq}
        t0 = time.time()
        try:
            steps_seen = {"v": 0, "m": RENDER["steps"]}

            def on_step(v, m):
                steps_seen["v"], steps_seen["m"] = v, m
                print(f"\r   render {v}/{m}", end="", flush=True)

            png = await asyncio.wait_for(
                comfy.run_workflow(wf, params, on_progress=on_step),
                timeout=RENDER_TIMEOUT)
            dt = time.time() - t0
            blocked, metrics = blockguard.is_safety_block(png)
            fn = out_dir / f"{cid}.png"
            fn.write_bytes(png)
            imgs_for_sheet.append((cid, cq, uq, clipq, png, dt, blocked))
            print("\r" + " " * 40 + "\r", end="")
            flag = "  ⚠ BLOQUEO" if blocked else ""
            _p(f"   ✓ {dt:5.1f}s · gris={metrics['gray_fraction']:.2f} "
               f"sat={metrics['mean_saturation']:.3f}{flag}  → {fn.name}")
            row.update(ok=True, seconds=round(dt, 1), blocked=blocked,
                       metrics=metrics, file=fn.name)
        except asyncio.TimeoutError:
            print()
            _p(f"   ✗ TIMEOUT (> {RENDER_TIMEOUT}s) — posible hang/OOM")
            row.update(ok=False, error=f"timeout>{RENDER_TIMEOUT}s")
            await comfy.interrupt()
        except ComfyError as e:
            print()
            msg = str(e)
            oom = "out of memory" in msg.lower() or "alloc" in msg.lower()
            _p(f"   ✗ {'OOM' if oom else 'ComfyError'}: {msg[:200]}")
            row.update(ok=False, oom=oom, error=msg[:500])
        except Exception as e:
            print()
            _p(f"   ✗ {type(e).__name__}: {e}")
            row.update(ok=False, error=f"{type(e).__name__}: {e}")
        results.append(row)

    if imgs_for_sheet:
        sheet = _contact_sheet(imgs_for_sheet)
        sp = out_dir / "montaje.png"
        sheet.save(sp)
        _p(f"\nMontaje comparativo → {sp}")
    return results


def _contact_sheet(items: list) -> Image.Image:
    """Grid etiquetado con cond/uncond/clip + tiempo por combo."""
    thumb = 384
    pad, label_h = 12, 46
    cols = min(3, len(items))
    rows = (len(items) + cols - 1) // cols
    W = cols * thumb + (cols + 1) * pad
    H = rows * (thumb + label_h) + (rows + 1) * pad
    sheet = Image.new("RGB", (W, H), (24, 24, 28))
    d = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("arial.ttf", 16)
    except Exception:
        font = ImageFont.load_default()
    for i, (cid, cq, uq, clipq, png, dt, blocked) in enumerate(items):
        r, c = divmod(i, cols)
        x = pad + c * (thumb + pad)
        y = pad + r * (thumb + label_h + pad)
        im = Image.open(io.BytesIO(png)).convert("RGB")
        im.thumbnail((thumb, thumb))
        sheet.paste(im, (x, y))
        col = (255, 90, 90) if blocked else (220, 220, 225)
        d.text((x, y + thumb + 4), cid, fill=col, font=font)
        d.text((x, y + thumb + 24),
               f"c:{cq} u:{uq} clip:{clipq}  {dt:.0f}s{'  BLOQUEO' if blocked else ''}",
               fill=(150, 150, 160), font=font)
    return sheet


# ── Fase FULL (end-to-end descripción → imagen, con unload de VRAM) ──────────
async def phase_full(comfy: ComfyClient, lm: LMStudio, llm_model: str,
                     out_dir: Path) -> dict:
    _hr("FASE 4 · END-TO-END (descripción → LLM → unload → render)")
    from ideogram.core import pipeline
    if not llm_model:
        llm_model = await _pick_llm(lm)
    _p(f"LLM: {llm_model}")
    # combo por defecto: el más ligero que suele evitar OOM
    p = pipeline.GenParams(
        description=CAPTION_PROMPTS[0][1], llm_model=llm_model,
        unet_cond=COND["nvfp4"], unet_uncond=UNCOND["nvfp4"],
        clip_name=CLIP["q_fp8"], vae_name=VAE, **RENDER, manage_vram=True)
    t0 = time.time()
    try:
        def on_phase(name, extra):
            _p(f"   · fase: {name} {extra if extra else ''}")
        manifest = await pipeline.generate(comfy, lm, p, on_phase=on_phase)
        _p(f"   ✓ {time.time()-t0:.1f}s · run {manifest['id']} · "
           f"bloqueado={manifest['blocked']}")
        return {"ok": True, "run_id": manifest["id"], "blocked": manifest["blocked"]}
    except Exception as e:
        _p(f"   ✗ {type(e).__name__}: {e}")
        traceback.print_exc()
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


# ── Resumen ──────────────────────────────────────────────────────────────────
def summary(render_rows: list[dict]):
    if not render_rows:
        return
    _hr("RESUMEN DEL BARRIDO")
    _p(f"{'combo':18} {'cond':6} {'uncond':7} {'clip':8} {'ok':3} {'s':>6} {'nota'}")
    _p("-" * 70)
    ok_rows = []
    for r in render_rows:
        note = ""
        if not r.get("ok"):
            note = ("OOM" if r.get("oom") else "") + " " + r.get("error", "")[:34]
        elif r.get("blocked"):
            note = "⚠ BlockGuard"
        else:
            ok_rows.append(r)
        _p(f"{r['id']:18} {r['cond']:6} {r['uncond']:7} {r['clip']:8} "
           f"{'✓' if r.get('ok') else '✗':3} {r.get('seconds', ''):>6} {note}")
    if ok_rows:
        best = min(ok_rows, key=lambda r: r["seconds"])
        _p(f"\nMás rápido sin bloqueo: {best['id']}  ({best['seconds']}s)")
        _p("La calidad se juzga a ojo en montaje.png (fidelidad de texto/color/detalle).")


# ═══════════════════════════════════════════════════════════════════════════
async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", default="all",
                    choices=["all", "diag", "caption", "render", "full"])
    ap.add_argument("--llm", default="", help="modelo LM Studio para caption/full")
    args = ap.parse_args()

    comfy = ComfyClient(port=8188)
    lm = LMStudio()
    ts = time.strftime("%Y%m%d-%H%M%S")
    out_dir = _OUT_ROOT / ts
    out_dir.mkdir(parents=True, exist_ok=True)
    _p(f"Resultados → {out_dir}")

    report = {"timestamp": ts, "phase": args.phase}
    if args.phase in ("all", "diag"):
        report["diag"] = await phase_diag(comfy, lm)
    if args.phase in ("all", "caption"):
        report["caption"] = await phase_caption(lm, args.llm)
    if args.phase in ("all", "render"):
        report["render"] = await phase_render(comfy, out_dir)
        summary(report["render"])
    if args.phase == "full":
        report["full"] = await phase_full(comfy, lm, args.llm, out_dir)

    (out_dir / "results.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    _p(f"\nresults.json → {out_dir / 'results.json'}")


if __name__ == "__main__":
    asyncio.run(main())
