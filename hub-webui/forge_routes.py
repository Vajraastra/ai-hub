"""
Forge Lab API — rutas FastAPI del módulo.

Fase 0: diagnóstico (ComfyUI, modelos, nodos Realtime-Lora).
Fase 2: sets de validación fijos — CRUD de borradores, bloqueo, clonado,
regeneración completa como job en background y servido de imágenes de runs.
Fase 3: catálogo de LoRAs, merge LoRA→checkpoint derivado (job en background,
proceso CPU aparte — no necesita ComfyUI) y registro de checkpoints con linaje.
"""
import sys
import os
import json
import uuid
import asyncio
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

_HERE = Path(__file__).parent
_ROOT = _HERE.parent
_APPS = _ROOT / "apps"

# Import por paquete (forge_lab.core.*), nunca módulos sueltos: painter/core
# también define comfy_client.py y el primero en importarse se queda con el
# nombre en sys.modules (bug real: forge acababa cargando workflows de painter).
if str(_APPS) not in sys.path:
    sys.path.insert(0, str(_APPS))

from forge_lab.core.comfy_client import ComfyClient, load_workflow
from forge_lab.core.architectures import get_adapter, SUPPORTED_ARCHITECTURES
from forge_lab.core import validation_set as vsets
from forge_lab.core import explore
from forge_lab.core.validation_set import ValidationSet, ValidationSetError, LockedError
from forge_lab.core.merge import MergeOrchestrator, MergeError
from forge_lab.core.explore import ExploreError

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


_merger = MergeOrchestrator(_models_root())


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


# ═══════════════════════════════════════════════════════════════════════════
# Fase 3 — LoRAs, merge y checkpoints derivados
# ═══════════════════════════════════════════════════════════════════════════

class MergeBody(BaseModel):
    arch: str = "zimage"
    base: str                     # nombre de checkpoint del registro
    lora: str                     # ruta relativa a <models>/loras
    strength: float = 1.0
    name: str                     # slug del checkpoint derivado
    label: str = ""
    # None = merge completo (Fase 3); lista = bloques a dosis 1.0;
    # dict bloque→dosis (Fase 4: escala final del delta = strength × dosis)
    blocks: dict[str, float] | list[str] | None = None


@forge_router.get("/loras")
async def loras_list(arch: str = "zimage"):
    return {"loras": _merger.list_loras(arch)}


@forge_router.get("/checkpoints")
async def checkpoints_list(arch: str = "zimage"):
    return {"checkpoints": _merger.list_checkpoints(arch)}


@forge_router.delete("/checkpoints/{name}")
async def checkpoint_delete(name: str):
    if any(j["status"] == "running" for j in _jobs.values()):
        raise HTTPException(409, "hay un job en curso; espera a que termine")
    try:
        _merger.delete_checkpoint(name)
    except MergeError as e:
        raise HTTPException(404, str(e))
    return {"deleted": name}


async def _merge_job(job: dict, body: MergeBody):
    def on_progress(p: dict):
        job.update(p)
    try:
        entry = await _merger.merge_lora(
            arch=body.arch, base=body.base, lora_file=body.lora,
            strength=body.strength, name=body.name, label=body.label,
            blocks=body.blocks, on_progress=on_progress)
        job["status"] = "done"
        job["checkpoint"] = entry
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)


@forge_router.post("/merge")
async def merge_start(body: MergeBody):
    """Merge como job en background. Corre en CPU en proceso aparte:
    ComfyUI no hace falta y la GPU queda libre; aun así se serializa con
    los runs para no confundir el seguimiento (un job a la vez)."""
    if any(j["status"] == "running" for j in _jobs.values()):
        raise HTTPException(409, "ya hay un job en curso")
    # validaciones síncronas baratas antes de aceptar el job
    try:
        _merger.get_checkpoint(body.base, body.arch)
    except MergeError as e:
        raise HTTPException(400, str(e))
    job_id = uuid.uuid4().hex[:12]
    job = {"id": job_id, "type": "merge", "status": "running",
           "name": body.name, "base": body.base, "lora": body.lora,
           "strength": body.strength, "phase": "", "done": 0, "total": 1,
           "checkpoint": None, "error": None}
    if len(_jobs) >= _MAX_JOBS:
        for k in [k for k, j in _jobs.items() if j["status"] != "running"]:
            del _jobs[k]
    _jobs[job_id] = job
    asyncio.create_task(_merge_job(job, body))
    return {"job_id": job_id}


# ═══════════════════════════════════════════════════════════════════════════
# Fase 4 — Exploración por bloques (laboratorio de switches)
# ═══════════════════════════════════════════════════════════════════════════

class ExploreSessionBody(BaseModel):
    arch: str = "zimage"
    checkpoint: str               # nombre del registro (para el merge final)
    lora: str                     # ruta relativa posix a <models>/loras
    strength: float = 1.0
    prompt: str
    negative: str = ""
    seed: int
    sampling: dict | None = None  # None → defaults del adaptador


class ExploreGenerateBody(BaseModel):
    config: dict                  # {"layers": {"0": dosis...}, "other": dosis}


class ExploreConfirmBody(BaseModel):
    set_name: str
    gen_id: str                   # generación cuya config se confirma
    label: str = ""


class ExploreRefBody(BaseModel):
    gen_id: str


@forge_router.get("/explore/session")
async def explore_session_get():
    return {"session": explore.get_session()}


