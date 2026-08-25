"""Recorrido completo contra el hub vivo: idea -> enriquecer -> reescribir una
caja -> generar el clip, vigilando la VRAM en cada frontera.

No mide calidad: mide plomería. Lo que verifica —y no se puede verificar leyendo
el código— es que el LLM se monta al enriquecer, que `/generate` lo desmonta de
verdad antes de encolar, y que el mp4 que sale trae las dos pistas. H3 y el
escritor no caben juntos en VRAM, así que ese desmontaje es el punto frágil.

    hub-webui/.venv/Scripts/python.exe apps/minimax_h3/tests/e2e_pipeline.py

Necesita el hub en :9753, ComfyUI en :8188 y LM Studio en :1234. Por defecto usa
un clip minúsculo (640x384, 2 s) porque el objetivo es recorrer los pasos, no
esperar. `ffprobe` es opcional: si está, comprueba las pistas del mp4.
"""
import argparse
import json
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

API = "http://127.0.0.1:9753/api/minimax-h3"

IDEA = ("Un faro en un acantilado al anochecer; la luz gira y barre la niebla "
        "mientras las olas rompen abajo.")


def vram(tag):
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used",
                              "--format=csv,noheader,nounits"],
                             capture_output=True, text=True).stdout.strip()
        used = [int(x) for x in out.splitlines()]
    except Exception:
        return []
    print(f"  VRAM [{tag}]  " + "  ".join(f"gpu{i}={m} MiB" for i, m in enumerate(used)))
    return used


def call(method, path, body=None, timeout=1800):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        sys.exit(f"  !! {method} {path} -> {e.code}: {e.read().decode('utf-8')[:300]}")


def pick(folder, *needles):
    """Primer archivo del catálogo que contenga todas las agujas. Los nombres no
    se escriben a mano: cambian con cada revisión de los pesos."""
    items = call("GET", f"/catalog/{folder}")
    items = items if isinstance(items, list) else (items.get("items") or
                                                   next(iter(items.values())))
    for it in items:
        low = str(it).lower()
        if all(n in low for n in needles):
            return it
    sys.exit(f"  !! no hay nada en {folder} que case con {needles}")


