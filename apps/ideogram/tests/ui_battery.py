r"""
Batería UI del módulo Ideogram 4 — simula al USUARIO desde la interfaz.

A diferencia de battery.py (que le pega directo al ComfyClient con nombres de
modelo hardcodeados y por eso nunca reprodujo el bug de la UI), esta batería
golpea los MISMOS endpoints HTTP del hub que dispara el navegador
(hub-webui/static/ideogram.js), y replica la lógica pick() del JS para elegir
los mismos valores por defecto que verían los desplegables. Así reproduce la
ruta real: /status → /models → (pick defaults) → /caption → /generate → poll.

Todo lo que sucede se registra en apps/ideogram/data/ui_battery/<ts>/run.log
(y en stdout). Requiere hub (:9753) + ComfyUI (:8188) + LM Studio arrancados.
NO arranca ni apaga nada.

  hub-webui\.venv\Scripts\python.exe apps\ideogram\tests\ui_battery.py
  ...\python.exe apps\ideogram\tests\ui_battery.py --hub 9753 --desc "un gato"
  ...\python.exe apps\ideogram\tests\ui_battery.py --reproduce-bug   # fuerza CLIP gemma4
"""
import re
import sys
import json
import time
import argparse
import datetime as dt
import urllib.request
import urllib.error
from pathlib import Path

try:  # consola Windows cp1252
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

_HERE = Path(__file__).resolve().parent
_MODULE = _HERE.parent
_OUT_ROOT = _MODULE / "data" / "ui_battery"


# ── Logger dual (stdout + fichero) ───────────────────────────────────────────
class Log:
    def __init__(self, path: Path):
        self.f = open(path, "w", encoding="utf-8")

    def __call__(self, *parts):
        line = " ".join(str(p) for p in parts)
        ts = dt.datetime.now().strftime("%H:%M:%S")
        print(line, flush=True)
        self.f.write(f"[{ts}] {line}\n")
        self.f.flush()

    def block(self, title, obj):
        self("┌─", title)
        txt = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False, indent=2)
        for ln in txt.splitlines():
            self("│ ", ln)
        self("└─")

    def close(self):
        self.f.close()


# ── Cliente HTTP mínimo (stdlib, síncrono) ───────────────────────────────────
class Hub:
    def __init__(self, port: int, log: Log):
        self.base = f"http://127.0.0.1:{port}/api/ideogram"
        self.log = log

    def _req(self, method, path, body=None):
        url = self.base + path
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            payload = e.read().decode()
            try:
                payload = json.loads(payload)
            except Exception:
                pass
            return e.code, payload
        except Exception as e:
            return -1, {"transport_error": str(e)}

    def get(self, path):
        return self._req("GET", path)

    def post(self, path, body):
        return self._req("POST", path, body)


# ── Réplica de la lógica pick()/fillSelect del JS ────────────────────────────
def fill_default(arr):
    """fillSelect deja seleccionado el 1º elemento del desplegable."""
    return arr[0] if arr else ""


def pick(arr, pattern):
    """pick(): si algún item casa la regex, ése gana; si no, se queda el 1º."""
    rx = re.compile(pattern, re.I)
    for v in arr or []:
        if rx.search(v):
            return v
    return fill_default(arr)


def resolve_ui_defaults(models: dict, reproduce_bug: bool = False) -> dict:
    """Reproduce exactamente qué valores tendrían los selectores tras init()."""
    cond = pick(models.get("cond") or [], r"int8_convrot")
    uncond = pick(models.get("uncond") or [], r"unconditional.*nvfp4")
    te = models.get("text_encoders") or []
    # pick del JS para el CLIP: qwen3vl.*fp8
    clip = pick(te, r"qwen3vl.*fp8")
    if reproduce_bug:
        # Estado ANTERIOR al fix: sin pick, el CLIP caía en el 1º del desplegable
        # (el endpoint ordena qwen3vl/gemma4/ideogram alfabéticamente → gemma4).
        clip = fill_default(te)
    vae = pick(models.get("vae") or [], r"flux2")
    llms = [m.get("id") for m in (models.get("llms") or [])]
    llm = fill_default(llms)
    return {"unet_cond": cond, "unet_uncond": uncond, "clip_name": clip,
            "vae_name": vae, "llm_model": llm}


