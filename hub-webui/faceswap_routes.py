"""
Face Swap — rutas FastAPI del módulo.

Flujo: foto de escena (destino, aporta pose/expresión) + foto del donante →
LivePortrait reanima al donante → ReActor (HyperSwap, MIT) compone el rostro
sobre la escena. Los nodos viven en apps/comfyui/custom_nodes (sesión 46).
"""
import sys
import uuid
import base64
import asyncio
import subprocess
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

_HERE = Path(__file__).parent
_ROOT = _HERE.parent
_APPS = _ROOT / "apps"

# Import por paquete (faceswap.core.*), nunca módulos sueltos: painter/forge/
# ideogram también definen comfy_client.py y el primero en importarse gana el
# nombre en sys.modules (bug real documentado en forge_routes).
if str(_APPS) not in sys.path:
    sys.path.insert(0, str(_APPS))

from faceswap.core.comfy_client import ComfyClient, ComfyError
from faceswap.core import pipeline
from faceswap.core import nsfw
from faceswap.core import measure as measure_mod
from faceswap.core import reactor_patch
from faceswap.core.pipeline import SwapParams, PipelineError

faceswap_router = APIRouter(prefix="/api/faceswap", tags=["faceswap"])

_comfy = ComfyClient(port=8188)

# Auto-reparación del parche del filtro al cargar el módulo: si una actualización
# de ReActor lo pisó, se re-aplica solo antes de que el operador lance ComfyUI.
_PATCH_RESULT = reactor_patch.ensure_patch()

_REQUIRED_NODES = ["ReActorFaceSwap", "AdvancedLivePortrait", "LoadImage", "SaveImage"]


async def _mask_models() -> tuple[list[str], list[str]]:
    """Modelos que ve el MaskHelper (bbox y sam), desde object_info de ComfyUI."""
    info = await _comfy.get_object_info("ReActorMaskHelper")
    try:
        req = info["input"]["required"]
        return list(req["bbox_model_name"][0]), list(req["sam_model_name"][0])
    except (KeyError, IndexError, TypeError):
        return [], []


def _pick_face_bbox(models: list[str]) -> str:
    """Elige un YOLO de cara (prioriza m>s>n) de los registrados."""
    faces = [m for m in models if "face" in m.lower()]
    for pref in ("yolov8m", "yolov8s", "yolov8n"):
        hit = next((m for m in faces if pref in m.lower()), None)
        if hit:
            return hit
    return faces[0] if faces else (models[0] if models else "")


def _decode_data_uri(data: str, what: str) -> bytes:
    """data URI (data:image/...;base64,xxx) o base64 puro → bytes."""
    data = (data or "").strip()
    if not data:
        raise HTTPException(400, f"falta la imagen: {what}")
    if data.startswith("data:"):
        try:
            data = data.split(",", 1)[1]
        except IndexError:
            raise HTTPException(400, f"data URI inválido en {what}")
    try:
        return base64.b64decode(data)
    except Exception:
        raise HTTPException(400, f"base64 inválido en {what}")


# ═══════════════════════════════════════════════════════════════════════════
# Diagnóstico
# ═══════════════════════════════════════════════════════════════════════════

@faceswap_router.get("/status")
async def status():
    comfy_up = await _comfy.health_check()
    nodes = {}
    swap_models: list[str] = []
    restore_models: list[str] = []
    if comfy_up:
        for n in _REQUIRED_NODES:
            nodes[n] = await _comfy.probe_node(n)
        # las opciones de los combos de ReActor dicen qué modelos hay en disco
        info = await _comfy.get_object_info("ReActorFaceSwap")
        try:
            req = info["input"]["required"]
            swap_models = list(req["swap_model"][0])
            restore_models = list(req["face_restore_model"][0])
        except (KeyError, IndexError, TypeError):
            pass
    model_ok = pipeline.DEFAULT_SWAP_MODEL in swap_models
    return {
        "comfyui": comfy_up,
        "nodes": nodes,
        "nodes_ok": comfy_up and all(nodes.values() or [False]),
        "swap_models": swap_models,
        "restore_models": restore_models,
        "default_swap_model": pipeline.DEFAULT_SWAP_MODEL,
        "swap_model_ok": model_ok,
        "detectors": pipeline.FACE_DETECTORS,
        "nsfw": nsfw.get_config(),
        "nsfw_last": nsfw.last_score(),
    }


