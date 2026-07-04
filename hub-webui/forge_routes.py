"""
Forge Lab API — rutas FastAPI del módulo.

Fase 0: diagnóstico (ComfyUI, modelos, nodos Realtime-Lora).
Fase 2: sets de validación fijos — CRUD de borradores, bloqueo, clonado,
regeneración completa como job en background y servido de imágenes de runs.
Las rutas de merge llegan con las Fases 3+.
"""
import sys
import json
import uuid
import asyncio
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

_HERE = Path(__file__).parent
_ROOT = _HERE.parent
_CORE = _ROOT / "apps" / "forge_lab" / "core"

for _p in [str(_ROOT), str(_CORE)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from comfy_client import ComfyClient
from architectures import get_adapter, SUPPORTED_ARCHITECTURES
import validation_set as vsets
from validation_set import ValidationSet, ValidationSetError, LockedError

forge_router = APIRouter(prefix="/api/forge", tags=["forge"])

_comfy = ComfyClient(port=8188)

# Nodos de comfyUI-Realtime-Lora que la Fase 4 necesita disparar por API
_REQUIRED_NODES = [
    "ZImageSelectiveLoRALoader",
    "LoRALoaderWithAnalysis",
    "ScheduledLoRALoader",
]


def _models_root() -> Path:
    cfg = json.loads((_ROOT / "hub" / "hub_config.json").read_text(encoding="utf-8"))
    return Path(cfg["paths"]["models"])


@forge_router.get("/architectures")
async def architectures():
    out = []
    for name in SUPPORTED_ARCHITECTURES:
        a = get_adapter(name)
        out.append({
            "name": a.name,
            "label": a.label,
            "blocks": a.list_blocks(),
            "groups": [vars(g) for g in a.block_groups()],
            "forbidden_zones": a.forbidden_zones(),
            "sampling_defaults": vars(a.sampling_defaults()),
        })
    return {"architectures": out}


@forge_router.get("/status")
async def status(arch: str = "zimage"):
    """Diagnóstico Fase 0: ComfyUI vivo + modelos presentes + nodos requeridos."""
    try:
        adapter = get_adapter(arch)
    except ValueError as e:
        raise HTTPException(400, str(e))

    root = _models_root()
    files = vars(adapter.model_files())
    models = {k: {"path": v, "present": (root / v).exists()} for k, v in files.items()}

    comfy_up = await _comfy.health_check()
    nodes = {}
    if comfy_up:
        for n in _REQUIRED_NODES:
            nodes[n] = await _comfy.probe_node(n)

    return {
        "arch": adapter.name,
        "comfyui": comfy_up,
        "models": models,
        "nodes": nodes,
        "ready": comfy_up and all(m["present"] for m in models.values())
                 and all(nodes.values() or [False]),
    }


# ═══════════════════════════════════════════════════════════════════════════
# Fase 2 — Sets de validación fijos
# ═══════════════════════════════════════════════════════════════════════════

class SetCreateBody(BaseModel):
    name: str
    arch: str = "zimage"
    starter: bool = True          # precargar el borrador de prompts de arranque


class SetUpdateBody(BaseModel):
    sampling: dict | None = None
    prompts: list[dict] | None = None


class SetCloneBody(BaseModel):
    new_name: str


class SetRunBody(BaseModel):
    model: str | None = None      # None → el del adaptador
    label: str = ""


def _vs_or_404(name: str) -> ValidationSet:
    try:
        return ValidationSet.load(name)
    except ValidationSetError as e:
        raise HTTPException(404, str(e))


@forge_router.get("/sets")
async def sets_list():
    return {"sets": vsets.list_sets()}


@forge_router.post("/sets")
async def sets_create(body: SetCreateBody):
    try:
        adapter = get_adapter(body.arch)
        prompts = vsets.starter_prompts() if body.starter else []
        vs = ValidationSet.create(body.name, adapter.name,
                                  vars(adapter.sampling_defaults()), prompts)
    except (ValidationSetError, ValueError) as e:
        raise HTTPException(400, str(e))
    return vs.to_dict()


@forge_router.get("/sets/{name}")
async def sets_get(name: str):
    return _vs_or_404(name).to_dict()


@forge_router.put("/sets/{name}")
async def sets_update(name: str, body: SetUpdateBody):
    vs = _vs_or_404(name)
    try:
        vs.update(sampling=body.sampling, prompts=body.prompts)
    except LockedError as e:
        raise HTTPException(409, str(e))
    except ValidationSetError as e:
        raise HTTPException(400, str(e))
    return vs.to_dict()


@forge_router.post("/sets/{name}/lock")
async def sets_lock(name: str):
    vs = _vs_or_404(name)
    try:
        vs.lock()
    except LockedError as e:
        raise HTTPException(409, str(e))
    except ValidationSetError as e:
        raise HTTPException(400, str(e))
    return vs.to_dict()


@forge_router.post("/sets/{name}/archive")
async def sets_archive(name: str):
    vs = _vs_or_404(name)
    vs.archive()
    return vs.to_dict()


@forge_router.post("/sets/{name}/unarchive")
async def sets_unarchive(name: str):
    vs = _vs_or_404(name)
    vs.unarchive()
    return vs.to_dict()


@forge_router.post("/sets/{name}/clone")
async def sets_clone(name: str, body: SetCloneBody):
    vs = _vs_or_404(name)
    try:
        return vs.clone(body.new_name).to_dict()
    except ValidationSetError as e:
        raise HTTPException(400, str(e))


@forge_router.delete("/sets/{name}")
async def sets_delete(name: str):
    vs = _vs_or_404(name)
    try:
        vs.delete()
    except LockedError as e:
        raise HTTPException(409, str(e))
    return {"deleted": name}


# ── Regeneración (job en background) ───────────────────────────────────────

_jobs: dict[str, dict] = {}
_MAX_JOBS = 20


async def _run_set_job(job: dict, vs: ValidationSet, model: str | None,
                       label: str):
    def on_progress(p: dict):
        job.update(p)
    try:
        manifest = await vs.run(_comfy, model=model, label=label,
                                on_progress=on_progress)
        job["status"] = "done"
        job["run_id"] = manifest["run_id"]
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)


