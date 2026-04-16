"""
LoRA Merger API — rutas FastAPI sin PySide6.
Expone: file browser, analyze, suggest, merge (SSE progress).
"""
import os
import sys
import uuid
import json
import queue
import asyncio
import threading
from typing import Optional
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter
from fastapi.responses import StreamingResponse, Response
from pydantic import BaseModel

# ── Paths ────────────────────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)           # /ai-hub
_HUB  = os.path.join(_ROOT, "hub")
_APPS = os.path.join(_ROOT, "apps")

for _p in [_ROOT, _HUB, _APPS]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

merger_router = APIRouter(prefix="/api/merger", tags=["merger"])

# Sesiones de merge activas: id → queue.Queue
_sessions: dict[str, queue.Queue] = {}
_sessions_lock = threading.Lock()
_pool = ThreadPoolExecutor(max_workers=4)


# ── Lazy imports ──────────────────────────────────────────────────────────────

def _detect():
    from lora_merger.core.detector import detect
    return detect

def _analyzer():
    from lora_merger.core.analyzer import analyze
    return analyze

def _validate():
    from lora_merger.core.validator import validate
    return validate

def _suggest():
    from lora_merger.core.suggester import suggest
    return suggest

def _get_merger():
    from lora_merger.core.merger import merge, MergeConfig
    return merge, MergeConfig


# ── Serialización ─────────────────────────────────────────────────────────────

def _info_to_dict(info) -> dict:
    return {
        "path":       info.path,
        "name":       os.path.basename(info.path),
        "arch":       info.arch,
        "arch_id":    info.arch_id,
        "num_layers": info.num_layers,
        "has_te":     info.has_te,
        "has_unet":   info.has_unet,
        "size_mb":    info.size_mb,
        "ranks":      info.ranks,
        "alphas":     info.alphas,
        "error":      info.error,
    }

def _analysis_to_dict(a) -> Optional[dict]:
    if a is None:
        return None
    return {
        "content_type":        a.content_type,
        "content_explanation": a.content_explanation,
        "block_scores":        a.block_scores or {},
        "layer_scores": [
            {"prefix": ls.prefix, "block_name": ls.block_name,
             "score": ls.score, "rank": ls.rank, "alpha": ls.alpha}
            for ls in (a.layer_scores or [])
        ],
        "error": a.error,
    }


# ── Pydantic models ───────────────────────────────────────────────────────────

class AnalyzePayload(BaseModel):
    paths: list[str]

class MergeConfigPayload(BaseModel):
    method:       str          = "weighted_sum"
    weights:      list[float]  = []
    target_rank:  Optional[int] = None
    layer_filter: str          = "full"
    dare_density: float        = 0.5
    ties_k:       float        = 0.2
    slerp_t:      float        = 0.5
    output_path:  str          = ""
    output_dtype: str          = "bf16"

class MergePayload(BaseModel):
    paths:  list[str]
    config: MergeConfigPayload


# ── Endpoints ─────────────────────────────────────────────────────────────────

@merger_router.get("/browse")
async def browse(path: str = ""):
    """Lista directorios y archivos .safetensors en un path."""
    if not path:
        # Usar directorio de outputs del hub si está configurado
        try:
            cfg_path = os.path.join(_HUB, "hub_config.json")
            with open(cfg_path) as f:
                cfg = json.load(f)
            path = cfg.get("paths", {}).get("models", "") or os.path.expanduser("~")
        except Exception:
            path = os.path.expanduser("~")

    path = os.path.abspath(os.path.expanduser(path))

    if not os.path.isdir(path):
        return {"error": f"No encontrado: {path}", "path": path,
                "parent": None, "dirs": [], "files": []}

    try:
        entries = os.listdir(path)
    except PermissionError:
        return {"error": "Sin permiso", "path": path, "parent": None,
                "dirs": [], "files": []}

    dirs = sorted(
        [e for e in entries
         if os.path.isdir(os.path.join(path, e)) and not e.startswith(".")],
        key=str.lower,
    )
    safetensors = sorted(
        [e for e in entries if e.lower().endswith(".safetensors")],
        key=str.lower,
    )
    parent = os.path.dirname(path) if path != "/" else None

    return {
        "path":   path,
        "parent": parent,
        "dirs":   dirs,
        "files": [
            {
                "name":    f,
                "path":    os.path.join(path, f),
                "size_mb": round(os.path.getsize(os.path.join(path, f)) / 1024 ** 2, 1),
            }
            for f in safetensors
        ],
    }