# ═══════════════════════════════════════════════════════════════════════════
# Filtro NSFW modulable
# ═══════════════════════════════════════════════════════════════════════════

class NsfwBody(BaseModel):
    enabled: bool = True
    threshold: float = nsfw.DEFAULT_THRESHOLD


@faceswap_router.get("/nsfw")
async def nsfw_get():
    return {"config": nsfw.get_config(), "last": nsfw.last_score(),
            "default": nsfw.DEFAULT_THRESHOLD, "min": nsfw.MIN_THRESHOLD,
            "patch": reactor_patch.status()}


@faceswap_router.post("/nsfw")
async def nsfw_set(body: NsfwBody):
    return {"config": nsfw.set_config(body.enabled, body.threshold)}


class MeasureBody(BaseModel):
    image: str        # data URI


@faceswap_router.post("/measure")
async def measure_endpoint(body: MeasureBody):
    """Corre solo el clasificador NSFW sobre una imagen y devuelve el score,
    sin swap. Para calibrar el umbral con datos reales."""
    data = _decode_data_uri(body.image, "imagen")
    try:
        return measure_mod.measure(data)
    except measure_mod.MeasureError as e:
        raise HTTPException(400, str(e))


@faceswap_router.post("/repair-filter")
async def repair_filter():
    """Re-aplica el parche del filtro por la fuerza (botón de emergencia si una
    actualización de ReActor lo pisó)."""
    return reactor_patch.ensure_patch(force=True)


# ═══════════════════════════════════════════════════════════════════════════
# Ejecución (job en background, mismo patrón que ideogram)
# ═══════════════════════════════════════════════════════════════════════════

class RunBody(BaseModel):
    scene: str                    # data URI — foto real de destino (driving)
    donor: str = ""               # data URI — rostro del donante (compat 1 foto)
    donors: list[str] = []        # data URIs — 1..3 fotos del donante
    use_reenact: bool = False
    retargeting_eyes: float = 0.0
    retargeting_mouth: float = 0.0
    crop_factor: float = 1.7
    command: str = ""
    input_faces_index: str = "0"
    source_faces_index: str = "0"
    facedetection: str = "retinaface_resnet50"
    swap_model: str = pipeline.DEFAULT_SWAP_MODEL
    restore_model: str = pipeline.DEFAULT_RESTORE
    restore_visibility: float = 0.8
    use_mask_helper: bool = True
    mask_blur: int = 12
    mask_dilation: int = 0
    mask_sigma: float = 1.0
    # control del filtro NSFW para este run (se persiste en la config del módulo)
    nsfw_enabled: bool = True
    nsfw_threshold: float = nsfw.DEFAULT_THRESHOLD


_jobs: dict[str, dict] = {}
_MAX_JOBS = 30

# asyncio solo guarda referencias débiles a los tasks: sin retenerlos aquí el
# GC puede matar un job en vuelo (síntoma: contador clavado en 0/N y ComfyUI
# nunca recibe el POST /prompt).
_bg_tasks: set[asyncio.Task] = set()


def _spawn(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)
    return task


async def _run_job(job: dict, params: SwapParams, scene: bytes, donors: list[bytes]):
    def on_phase(name, extra):
        job["phase"] = name
        job.update(extra)

    def on_step(step, total):
        job["step"] = step
        job["steps_total"] = total

    try:
        meta = await pipeline.generate(_comfy, params, scene, donors,
                                       on_phase=on_phase, on_step=on_step)
        job["status"] = "done"
        job["run_id"] = meta["id"]
        job["has_reenact"] = meta["has_reenact"]
    except (PipelineError, ComfyError) as e:
        job["status"] = "error"
        job["error"] = str(e)
    except Exception as e:
        job["status"] = "error"
        job["error"] = f"error inesperado: {e}"


