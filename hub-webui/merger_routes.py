"""
LoRA Merger API — rutas FastAPI sin PySide6.
Expone: file browser, analyze, suggest, merge (polling progress).
"""
import os
import sys
import uuid
import json
import time
import traceback
import threading
from typing import Optional
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter
from fastapi.responses import Response
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

# Estado de sesiones de merge: id → dict con status, progress, etc.
# Reemplaza el SSE (EventSource) por polling HTTP — más confiable en WebKitGTK/pywebview.
_sessions: dict[str, dict] = {}
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

@merger_router.get("/pick-files")
async def pick_files(initial_dir: str = ""):
    """
    Lanza kdialog (KDE) o zenity (GTK) para seleccionar .safetensors.
    Retorna {"paths": [...], "available": bool}.
    Si available=True y paths=[], el usuario canceló — no abrir el modal fallback.
    """
    import shutil
    import subprocess
    import shlex

    if not initial_dir or not os.path.isdir(initial_dir):
        initial_dir = os.path.expanduser("~")

    def _kdialog():
        result = subprocess.run(
            ["kdialog", "--getopenfilename", initial_dir,
             "SafeTensors (*.safetensors *.pt)", "--multiple",
             "--title", "Seleccionar LoRAs"],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            return []
        return [p for p in shlex.split(result.stdout.strip())
                if p.endswith((".safetensors", ".pt"))]

    def _zenity():
        result = subprocess.run(
            ["zenity", "--file-selection", "--multiple",
             "--file-filter=SafeTensors | *.safetensors",
             "--separator=\n", f"--filename={initial_dir}/",
             "--title=Seleccionar LoRAs"],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            return []
        return [p for p in result.stdout.strip().split("\n")
                if p and p.endswith((".safetensors", ".pt"))]

    if shutil.which("kdialog"):
        try:
            return {"paths": _kdialog(), "available": True}
        except Exception:
            pass
    if shutil.which("zenity"):
        try:
            return {"paths": _zenity(), "available": True}
        except Exception:
            pass

    return {"paths": [], "available": False}


@merger_router.get("/browse")
async def browse(path: str = ""):
    """Lista directorios y archivos .safetensors en un path."""
    if not path:
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
    import asyncio
    loop = asyncio.get_running_loop()

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
    import asyncio
    loop = asyncio.get_running_loop()

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
    """Inicia un merge en background. Retorna session_id para polling."""
    session_id = str(uuid.uuid4())

    with _sessions_lock:
        _sessions[session_id] = {
            "type":    "progress",
            "current": 0,
            "total":   0,
            "layer":   "Iniciando...",
            "error":   None,
            "ts":      time.time(),
        }

    def _run():
        print(f"[merger] Iniciando merge — session {session_id[:8]}")
        try:
            merge, MergeConfig = _get_merger()
            detect = _detect()
            infos  = [detect(p) for p in payload.paths]

            print(f"[merger] LoRAs detectados: {[os.path.basename(p) for p in payload.paths]}")

            c   = payload.config
            cfg = MergeConfig(
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
                # Verificar si el usuario solicitó cancelar
                with _sessions_lock:
                    if _sessions.get(session_id, {}).get("cancel"):
                        raise RuntimeError("Merge cancelado por el usuario.")
                    _sessions[session_id].update({
                        "type":    "progress",
                        "current": prog.current,
                        "total":   prog.total,
                        "layer":   prog.layer_name,
                        "ts":      time.time(),
                    })
                # Imprimir cada 10% o primera capa para no saturar la terminal
                if prog.total and (prog.current % max(1, prog.total // 10) == 0
                                   or prog.current == 1):
                    print(f"[merger] {prog.current}/{prog.total}  {prog.layer_name}")

            output = merge(infos, cfg, _cb)
            print(f"[merger] Merge completado → {output}")

            with _sessions_lock:
                _sessions[session_id] = {
                    "type":        "done",
                    "output_path": output,
                    "error":       None if output else "merge() no retornó ruta",
                    "ts":          time.time(),
                }

        except Exception as e:
            err = f"{e}\n{traceback.format_exc()}"
            print(f"[merger] ERROR:\n{err}")
            with _sessions_lock:
                _sessions[session_id] = {
                    "type":        "done",
                    "output_path": None,
                    "error":       str(e),
                    "ts":          time.time(),
                }

    threading.Thread(target=_run, daemon=True).start()
    return {"session_id": session_id}


@merger_router.post("/cancel/{session_id}")
async def cancel_merge(session_id: str):
    """Solicita la cancelación de un merge activo."""
    with _sessions_lock:
        session = _sessions.get(session_id)
    if session is None:
        return Response(status_code=404)
    with _sessions_lock:
        _sessions[session_id]["cancel"] = True
    print(f"[merger] Cancelación solicitada — session {session_id[:8]}")
    return {"ok": True}


@merger_router.get("/status/{session_id}")
async def merge_status(session_id: str):
    """Polling endpoint: devuelve el estado actual del merge."""
    with _sessions_lock:
        status = _sessions.get(session_id)

    if status is None:
        return Response(status_code=404)

    # Limpiar sesiones "done" pasados 5 minutos para evitar memory leaks
    if status.get("type") == "done":
        age = time.time() - status.get("ts", 0)
        if age > 300:
            with _sessions_lock:
                _sessions.pop(session_id, None)

    return status