@merger_router.post("/analyze")
async def analyze_loras(payload: AnalyzePayload):
    """Detect + analyze cada LoRA. Devuelve lista de resultados."""
    loop = asyncio.get_event_loop()

    def _one(path: str) -> dict:
        try:
            info = _detect()(path)
            if info.error:
                return {"path": path, "info": _info_to_dict(info),
                        "analysis": None, "error": info.error}
            analysis = _analyzer()(info)
            return {"path": path, "info": _info_to_dict(info),
                    "analysis": _analysis_to_dict(analysis), "error": None}
        except Exception as e:
            return {"path": path, "info": None, "analysis": None, "error": str(e)}

    results = await asyncio.gather(
        *[loop.run_in_executor(_pool, _one, p) for p in payload.paths]
    )
    return {"results": list(results)}


@merger_router.post("/suggest")
async def suggest_merge(payload: AnalyzePayload):
    """Detect + analyze + validate + suggest en un call."""
    loop = asyncio.get_event_loop()

    def _run():
        detect   = _detect()
        analyzer = _analyzer()
        validate = _validate()
        suggest  = _suggest()

        infos    = [detect(p) for p in payload.paths]
        analyses = [analyzer(i) for i in infos]
        val      = validate(infos)
        sug      = suggest(infos, analyses, val)

        return {
            "method":       sug.method,
            "method_label": sug.method_label,
            "weights":      sug.weights,
            "layer_filter": sug.layer_filter,
            "target_rank":  sug.target_rank,
            "explanation":  sug.explanation,
            "warnings":     sug.warnings,
            "validation": {
                "compatible":            val.compatible,
                "warnings":              val.warnings,
                "errors":                val.errors,
                "suggested_target_rank": val.suggested_target_rank,
            },
        }

    return await loop.run_in_executor(_pool, _run)


@merger_router.post("/merge")
async def start_merge(payload: MergePayload):
    """Inicia un merge en background. Retorna session_id para SSE."""
    session_id = str(uuid.uuid4())
    q: queue.Queue = queue.Queue()
    with _sessions_lock:
        _sessions[session_id] = q

    def _run():
        try:
            merge, MergeConfig = _get_merger()
            detect = _detect()
            infos  = [detect(p) for p in payload.paths]
            c      = payload.config
            cfg    = MergeConfig(
                method       = c.method,
                weights      = c.weights or [1.0 / len(payload.paths)] * len(payload.paths),
                target_rank  = c.target_rank,
                layer_filter = c.layer_filter,
                dare_density = c.dare_density,
                ties_k       = c.ties_k,
                slerp_t      = c.slerp_t,
                output_path  = c.output_path,
                output_dtype = c.output_dtype,
            )

            def _cb(prog):
                q.put({
                    "type":    "progress",
                    "current": prog.current,
                    "total":   prog.total,
                    "layer":   prog.layer_name,
                    "done":    prog.done,
                    "error":   prog.error,
                })

            output = merge(infos, cfg, _cb)
            q.put({
                "type":        "done",
                "output_path": output,
                "error":       None if output else "merge() no retornó ruta de salida",
            })
        except Exception as e:
            import traceback
            q.put({"type": "done", "output_path": None,
                   "error": f"{e}\n{traceback.format_exc()}"})

    threading.Thread(target=_run, daemon=True).start()
    return {"session_id": session_id}


@merger_router.get("/progress/{session_id}")
async def merge_progress(session_id: str):
    """SSE stream del progreso de un merge activo."""
    q = _sessions.get(session_id)
    if not q:
        return Response(status_code=404)

    async def _gen():
        loop = asyncio.get_event_loop()
        try:
            while True:
                try:
                    event = await loop.run_in_executor(
                        None, lambda: q.get(timeout=30)
                    )
                    yield f"data: {json.dumps(event)}\n\n"
                    if event.get("type") == "done":
                        break
                except queue.Empty:
                    yield 'data: {"type":"ping"}\n\n'
        finally:
            with _sessions_lock:
                _sessions.pop(session_id, None)

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