@faceswap_router.post("/run")
async def run(body: RunBody):
    if any(j["status"] == "running" for j in _jobs.values()):
        raise HTTPException(409, "ya hay un swap en curso")
    scene = _decode_data_uri(body.scene, "escena")
    donor_uris = body.donors or ([body.donor] if body.donor else [])
    if not donor_uris:
        raise HTTPException(400, "falta la foto del donante")
    donors = [_decode_data_uri(u, f"donante {i+1}") for i, u in enumerate(donor_uris[:3])]
    # nunca inswapper: el usuario descartó los modelos no-comerciales de swap
    if "inswapper" in body.swap_model.lower():
        raise HTTPException(400, "inswapper está descartado por licencia; usá HyperSwap")

    # ComfyUI tiene que estar arriba: si no, el error de la máscara sería confuso.
    if not await _comfy.health_check():
        raise HTTPException(503, "ComfyUI no responde en :8188 — levantalo desde el hub")

    # Aplicar el estado del filtro NSFW elegido para este run (el nodo lo lee
    # del archivo de config en cada análisis).
    nsfw.set_config(body.nsfw_enabled, body.nsfw_threshold)

    params = SwapParams(**body.model_dump(
        exclude={"scene", "donor", "donors", "nsfw_enabled", "nsfw_threshold"}))

    # Resolver los modelos de máscara desde lo que ComfyUI tiene registrado,
    # para no depender de un nombre hardcodeado que puede no estar en el equipo.
    if params.use_mask_helper:
        bbox_models, sam_models = await _mask_models()
        if not bbox_models or not sam_models:
            raise HTTPException(400, "el MaskHelper no ve modelos bbox/sam en "
                                "E:/Models (ultralytics/bbox y sams); revisá el store")
        if not params.mask_bbox_model:
            params.mask_bbox_model = _pick_face_bbox(bbox_models)
        if not params.mask_sam_model:
            params.mask_sam_model = sam_models[0]
    job_id = uuid.uuid4().hex[:12]
    job = {"id": job_id, "status": "running", "phase": "queued",
           "step": 0, "steps_total": 0, "run_id": None, "error": None}
    if len(_jobs) >= _MAX_JOBS:
        for k in [k for k, j in _jobs.items() if j["status"] != "running"]:
            del _jobs[k]
    _jobs[job_id] = job
    _spawn(_run_job(job, params, scene, donors))
    return {"job_id": job_id}


@faceswap_router.get("/jobs/{job_id}")
async def job_get(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "job desconocido")
    return job


@faceswap_router.post("/interrupt")
async def interrupt():
    await _comfy.interrupt()
    return {"ok": True}


# ═══════════════════════════════════════════════════════════════════════════
# Historial
# ═══════════════════════════════════════════════════════════════════════════

@faceswap_router.get("/history")
async def history():
    return {"runs": pipeline.list_history()}


@faceswap_router.get("/history/{run_id}/meta")
async def history_meta(run_id: str):
    try:
        return pipeline.history_meta(run_id)
    except PipelineError as e:
        raise HTTPException(404, str(e))


@faceswap_router.get("/history/{run_id}/image")
async def history_image(run_id: str, kind: str = "result"):
    try:
        return FileResponse(pipeline.history_image(run_id, kind))
    except PipelineError as e:
        raise HTTPException(404, str(e))


@faceswap_router.post("/history/{run_id}/reveal")
async def history_reveal(run_id: str):
    """Abre la carpeta del run en el explorador (hub local)."""
    try:
        d, img = pipeline.history_dir(run_id)
    except PipelineError as e:
        raise HTTPException(404, str(e))
    try:
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", str(img)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(img)])
        else:
            subprocess.Popen(["xdg-open", str(d)])
    except Exception as e:
        raise HTTPException(500, f"no se pudo abrir la carpeta: {e}")
    return {"opened": str(d)}


@faceswap_router.delete("/history/{run_id}")
async def history_delete(run_id: str):
    try:
        pipeline.delete_run(run_id)
    except PipelineError as e:
        raise HTTPException(404, str(e))
    return {"deleted": run_id}