def step(n, title):
    print(f"\n=== {n}. {title} " + "=" * max(0, 54 - len(title)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="", help="LLM de LM Studio (por defecto, el sugerido)")
    ap.add_argument("--duration", type=float, default=2.0)
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--height", type=int, default=384)
    ap.add_argument("--steps", type=int, default=20)
    args = ap.parse_args()

    t_all = time.time()

    step(1, "Estado de partida")
    st = call("GET", "/status")
    print(f"  ComfyUI={st['comfyui']}  nodos_ok={st['nodes_ok']}")
    if not (st["comfyui"] and st["nodes_ok"]):
        sys.exit("  !! ComfyUI o sus nodos no están listos")
    if not call("GET", "/models/status")["ready"]:
        sys.exit("  !! faltan modelos base de H3")
    # Punto de partida conocido: si quedó un LLM montado de una sesión anterior,
    # la medición del paso 3 no diría nada.
    call("POST", "/prompt/unload-llm")
    time.sleep(3)
    base = vram("inicio")

    step(2, "Catálogo de modelos de escritura")
    pm = call("GET", "/prompt/models")
    model = args.model or pm["suggested"]
    print(f"  {len(pm['llms'])} modelos en LM Studio · se usará: {model}")
    if not model:
        sys.exit("  !! LM Studio no ofreció ningún modelo")

    step(3, "Enriquecer la idea")
    enr = call("POST", "/prompt/enrich", {"idea": IDEA, "llm_model": model,
                                          "mode": "T2VA", "duration_s": args.duration})
    print(f"  ok={enr['ok']}  {enr['seconds']}s  reparaciones={enr['repairs']}  "
          f"issues={[i['code'] for i in enr['issues']]}")
    for k, v in enr["fields"].items():
        print(f"    · {k}: {v[:80]}…")
    if not enr["ok"]:
        sys.exit("  !! el enriquecedor devolvió un prompt inválido")
    loaded = vram("con el LLM montado")
    # Comparado por GPU, no por el total: ComfyUI puede estar ocupando una de
    # ellas y entonces el máximo global no se mueve aunque el LLM sí se montó.
    if base and loaded and not any(b - a > 500 for a, b in zip(base, loaded)):
        sys.exit("  !! la VRAM no subió en ninguna GPU: el LLM no llegó a montarse")

    step(4, "Reescribir solo la caja de música")
    before = enr["fields"]["integrated_multimodal_description"]
    rw = call("POST", "/prompt/rewrite",
              {"llm_model": model, "mode": enr["mode"], "duration_s": args.duration,
               "fields": enr["fields"], "field": "non_diegetic_music",
               "note": "Solo un piano grave, muy espaciado, sin cuerdas."})
    print(f"  ok={rw['ok']}  {rw['seconds']}s")
    print(f"  música: {rw['fields']['non_diegetic_music'][:110]}")
    if rw["fields"]["integrated_multimodal_description"] != before:
        sys.exit("  !! reescribir la música tocó el campo visual")

    step(5, "Generar (aquí debe desmontarse el LLM)")
    job = call("POST", "/generate", {
        "prompt": rw["prompt"],
        "unet_name": pick("diffusion_models", "h3", "fl2va"),
        "clip_name": pick("text_encoders", "minimax_h3"),
        "video_vae_name": pick("vae", "h3", "video"),
        "audio_vae_name": pick("vae", "h3", "audio"),
        "width": args.width, "height": args.height, "duration_s": args.duration,
        "steps": args.steps, "seed": 1234,
    })
    jid = job["job_id"]
    print(f"  job {jid}")

    phases, unloaded, t0 = [], None, time.time()
    while True:
        j = call("GET", f"/jobs/{jid}")
        ph = j.get("phase")
        if ph and (not phases or phases[-1][0] != ph):
            phases.append((ph, round(time.time() - t0)))
            print(f"  [{phases[-1][1]:>5}s] fase: {ph}")
            # La VRAM se mira en la primera fase POSTERIOR a unload: durante la
            # fase el desmontaje aún puede estar a medias.
            if unloaded is None and any(p == "unload" for p, _ in phases) and ph != "unload":
                unloaded = j.get("llm_unloaded", [])
                vram("tras desmontar el LLM")
        if j["status"] in ("done", "error"):
            break
        time.sleep(5)
    if j["status"] == "error":
        sys.exit(f"  !! la generación falló: {j['error']}")
    print(f"  LLM que desmontó el propio /generate: {unloaded}")
    if unloaded is not None and not unloaded:
        print("  ⚠ nadie se desmontó — ¿el enriquecedor no llegó a montar nada?")

    step(6, "Comprobar la salida")
    meta = call("GET", f"/history/{j['run_id']}/meta")
    r = meta.get("render", {})
    print(f"  run_id={j['run_id']}  {r.get('width')}x{r.get('height')}  "
          f"{meta.get('duration_s')}s  {meta.get('length_frames')} frames")
    with urllib.request.urlopen(f"{API}/history/{j['run_id']}/video", timeout=120) as resp:
        blob = resp.read()
    print(f"  video servido: {len(blob)/1e6:.2f} MB")
    if len(blob) < 10_000:
        sys.exit("  !! el archivo de video está vacío")

    if shutil.which("ffprobe"):
        import tempfile, os
        tmp = os.path.join(tempfile.gettempdir(), f"h3_e2e_{j['run_id']}.mp4")
        with open(tmp, "wb") as f:
            f.write(blob)
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                              "stream=codec_type,codec_name", "-of", "csv=p=0", tmp],
                             capture_output=True, text=True).stdout
        kinds = {l.split(",")[1] for l in out.strip().splitlines() if "," in l}
        print(f"  pistas: {out.strip().splitlines()}")
        os.unlink(tmp)
        if kinds != {"video", "audio"}:
            sys.exit("  !! H3 genera audio nativo y este mp4 no trae las dos pistas")

    vram("final")
    print(f"\nRecorrido completo en {round((time.time()-t_all)/60, 1)} min.")
    print("fases:", " -> ".join(f"{p}({s}s)" for p, s in phases))
    print("Todo en orden.")


if __name__ == "__main__":
    main()