@forge_router.post("/explore/session")
async def explore_session_create(body: ExploreSessionBody):
    if any(j["status"] == "running" for j in _jobs.values()):
        raise HTTPException(409, "hay un job en curso; espera a que termine")
    try:
        ckpt = _merger.get_checkpoint(body.checkpoint, body.arch)
        if not ckpt["present"]:
            raise MergeError(f"el checkpoint {body.checkpoint!r} no está en disco")
        session = explore.create_session(
            arch=body.arch, checkpoint=ckpt["name"], model=ckpt["unet_name"],
            lora=body.lora, strength=body.strength, prompt=body.prompt,
            negative=body.negative, seed=body.seed, sampling=body.sampling)
    except (ExploreError, MergeError) as e:
        raise HTTPException(400, str(e))
    return {"session": session}


@forge_router.delete("/explore/session")
async def explore_session_close():
    if any(j["status"] == "running" for j in _jobs.values()):
        raise HTTPException(409, "hay un job en curso; espera a que termine")
    explore.clear_session()
    return {"closed": True}


@forge_router.post("/explore/reference")
async def explore_reference(body: ExploreRefBody):
    try:
        return {"session": explore.set_reference(body.gen_id)}
    except ExploreError as e:
        raise HTTPException(404, str(e))


@forge_router.get("/explore/image/{gen_id}")
async def explore_image(gen_id: str):
    try:
        return FileResponse(explore.image_path(gen_id))
    except ExploreError as e:
        raise HTTPException(404, str(e))


async def _explore_gen_job(job: dict, config: dict):
    def on_progress(step, total):
        job.update({"step": step, "steps_total": total})
    try:
        gen = await explore.generate(_comfy, config, on_progress=on_progress)
        job["status"] = "done"
        job["gen"] = gen
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)


@forge_router.post("/explore/generate")
async def explore_generate(body: ExploreGenerateBody):
    session = explore.get_session()
    if not session:
        raise HTTPException(404, "no hay sesión de exploración activa")
    if any(j["status"] == "running" for j in _jobs.values()):
        raise HTTPException(409, "ya hay un job en curso")
    if not await _comfy.health_check():
        raise HTTPException(503, "ComfyUI no responde en :8188 — arráncalo desde el Hub")
    try:
        config = explore.normalize_config(body.config)
    except ExploreError as e:
        raise HTTPException(400, str(e))
    job_id = uuid.uuid4().hex[:12]
    job = {"id": job_id, "type": "explore", "status": "running",
           "step": 0, "steps_total": session["sampling"]["steps"],
           "gen": None, "error": None}
    _jobs[job_id] = job
    asyncio.create_task(_explore_gen_job(job, config))
    return {"job_id": job_id}


async def _explore_confirm_job(job: dict, vs: ValidationSet, session: dict,
                               config: dict, label: str):
    def on_progress(p: dict):
        job.update(p)
    try:
        template = load_workflow("txt2img_lora_selective.json", session["arch"])
        template["10"]["inputs"].update(explore.node_inputs(config))
        lora_name = Path(session["lora"]).name
        desc = (f"{session['checkpoint']} + {lora_name} @ "
                f"{session['strength']} [{explore.config_summary(config)}]")
        manifest = await vs.run(
            _comfy, model=session["model"], label=label,
            on_progress=on_progress, template=template,
            extra_params={"lora": session["lora"].replace("/", os.sep),
                          "lora_strength": session["strength"]},
            model_desc=desc,
            extra_manifest={"runtime_lora": {
                "lora": session["lora"], "strength": session["strength"],
                "config": config}})
        job["status"] = "done"
        job["run_id"] = manifest["run_id"]
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)


@forge_router.post("/explore/confirm")
async def explore_confirm(body: ExploreConfirmBody):
    """Doble confirmación: regenera el set completo en runtime con la config
    de bloques de una generación de la sesión. El run SÍ se guarda (es
    evidencia de comparación); las imágenes de exploración no."""
    session = explore.get_session()
    if not session:
        raise HTTPException(404, "no hay sesión de exploración activa")
    gen = next((g for g in session["generations"] if g["id"] == body.gen_id), None)
    if gen is None:
        raise HTTPException(404, f"no existe la generación {body.gen_id!r}")
    vs = _vs_or_404(body.set_name)
    if any(j["status"] == "running" for j in _jobs.values()):
        raise HTTPException(409, "ya hay un job en curso")
    if not await _comfy.health_check():
        raise HTTPException(503, "ComfyUI no responde en :8188 — arráncalo desde el Hub")
    job_id = uuid.uuid4().hex[:12]
    job = {"id": job_id, "type": "run", "set": body.set_name,
           "status": "running", "draft": not vs.locked,
           "model": session["model"], "label": body.label,
           "prompt_index": 0, "total": len(vs.prompts), "prompt_id": "",
           "step": 0, "steps_total": vs.sampling["steps"],
           "run_id": None, "error": None}
    _jobs[job_id] = job
    asyncio.create_task(_explore_confirm_job(job, vs, session,
                                             gen["config"], body.label))
    return {"job_id": job_id}


@forge_router.post("/explore/merge")
async def explore_merge(body: dict):
    """Merge final con la config de una generación de la sesión: traduce la
    config a bloques del worker y delega en /merge (misma matemática que el
    preview runtime — dosis lineal, sqrt en el nodo)."""
    session = explore.get_session()
    if not session:
        raise HTTPException(404, "no hay sesión de exploración activa")
    gen = next((g for g in session["generations"]
                if g["id"] == body.get("gen_id")), None)
    if gen is None:
        raise HTTPException(404, f"no existe la generación {body.get('gen_id')!r}")
    try:
        blocks = explore.config_to_merge_blocks(gen["config"])
    except ExploreError as e:
        raise HTTPException(400, str(e))
    return await merge_start(MergeBody(
        arch=session["arch"], base=session["checkpoint"],
        lora=session["lora"], strength=session["strength"],
        name=str(body.get("name", "")), label=str(body.get("label", "")),
        blocks=blocks))


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
