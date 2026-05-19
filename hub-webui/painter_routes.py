"""
Painter API — todas las rutas FastAPI del módulo.
"""
import sys
import json
import uuid
import base64
import io
import asyncio
import datetime
from pathlib import Path
from typing import AsyncGenerator

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import StreamingResponse, Response

_HERE = Path(__file__).parent
_ROOT = _HERE.parent
_CORE = _ROOT / "apps" / "painter" / "core"

for _p in [str(_ROOT), str(_CORE)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from setup       import run_setup, get_status, quick_check
from comfy_client import ComfyClient, ComfyError, load_workflow, SUPPORTED_ARCHITECTURES
from models      import (GenerateRequest, InpaintRequest, OutpaintRequest,
                         UpscaleRequest,
                         RegionalRequest, RegionalStepRequest, AcceptRequest,
                         JobResponse, JobStatusResponse, SessionStateResponse, ModelsResponse)
from session     import session
from image_utils import validate_resolution, resolve_seed, bytes_to_b64

painter_router = APIRouter(prefix="/api/painter", tags=["painter"])

_comfy = ComfyClient(port=8188)

# ── Job manager ────────────────────────────────────────────────────────────
#
# Un solo job activo a la vez (limitación de ComfyUI en modo single-user).
# _jobs: {job_id: {status, progress, total, result_bytes, error}}
# _queues: {job_id: asyncio.Queue}  — canal hacia los WebSocket de progreso

_jobs:   dict[str, dict]            = {}
_queues: dict[str, asyncio.Queue]   = {}
_active_job: str | None             = None
_loras_cache: list[str]             = []   # poblado en GET /models al iniciar Painter


def _new_job(job_id: str, job_type: str = "image"):
    _jobs[job_id]   = {"status": "queued", "progress": 0, "total": 0,
                       "result_bytes": None, "error": None, "type": job_type}
    _queues[job_id] = asyncio.Queue()


async def _run_job(job_id: str, workflow: dict, params: dict):
    global _active_job
    q = _queues[job_id]
    _jobs[job_id]["status"] = "running"
    await q.put({"type": "queued"})

    def on_progress(step: int, total: int):
        _jobs[job_id]["progress"] = step
        _jobs[job_id]["total"]    = total
        asyncio.get_event_loop().call_soon_threadsafe(
            q.put_nowait, {"type": "progress", "step": step, "total": total}
        )

    try:
        result = await _comfy.run_workflow(workflow, params, on_progress)
        _jobs[job_id]["status"]       = "done"
        _jobs[job_id]["result_bytes"] = result
        session.set_preview(result)
        await q.put({"type": "done"})
    except ComfyError as e:
        _jobs[job_id]["status"] = "error"
        _jobs[job_id]["error"]  = str(e)
        await q.put({"type": "error", "msg": str(e)})
    finally:
        _active_job = None


async def _launch(workflow: dict, params: dict, job_type: str = "image") -> str:
    global _active_job
    if _active_job:
        raise HTTPException(409, "Una generación está en curso")
    job_id = str(uuid.uuid4())
    _new_job(job_id, job_type)
    _active_job = job_id
    asyncio.create_task(_run_job(job_id, workflow, params))
    return job_id


# ── Setup ──────────────────────────────────────────────────────────────────

@painter_router.get("/setup/status")
async def setup_status():
    status = get_status()
    if status.get("ready"):
        alive = await quick_check()
        if not alive:
            return {**status, "ready": False, "comfyui_offline": True,
                    "msg": "ComfyUI no responde — inícialo desde el hub"}
    return status


@painter_router.get("/setup/run")
async def setup_run():
    """SSE — progreso del setup en tiempo real."""
    async def stream() -> AsyncGenerator[str, None]:
        async for ev in run_setup():
            yield f"data: {json.dumps(ev)}\n\n"
        yield 'data: {"step":"stream_end","status":"ok","msg":""}\n\n'

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


# ── Modelos disponibles ────────────────────────────────────────────────────

@painter_router.get("/models", response_model=ModelsResponse)
async def get_models():
    global _loras_cache
    checkpoints, controlnet, upscalers, loras, samplers, schedulers = await asyncio.gather(
        _comfy.get_models("checkpoints"),
        _comfy.get_models("controlnet"),
        _comfy.get_models("upscale_models"),
        _comfy.get_models("loras"),
        _comfy.get_samplers(),
        _comfy.get_schedulers(),
    )
    if loras:
        _loras_cache = loras
    return ModelsResponse(
        checkpoints=checkpoints,
        controlnet=controlnet,
        upscale_models=upscalers,
        loras=loras,
        samplers=samplers,
        schedulers=schedulers,
        architectures=SUPPORTED_ARCHITECTURES,
    )


def _apply_prompt_loras(wf: dict, prompt: str) -> tuple[dict, str]:
    """Extrae tokens <lora:...> del prompt, los resuelve e inyecta en el workflow."""
    from image_utils import parse_lora_tokens, resolve_lora_name
    cleaned, tokens = parse_lora_tokens(prompt)
    if not tokens:
        return wf, prompt
    loras = []
    for t in tokens:
        name = resolve_lora_name(t["name"], _loras_cache)
        if name:
            loras.append({"name": name, "strength": t["strength"]})
    if loras:
        wf = ComfyClient.inject_loras(wf, loras)
    return wf, cleaned


# ── Generación ─────────────────────────────────────────────────────────────

@painter_router.post("/generate", response_model=JobResponse)
async def generate(req: GenerateRequest):
    validate_resolution(req.width, req.height)
    wf, prompt_clean = _apply_prompt_loras(load_workflow("txt2img.json", req.arch), req.prompt)
    params = {
        "checkpoint":      req.checkpoint,
        "prompt":          prompt_clean,
        "negative_prompt": req.negative_prompt,
        "width":           req.width,
        "height":          req.height,
        "seed":            resolve_seed(req.seed),
        "steps":           req.steps,
        "cfg":             req.cfg,
        "sampler":         req.sampler,
        "scheduler":       req.scheduler,
    }
    job_id = await _launch(wf, params)
    return JobResponse(job_id=job_id)


@painter_router.post("/inpaint", response_model=JobResponse)
async def inpaint(req: InpaintRequest):
    # Elegir workflow mejorado o básico según disponibilidad del nodo
    use_enhanced = (req.inpaint_model is not None and
                    await _comfy.probe_node("INPAINT_InpaintWithModel"))
    wf_name = "inpaint.json" if use_enhanced else "inpaint_basic.json"
    wf, prompt_clean = _apply_prompt_loras(load_workflow(wf_name, req.arch), req.prompt)
    params  = {
        "checkpoint":      req.checkpoint,
        "prompt":          prompt_clean,
        "negative_prompt": req.negative_prompt,
        "image_b64":       req.image_b64,
        "mask_b64":        req.mask_b64,
        "seed":            resolve_seed(req.seed),
        "steps":           req.steps,
        "cfg":             req.cfg,
        "sampler":         req.sampler,
        "scheduler":       req.scheduler,
        "denoise":         req.denoise,
        "feather_radius":  req.feather_radius,
    }
    if use_enhanced:
        params["inpaint_model"] = req.inpaint_model
    job_id = await _launch(wf, params)
    return JobResponse(job_id=job_id)


@painter_router.post("/outpaint", response_model=JobResponse)
async def outpaint(req: OutpaintRequest):
    wf, prompt_clean = _apply_prompt_loras(load_workflow("outpaint.json", req.arch), req.prompt)
    params = {
        "checkpoint":      req.checkpoint,
        "prompt":          prompt_clean,
        "negative_prompt": req.negative_prompt,
        "image_b64":       req.image_b64,
        "pad_left":        req.pad_left,
        "pad_right":       req.pad_right,
        "pad_top":         req.pad_top,
        "pad_bottom":      req.pad_bottom,
        "feathering":      req.feathering,
        "seed":            resolve_seed(req.seed),
        "steps":           req.steps,
        "cfg":             req.cfg,
        "sampler":         req.sampler,
        "scheduler":       req.scheduler,
        "denoise":         req.denoise,
    }
    job_id = await _launch(wf, params)
    return JobResponse(job_id=job_id)


@painter_router.post("/regional", response_model=JobResponse)
async def regional(req: RegionalRequest):
    if not req.regions:
        raise HTTPException(400, "Se requiere al menos una región")
    if len(req.regions) > 4:
        raise HTTPException(400, "Máximo 4 regiones")

    from PIL import Image, ImageFilter  # PIL disponible vía Pillow en el venv del hub

    total_pixels = req.width * req.height

    # Procesar cada máscara: calcular área → strength, aplicar gaussian blur
    processed: list[dict] = []
    for reg in req.regions:
        raw_bytes = base64.b64decode(reg.mask_b64)
        img = Image.open(io.BytesIO(raw_bytes)).convert("L")

        # Calcular área antes del blur
        pixels   = list(img.getdata())
        area     = sum(1 for p in pixels if p > 128) / max(total_pixels, 1)
        strength = max(0.6, min(1.0, 1.0 - area * 0.4))

        # Gaussian blur en bordes para reducir bleeding
        blurred = img.filter(ImageFilter.GaussianBlur(radius=15))
        buf     = io.BytesIO()
        blurred.save(buf, format="PNG")
        processed_b64 = base64.b64encode(buf.getvalue()).decode()

        processed.append({
            "prompt":   reg.prompt,
            "mask_b64": processed_b64,
            "strength": strength,
        })

    wf = ComfyClient.build_regional_workflow(
        checkpoint      = req.checkpoint,
        global_prompt   = req.prompt,
        negative_prompt = req.negative_prompt,
        regions         = processed,
        width           = req.width,
        height          = req.height,
        seed            = resolve_seed(req.seed),
        steps           = req.steps,
        cfg             = req.cfg,
        denoise         = req.denoise,
        image_b64       = req.image_b64,
        scheduler       = req.scheduler,
    )
    job_id = await _launch(wf, {})   # workflow ya construido, params vacíos
    return JobResponse(job_id=job_id)


@painter_router.post("/regional_step", response_model=JobResponse)
async def regional_step(req: RegionalStepRequest):
    """
    Genera una sola región sobre la imagen acumulada.
    Usa SetLatentNoiseMask para que solo los píxeles dentro de la máscara cambien.
    """
    from PIL import Image, ImageFilter

    # Procesar máscara: blur para suavizar bordes
    raw_bytes = base64.b64decode(req.mask_b64)
    img       = Image.open(io.BytesIO(raw_bytes)).convert("L")
    blurred   = img.filter(ImageFilter.GaussianBlur(radius=15))
    buf       = io.BytesIO()
    blurred.save(buf, format="PNG")
    mask_processed = base64.b64encode(buf.getvalue()).decode()

    wf = ComfyClient.build_regional_step_workflow(
        checkpoint      = req.checkpoint,
        prompt          = req.prompt,
        negative_prompt = req.negative_prompt,
        mask_b64        = mask_processed,
        image_b64       = req.image_b64,
        seed            = resolve_seed(req.seed),
        steps           = req.steps,
        cfg             = req.cfg,
        denoise         = req.denoise,
        width           = req.width,
        height          = req.height,
        scheduler       = req.scheduler,
    )
    job_id = await _launch(wf, {})
    return JobResponse(job_id=job_id)


@painter_router.post("/upscale", response_model=JobResponse)
async def upscale(req: UpscaleRequest):
    wf     = load_workflow("upscale.json", req.arch)
    params = {"image_b64": req.image_b64, "model_name": req.model_name}
    job_id = await _launch(wf, params)
    return JobResponse(job_id=job_id)


# ── Estado y resultado de jobs ─────────────────────────────────────────────

@painter_router.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def job_status(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job no encontrado")
    return JobStatusResponse(
        job_id=job_id,
        status=job["status"],
        progress=job["progress"],
        total=job["total"],
        error=job.get("error"),
    )


@painter_router.get("/jobs/{job_id}/result")
async def job_result(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job no encontrado")
    if job["status"] != "done":
        raise HTTPException(400, f"Job aún no terminado: {job['status']}")
    headers = {"X-Job-Type": job.get("type", "image")}
    return Response(content=job["result_bytes"], media_type="image/png", headers=headers)


@painter_router.post("/interrupt")
async def interrupt_job():
    await _comfy.interrupt()
    return {"ok": True}


# ── Progreso en tiempo real (WebSocket) ────────────────────────────────────

@painter_router.websocket("/progress/{job_id}")
async def progress_ws(ws: WebSocket, job_id: str):
    await ws.accept()
    job = _jobs.get(job_id)
    if not job:
        await ws.send_json({"type": "error", "msg": "Job no encontrado"})
        await ws.close()
        return

    # Si ya terminó antes de que el WS se conectara, enviar estado final
    if job["status"] in ("done", "error"):
        if job["status"] == "done":
            await ws.send_json({"type": "done"})
        else:
            await ws.send_json({"type": "error", "msg": job.get("error", "")})
        await ws.close()
        return

    q = _queues.get(job_id)
    if not q:
        await ws.close()
        return

    try:
        while True:
            event = await asyncio.wait_for(q.get(), timeout=120)
            await ws.send_json(event)
            if event["type"] in ("done", "error"):
                break
    except (asyncio.TimeoutError, WebSocketDisconnect):
        pass
    finally:
        try:
            await ws.close()
        except Exception:
            pass


# ── Sesión ─────────────────────────────────────────────────────────────────

@painter_router.get("/session", response_model=SessionStateResponse)
async def session_state():
    return SessionStateResponse(**session.state_dict())


@painter_router.post("/session/accept")
async def session_accept():
    try:
        result = session.accept()
        return {"ok": True, "image_b64": bytes_to_b64(result), **session.state_dict()}
    except ValueError as e:
        raise HTTPException(400, str(e))


@painter_router.post("/session/reject")
async def session_reject():
    session.reject()
    return {"ok": True, **session.state_dict()}


@painter_router.post("/session/undo")
async def session_undo():
    try:
        result = session.undo()
        return {"ok": True, "image_b64": bytes_to_b64(result), **session.state_dict()}
    except ValueError as e:
        raise HTTPException(400, str(e))


@painter_router.post("/session/redo")
async def session_redo():
    try:
        result = session.redo()
        return {"ok": True, "image_b64": bytes_to_b64(result), **session.state_dict()}
    except ValueError as e:
        raise HTTPException(400, str(e))


@painter_router.post("/session/clear")
async def session_clear():
    session.clear()
    return {"ok": True}


@painter_router.get("/session/current")
async def session_current():
    if not session.has_current:
        raise HTTPException(404, "No hay imagen actual en la sesión")
    return Response(content=session.current, media_type="image/png")


# ── Estilos (quality prompts guardados) ───────────────────────────────────

_STYLES_FILE = _HERE / "painter_styles.json"

def _load_styles() -> list[dict]:
    if _STYLES_FILE.exists():
        try:
            return json.loads(_STYLES_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []

def _save_styles(styles: list[dict]) -> None:
    _STYLES_FILE.write_text(json.dumps(styles, ensure_ascii=False, indent=2), encoding="utf-8")


@painter_router.get("/styles")
async def get_styles():
    return {"styles": _load_styles()}


@painter_router.post("/styles")
async def save_style(body: dict):
    name   = (body.get("name") or "").strip()
    prompt = (body.get("prompt") or "").strip()
    if not name:
        raise HTTPException(400, "El nombre no puede estar vacío")
    styles = _load_styles()
    # Actualizar si ya existe, agregar si no
    for s in styles:
        if s["name"] == name:
            s["prompt"] = prompt
            _save_styles(styles)
            return {"ok": True}
    styles.append({"name": name, "prompt": prompt})
    _save_styles(styles)
    return {"ok": True}


@painter_router.delete("/styles/{name}")
async def delete_style(name: str):
    styles = [s for s in _load_styles() if s["name"] != name]
    _save_styles(styles)
    return {"ok": True}


# ── Tags — autocomplete Danbooru (multi-CSV) ───────────────────────────────

_PROFILES_FILE   = _ROOT / "apps" / "painter" / "model_profiles.json"
_SETUP_STATUS    = _ROOT / "apps" / "painter" / "painter_setup.json"

def _load_setup_status() -> dict:
    try:
        return json.loads(_SETUP_STATUS.read_text()) if _SETUP_STATUS.exists() else {}
    except Exception:
        return {}

try:
    import tag_engine as _te
except ImportError:
    _te = None  # type: ignore


def _load_profiles_data() -> dict:
    if not _PROFILES_FILE.exists():
        return {"profiles": [], "default": ""}
    try:
        return json.loads(_PROFILES_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"profiles": [], "default": ""}


def _bg_load_tags():
    """Precarga en background todos los CSVs presentes en disco para los perfiles configurados."""
    if _te is None:
        return
    data = _load_profiles_data()
    for profile in data.get("profiles", []):
        csv_name = profile.get("tag_csv")
        if not csv_name:
            continue
        p = _te.csv_path_for(csv_name)
        if p.exists() and not _te.is_loaded(csv_name):
            try:
                n = _te.load_csv(csv_name)
                if n:
                    print(f"[painter] Tags cargados ({csv_name}): {n:,}")
            except Exception as e:
                print(f"[painter] Error cargando {csv_name}: {e}")


import threading as _threading
_threading.Thread(target=_bg_load_tags, daemon=True).start()


@painter_router.get("/tags/status")
async def tags_status(csv: str = ""):
    """Estado de un CSV específico. csv = nombre sin extensión."""
    if not csv or not _te:
        return {"loaded": False, "count": 0, "csv_present": False}
    csv_p = _te.csv_path_for(csv)
    return {
        "loaded":      _te.is_loaded(csv),
        "count":       _te.tag_count(csv),
        "csv_present": csv_p.exists(),
    }


@painter_router.get("/tags/search")
async def tags_search(q: str = "", csv: str = "", limit: int = 5):
    """Busca tags en el CSV del perfil activo."""
    limit = min(limit, 20)
    if not _te or not csv:
        return {"results": []}
    return {"results": _te.search(csv, q, limit)}


@painter_router.post("/tags/reload")
async def tags_reload(csv: str = ""):
    if not _te or not csv:
        raise HTTPException(400, "Falta el parámetro csv")
    n = _te.load_csv(csv)
    return {"ok": True, "count": n}


@painter_router.get("/tags/profiles")
async def tags_profiles():
    return _load_profiles_data()


@painter_router.get("/tags/download")
async def tags_download(profile_id: str):
    """SSE — descarga el CSV configurado para el perfil indicado."""
    data = _load_profiles_data()
    profiles_map = {p["id"]: p for p in data.get("profiles", [])}
    profile  = profiles_map.get(profile_id)
    if not profile:
        raise HTTPException(404, "Perfil no encontrado")
    csv_name = profile.get("tag_csv")
    csv_url  = profile.get("tag_csv_url")
    if not csv_name or not csv_url:
        raise HTTPException(400, "Este perfil no tiene CSV configurado")

    async def stream() -> AsyncGenerator[str, None]:
        if not _te:
            yield f'data: {json.dumps({"type":"error","msg":"Motor de tags no disponible"})}\n\n'
            return

        dest = _te.csv_path_for(csv_name)
        dest.parent.mkdir(parents=True, exist_ok=True)

        try:
            import aiohttp
            yield f'data: {json.dumps({"type":"start","msg":f"Descargando {csv_name}…"})}\n\n'

            async with aiohttp.ClientSession() as sess:
                async with sess.get(csv_url) as resp:
                    if resp.status != 200:
                        yield f'data: {json.dumps({"type":"error","msg":f"HTTP {resp.status}"})}\n\n'
                        return
                    total      = int(resp.headers.get("Content-Length", 0))
                    downloaded = 0
                    with open(dest, "wb") as f:
                        async for chunk in resp.content.iter_chunked(64 * 1024):
                            f.write(chunk)
                            downloaded += len(chunk)
                            pct = int(downloaded * 100 / total) if total else 0
                            yield f'data: {json.dumps({"type":"progress","downloaded":downloaded,"total":total,"pct":pct})}\n\n'

            yield f'data: {json.dumps({"type":"loading","msg":"Indexando tags…"})}\n\n'
            n = _te.load_csv(csv_name)
            yield f'data: {json.dumps({"type":"done","count":n,"csv":csv_name})}\n\n'

        except Exception as e:
            dest.unlink(missing_ok=True)
            yield f'data: {json.dumps({"type":"error","msg":str(e)})}\n\n'

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


# ── Guardar imagen a disco ───────────────────────────────────────────────────

def _get_outputs_dir() -> Path:
    """Lee outputs path desde hub_config.json; fallback a outputs/ local."""
    cfg_file = _ROOT / "hub" / "hub_config.json"
    try:
        cfg = json.loads(cfg_file.read_text())
        base = cfg.get("paths", {}).get("outputs", "")
        if base:
            return Path(base) / "painter"
    except Exception:
        pass
    return _ROOT / "outputs" / "painter"

@painter_router.post("/save")
async def save_image(body: dict):
    """Guarda la imagen actual (base64) en outputs/painter/ con timestamp."""
    image_b64: str = body.get("image_b64", "")
    if not image_b64:
        raise HTTPException(status_code=400, detail="image_b64 requerido")

    out_dir = _get_outputs_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    ts       = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"painter_{ts}.png"
    dest     = out_dir / filename

    img_bytes = base64.b64decode(image_b64)
    dest.write_bytes(img_bytes)

    return {"path": str(dest), "filename": filename}