@forge_router.post("/sets/{name}/run")
async def sets_run(name: str, body: SetRunBody):
    vs = _vs_or_404(name)
    if any(j["status"] == "running" for j in _jobs.values()):
        raise HTTPException(409, "ya hay una regeneración en curso")
    if not await _comfy.health_check():
        raise HTTPException(503, "ComfyUI no responde en :8188 — arráncalo desde el Hub")

    job_id = uuid.uuid4().hex[:12]
    job = {"id": job_id, "set": name, "status": "running",
           "draft": not vs.locked, "model": body.model, "label": body.label,
           "prompt_index": 0, "total": len(vs.prompts), "prompt_id": "",
           "step": 0, "steps_total": vs.sampling["steps"],
           "run_id": None, "error": None}
    if len(_jobs) >= _MAX_JOBS:
        for k in [k for k, j in _jobs.items() if j["status"] != "running"]:
            del _jobs[k]
    _jobs[job_id] = job
    asyncio.create_task(_run_set_job(job, vs, body.model, body.label))
    return {"job_id": job_id}


@forge_router.get("/jobs/{job_id}")
async def job_get(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "job desconocido")
    return job


# ── Runs e imágenes ────────────────────────────────────────────────────────

@forge_router.get("/sets/{name}/runs")
async def runs_list(name: str):
    _vs_or_404(name)
    return {"runs": vsets.list_runs(name)}


@forge_router.delete("/runs/{set_name}/{run_id}")
async def run_delete(set_name: str, run_id: str):
    if any(j["status"] == "running" and j["set"] == set_name
           for j in _jobs.values()):
        raise HTTPException(409, "hay una regeneración en curso sobre este set")
    try:
        vsets.delete_run(set_name, run_id)
    except ValidationSetError as e:
        raise HTTPException(404, str(e))
    return {"deleted": f"{set_name}/{run_id}"}


@forge_router.get("/runs/{set_name}/{run_id}/{filename}")
async def run_image(set_name: str, run_id: str, filename: str):
    base = vsets.RUNS_DIR.resolve()
    path = (base / set_name / run_id / filename).resolve()
    if not path.is_relative_to(base) or not path.is_file():
        raise HTTPException(404, "imagen no encontrada")
    return FileResponse(path)