# ── Fases ────────────────────────────────────────────────────────────────────
def phase_status(hub: Hub, log: Log) -> dict:
    log("═" * 60)
    log("FASE 1 · GET /status  (lo que muestra la cabecera de la UI)")
    code, st = hub.get("/status")
    log(f"  HTTP {code}")
    if code != 200:
        log.block("status (error)", st)
        return {}
    log(f"  ComfyUI: {st.get('comfyui')}   LM Studio: {st.get('lmstudio')}   nodes_ok: {st.get('nodes_ok')}")
    bad = [n for n, ok in (st.get("nodes") or {}).items() if not ok]
    if bad:
        log("  ⚠ nodos faltantes:", ", ".join(bad))
    else:
        log("  ✓ los 9 nodos nativos presentes")
    return st


def phase_models(hub: Hub, log: Log) -> dict:
    log("═" * 60)
    log("FASE 2 · GET /models  (lo que puebla los desplegables)")
    code, m = hub.get("/models")
    log(f"  HTTP {code}")
    if code != 200:
        log.block("models (error)", m)
        return {}
    for k in ("cond", "uncond", "text_encoders", "vae"):
        log(f"  {k} ({len(m.get(k) or [])}): {m.get(k)}")
    log(f"  samplers: {m.get('samplers')}")
    log(f"  llms: {[x.get('id') for x in (m.get('llms') or [])]}")
    if m.get("lm_error"):
        log("  ⚠ lm_error:", m["lm_error"])
    return m


def phase_defaults(models: dict, log: Log, reproduce_bug: bool) -> dict:
    log("═" * 60)
    tag = "  (MODO REPRODUCIR BUG: CLIP forzado al 1º)" if reproduce_bug else ""
    log("FASE 3 · Defaults que la UI seleccionaría" + tag)
    d = resolve_ui_defaults(models, reproduce_bug)
    log.block("selección efectiva de los desplegables", d)
    # chequeo de coherencia CLIP: el CLIPLoader usa type:'ideogram4'
    clip = d.get("clip_name", "")
    if "qwen3vl" not in clip.lower():
        log(f"  ⚠ CLIP='{clip}' NO es qwen3vl — puede ser incompatible con type:ideogram4")
    else:
        log(f"  ✓ CLIP='{clip}' es qwen3vl (compatible)")
    return d


def phase_caption(hub: Hub, log: Log, desc: str, defaults: dict, w: int, h: int) -> dict:
    log("═" * 60)
    log("FASE 4 · POST /caption  (descripción → JSON vía LLM)")
    if not defaults.get("llm_model"):
        log("  ⚠ no hay LLM en LM Studio; se omite. El pipeline lo hará internamente.")
        return {}
    body = {"description": desc, "llm_model": defaults["llm_model"],
            "width": w, "height": h}
    log(f"  desc='{desc}'  llm={defaults['llm_model']}")
    t0 = time.time()
    code, r = hub.post("/caption", body)
    log(f"  HTTP {code}   ({time.time()-t0:.1f}s)")
    if code != 200:
        log.block("caption (error)", r)
        return {}
    cap = r.get("caption", {})
    els = (cap.get("compositional_deconstruction") or {}).get("elements") or []
    types = {}
    for e in els:
        types[e.get("type")] = types.get(e.get("type"), 0) + 1
    log(f"  ✓ {len(els)} elementos  tipos={types}")
    log.block("caption JSON", cap)
    return r


def phase_generate(hub: Hub, log: Log, desc: str, defaults: dict,
                   json_prompt: str, w: int, h: int, steps: int) -> dict:
    log("═" * 60)
    log("FASE 5 · POST /generate + poll  (pipeline completo, como el botón Generar)")
    body = {
        "description": desc,
        "json_prompt": json_prompt,      # vacío = como la 1ª prueba del usuario
        "llm_model": defaults.get("llm_model", ""),
        "unet_cond": defaults["unet_cond"],
        "unet_uncond": defaults["unet_uncond"],
        "clip_name": defaults["clip_name"],
        "vae_name": defaults["vae_name"],
        "width": w, "height": h, "steps": steps,
        "cfg": 7.0, "mu": 0.5, "std": 1.75,
        "sampler": "euler", "seed": -1, "manage_vram": True,
    }
    log.block("body enviado a /generate", body)
    code, r = hub.post("/generate", body)
    log(f"  HTTP {code}")
    if code != 200:
        log.block("generate rechazado", r)
        return {"ok": False, "error": r}
    job_id = r.get("job_id")
    log(f"  job_id={job_id}  — polleando cada 0.6s (igual que la UI)…")

    last = None
    t0 = time.time()
    while True:
        time.sleep(0.6)
        code, j = hub.get(f"/jobs/{job_id}")
        if code != 200:
            log.block("jobs (error)", j)
            return {"ok": False, "error": j}
        snap = (j.get("status"), j.get("phase"), j.get("step"), j.get("steps_total"))
        if snap != last:
            log(f"  · status={j.get('status')} phase={j.get('phase')} "
                f"step={j.get('step')}/{j.get('steps_total')}")
            last = snap
        if j.get("status") == "done":
            log(f"  ✓ DONE en {time.time()-t0:.1f}s  run_id={j.get('run_id')}  blocked={j.get('blocked')}")
            return {"ok": True, "run_id": j.get("run_id"), "blocked": j.get("blocked")}
        if j.get("status") == "error":
            log("  ✗ ERROR del pipeline:")
            log.block(">>> MENSAJE DE ERROR (lo que ve el usuario) <<<", j.get("error") or "")
            return {"ok": False, "error": j.get("error")}
        if time.time() - t0 > 480:
            log("  ✗ timeout (>480s) esperando el job")
            return {"ok": False, "error": "timeout"}


# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hub", type=int, default=9753)
    ap.add_argument("--desc", default="un cartel de neón que dice 'HOLA MUNDO' en una calle lluviosa de noche")
    ap.add_argument("--width", type=int, default=1024)
    ap.add_argument("--height", type=int, default=1024)
    ap.add_argument("--steps", type=int, default=20)
    ap.add_argument("--reproduce-bug", action="store_true",
                    help="fuerza el CLIP al 1º del desplegable (gemma4) para reproducir el fallo previo")
    ap.add_argument("--use-caption-json", action="store_true",
                    help="pasa el JSON del caption a /generate (como si el usuario pulsara Generar JSON antes)")
    args = ap.parse_args()

    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = _OUT_ROOT / ts
    out.mkdir(parents=True, exist_ok=True)
    log = Log(out / "run.log")

    log("BATERÍA UI IDEOGRAM 4 — simulando al usuario desde la interfaz")
    log(f"hub=127.0.0.1:{args.hub}  desc='{args.desc}'  {args.width}x{args.height}  steps={args.steps}")
    log(f"reproduce_bug={args.reproduce_bug}  use_caption_json={args.use_caption_json}")
    log(f"log → {out / 'run.log'}")

    hub = Hub(args.hub, log)

    st = phase_status(hub, log)
    if not st or not st.get("comfyui"):
        log("✗ ComfyUI no responde — aborto (arranca ComfyUI en :8188).")
        log.close(); return

    models = phase_models(hub, log)
    if not models:
        log("✗ sin catálogos — aborto.")
        log.close(); return

    defaults = phase_defaults(models, log, args.reproduce_bug)
    if not defaults.get("unet_cond") or not defaults.get("unet_uncond"):
        log("✗ faltan modelos cond/uncond en el catálogo — aborto.")
        log.close(); return

    cap = phase_caption(hub, log, args.desc, defaults, args.width, args.height)

    json_prompt = ""
    if args.use_caption_json and cap.get("caption"):
        json_prompt = json.dumps(cap["caption"], ensure_ascii=False)

    res = phase_generate(hub, log, args.desc, defaults, json_prompt,
                         args.width, args.height, args.steps)

    log("═" * 60)
    log("RESUMEN")
    log(f"  defaults: cond={defaults['unet_cond']}")
    log(f"            uncond={defaults['unet_uncond']}")
    log(f"            clip={defaults['clip_name']}")
    log(f"            vae={defaults['vae_name']}")
    if res.get("ok"):
        log(f"  ✓ generación OK — run_id={res['run_id']} blocked={res['blocked']}")
    else:
        log(f"  ✗ generación FALLÓ — ver el error de arriba")
    log(f"  log completo: {out / 'run.log'}")
    log.close()


if __name__ == "__main__":
    main()
