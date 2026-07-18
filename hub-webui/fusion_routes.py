"""
Forge Fusion API (fork de Forge Lab, plan s62) — rutas FastAPI del módulo.

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

# Import por paquete (forge_fusion.core.*), nunca módulos sueltos: painter/core
# también define comfy_client.py y el primero en importarse se queda con el
# nombre en sys.modules (bug real: forge acababa cargando workflows de painter).
if str(_APPS) not in sys.path:
    sys.path.insert(0, str(_APPS))

from forge_fusion.core.comfy_client import ComfyClient, load_workflow
from forge_fusion.core.architectures import get_adapter, SUPPORTED_ARCHITECTURES
from forge_fusion.core import validation_set as vsets
from forge_fusion.core import explore
from forge_fusion.core import model_config
from forge_fusion.core import favorites
from forge_fusion.core import battery
from forge_fusion.core import lora_triggers
from forge_fusion.core.battery import BatteryError
from forge_fusion.core.favorites import FavoritesError
from forge_fusion.core.validation_set import ValidationSet, ValidationSetError, LockedError
from forge_fusion.core.merge import MergeOrchestrator, MergeError
from forge_fusion.core.explore import ExploreError
from forge_fusion.core.model_config import ModelConfigError

fusion_router = APIRouter(prefix="/api/fusion", tags=["fusion"])

_comfy = ComfyClient(port=8188)


def _models_root() -> Path:
    cfg = json.loads((_ROOT / "hub" / "hub_config.json").read_text(encoding="utf-8"))
    return Path(cfg["paths"]["models"])


_merger = MergeOrchestrator(_models_root())


@fusion_router.get("/architectures")
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
            "explore_switches": a.explore_switches(),
            "multi_base": a.multi_base,
            "file_keys": list(a.file_keys()),
        })
    return {"architectures": out}


@fusion_router.get("/sampling_options")
async def sampling_options():
    """Samplers/schedulers válidos según el KSampler de la instalación."""
    return await _comfy.get_sampler_options()


@fusion_router.get("/status")
async def status(arch: str = "zimage"):
    """Diagnóstico Fase 0: ComfyUI vivo + modelos presentes + nodos requeridos."""
    try:
        adapter = get_adapter(arch)
    except ValueError as e:
        raise HTTPException(400, str(e))

    root = _models_root()
    files = model_config.model_files(arch)   # defaults + overrides del usuario
    models = {k: {"path": v, "present": (root / v).exists()} for k, v in files.items()}

    comfy_up = await _comfy.health_check()
    nodes = {}
    if comfy_up:
        for n in adapter.required_nodes():
            nodes[n] = await _comfy.probe_node(n)

    models_ok = all(m["present"] for m in models.values())
    if not models_ok and adapter.multi_base:
        # sin base oficial única: basta con que el almacén tenga checkpoints
        models_ok = any(e["present"] for e in _merger.list_checkpoints(arch)
                        if e["kind"] == "official")
    return {
        "arch": adapter.name,
        "comfyui": comfy_up,
        "models": models,
        "nodes": nodes,
        "ready": comfy_up and models_ok and all(nodes.values() or [False]),
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


@fusion_router.get("/sets")
async def sets_list():
    return {"sets": vsets.list_sets()}


@fusion_router.post("/sets")
async def sets_create(body: SetCreateBody):
    try:
        adapter = get_adapter(body.arch)
        prompts = vsets.starter_prompts() if body.starter else []
        vs = ValidationSet.create(body.name, adapter.name,
                                  vars(adapter.sampling_defaults()), prompts)
    except (ValidationSetError, ValueError) as e:
        raise HTTPException(400, str(e))
    return vs.to_dict()


@fusion_router.get("/sets/{name}")
async def sets_get(name: str):
    return _vs_or_404(name).to_dict()


@fusion_router.put("/sets/{name}")
async def sets_update(name: str, body: SetUpdateBody):
    vs = _vs_or_404(name)
    try:
        vs.update(sampling=body.sampling, prompts=body.prompts)
    except LockedError as e:
        raise HTTPException(409, str(e))
    except ValidationSetError as e:
        raise HTTPException(400, str(e))
    return vs.to_dict()


@fusion_router.post("/sets/{name}/lock")
async def sets_lock(name: str):
    vs = _vs_or_404(name)
    try:
        vs.lock()
    except LockedError as e:
        raise HTTPException(409, str(e))
    except ValidationSetError as e:
        raise HTTPException(400, str(e))
    return vs.to_dict()


@fusion_router.post("/sets/{name}/archive")
async def sets_archive(name: str):
    vs = _vs_or_404(name)
    vs.archive()
    return vs.to_dict()


@fusion_router.post("/sets/{name}/unarchive")
async def sets_unarchive(name: str):
    vs = _vs_or_404(name)
    vs.unarchive()
    return vs.to_dict()


@fusion_router.post("/sets/{name}/clone")
async def sets_clone(name: str, body: SetCloneBody):
    vs = _vs_or_404(name)
    try:
        return vs.clone(body.new_name).to_dict()
    except ValidationSetError as e:
        raise HTTPException(400, str(e))


@fusion_router.delete("/sets/{name}")
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

# asyncio solo guarda referencias débiles a los tasks: sin retenerlos aquí el
# GC puede matar un job en vuelo (síntoma: contador clavado en 0/N y ComfyUI
# nunca recibe el POST /prompt).
_bg_tasks: set[asyncio.Task] = set()

# task vivo de cada job (para poder cancelarlo); fuera del dict del job porque
# ese dict se serializa tal cual en GET /jobs/{id}
_job_tasks: dict[str, asyncio.Task] = {}


def _spawn(coro, job: dict | None = None) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)
    if job is not None:
        jid = job["id"]
        _job_tasks[jid] = task
        task.add_done_callback(lambda _t: _job_tasks.pop(jid, None))
    return task


def _reject_if_job_running():
    """Un job a la vez. El 409 nombra al job que bloquea: un mensaje genérico
    ya llevó a perseguir causas fantasma (BITACORA s69) cuando el bloqueo era
    un job zombi en 'running'."""
    j = next((j for j in _jobs.values() if j["status"] == "running"), None)
    if j:
        raise HTTPException(
            409, f"ya hay un job en curso: {j['type']} {j['id']} — "
                 "cancélalo o espera a que termine")


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


@fusion_router.post("/sets/{name}/run")
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
    _spawn(_run_set_job(job, vs, body.model, body.label), job)
    return {"job_id": job_id}


@fusion_router.get("/jobs/{job_id}")
async def job_get(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "job desconocido")
    return job


@fusion_router.post("/jobs/{job_id}/cancel")
async def job_cancel(job_id: str):
    """Failsafe: cancela el job en curso (interrumpe ComfyUI si el prompt
    llegó; mata el task si nunca llegó a enviarse) y libera el candado de
    'ya hay un job en curso' para poder relanzar."""
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "job desconocido")
    if job["status"] != "running":
        return job
    if job.get("type") == "merge":
        # el merge corre en un proceso CPU aparte: cancelar el task lo dejaría
        # huérfano escribiendo el checkpoint a medias
        raise HTTPException(409, "el merge no se puede cancelar")
    try:
        # timeout defensivo: si ComfyUI no responde, igual hay que soltar el
        # candado de "job en curso" — si no, el cancelar tampoco cancela nada
        await asyncio.wait_for(_comfy.interrupt(), timeout=5)
    except asyncio.TimeoutError:
        pass
    task = _job_tasks.get(job_id)
    if task and not task.done():
        task.cancel()
    job["status"] = "error"
    job["error"] = "cancelado por el usuario"
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


def _norm_arch(s: str) -> str:
    """'Z-Image' / 'z_image' / 'ZImage Turbo' → comparable con 'zimage'."""
    import re as _re
    return _re.sub(r"[^a-z0-9]", "", (s or "").lower())


async def _vault_arch_map() -> dict:
    """{stem_lower: base_model} del Model Vault; vacío si el vault no está."""
    try:
        import vault_routes
        amap = await vault_routes.arch_map()
        return {k.lower(): v for k, v in amap.items()}
    except Exception:
        return {}


@fusion_router.get("/loras")
async def loras_list(arch: str = "zimage"):
    """Catálogo de LoRAs. arch_match combina dos señales: el header del
    safetensors (detección de merge.py) y la clasificación base_model del
    Model Vault — un LoRA sin metadata reconocible pero catalogado en el
    vault como Z-Image cuenta como match (petición del usuario: al elegir
    checkpoint de una arquitectura, ver solo sus LoRAs)."""
    loras = _merger.list_loras(arch)
    amap = await _vault_arch_map()
    target = _norm_arch(arch)
    fav = favorites.ids("loras")
    for l in loras:
        bm = amap.get(Path(l["file"]).stem.lower())
        if bm:
            l["vault_arch"] = bm
            if not l["arch_match"] and target and target in _norm_arch(bm):
                l["arch_match"] = True
        l["is_favorite"] = l["file"] in fav
    loras.sort(key=lambda e: (not e["arch_match"], e["name"].lower()))
    return {"loras": loras}


@fusion_router.get("/lora/triggers")
async def lora_triggers_get(file: str):
    """Trigger words del LoRA: sidecars de Civitai/Lora-Manager o, en su
    defecto, los tags más frecuentes del dataset de entrenamiento (kohya).
    Barato (JSON + header), con cache por mtime en el módulo."""
    try:
        return lora_triggers.triggers_for(_models_root(), file)
    except lora_triggers.TriggerError as e:
        raise HTTPException(404, str(e))


@fusion_router.get("/lora-preview")
async def lora_preview(file: str):
    """Imagen .preview.* junto al safetensors (convención del Vault)."""
    try:
        return FileResponse(_merger.lora_preview_path(file))
    except MergeError:
        raise HTTPException(404, "sin preview")


@fusion_router.get("/checkpoint-preview")
async def checkpoint_preview(name: str, arch: str = "zimage"):
    """Imagen .preview.* de un checkpoint (oficial o derivado)."""
    try:
        return FileResponse(_merger.checkpoint_preview_path(name, arch))
    except MergeError:
        raise HTTPException(404, "sin preview")


class ModelFilesBody(BaseModel):
    arch: str = "zimage"
    files: dict                    # {clave: path relativo | "" (= default)}


@fusion_router.get("/model-files")
async def model_files_get(arch: str = "zimage"):
    """Paths de modelo efectivos + defaults del adaptador + opciones
    elegibles escaneadas del almacén global."""
    try:
        adapter = get_adapter(arch)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "arch": arch,
        "files": model_config.model_files(arch),
        "defaults": adapter.model_files(),
        "options": model_config.list_options(_models_root(), arch),
    }


@fusion_router.put("/model-files")
async def model_files_put(body: ModelFilesBody):
    if any(j["status"] == "running" for j in _jobs.values()):
        raise HTTPException(409, "hay un job en curso; espera a que termine")
    try:
        files = model_config.set_model_files(body.arch, body.files,
                                             _models_root())
    except (ModelConfigError, ValueError) as e:
        raise HTTPException(400, str(e))
    return {"arch": body.arch, "files": files}


@fusion_router.get("/checkpoints")
async def checkpoints_list(arch: str = "zimage"):
    fav = favorites.ids("checkpoints")
    ckpts = _merger.list_checkpoints(arch)
    for c in ckpts:
        c["is_favorite"] = c["name"] in fav
    return {"checkpoints": ckpts}


def _reveal_in_explorer(path: Path):
    """Abre la carpeta en el explorador de archivos nativo del SO. Windows es
    el target soportado (os.startfile); best-effort en macOS/Linux."""
    if sys.platform == "win32":
        os.startfile(str(path))                                    # noqa: S606
    elif sys.platform == "darwin":
        import subprocess
        subprocess.Popen(["open", str(path)])
    else:
        import subprocess
        subprocess.Popen(["xdg-open", str(path)])


@fusion_router.post("/open-output-folder")
async def open_output_folder(arch: str = "zimage", mode: str = "derive"):
    """Abre en el explorador la carpeta contenedora de los productos del
    workbench (checkpoints derivados o LoRAs fusionados según el modo), para
    purgar o mover los merges sin navegar el árbol de carpetas. La crea si aún
    no existe (p. ej. antes del primer merge)."""
    try:
        folder = _merger.output_dir(arch, mode)
        folder.mkdir(parents=True, exist_ok=True)
        _reveal_in_explorer(folder)
    except Exception as e:
        raise HTTPException(500, f"no se pudo abrir la carpeta: {e}")
    return {"opened": str(folder)}


class FavToggleBody(BaseModel):
    kind: str                      # "loras" | "checkpoints"
    id: str                        # file (lora) | name (checkpoint)
    on: bool | None = None         # None → alterna


@fusion_router.get("/favorites")
async def favorites_get():
    return favorites.load()


@fusion_router.post("/favorites/toggle")
async def favorites_toggle(body: FavToggleBody):
    try:
        on = favorites.toggle(body.kind, body.id, body.on)
    except FavoritesError as e:
        raise HTTPException(400, str(e))
    return {"kind": body.kind, "id": body.id, "favorite": on}


@fusion_router.delete("/checkpoints/{name}")
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


@fusion_router.post("/merge")
async def merge_start(body: MergeBody):
    """Merge como job en background. Corre en CPU en proceso aparte:
    ComfyUI no hace falta y la GPU queda libre; aun así se serializa con
    los runs para no confundir el seguimiento (un job a la vez)."""
    _reject_if_job_running()
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
    _spawn(_merge_job(job, body))
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
    mode: str = "derive"          # derive (checkpoint) | fuse (LoRA A+B)
    lora2: str = ""               # LoRA B (obligatorio en modo fuse)
    strength2: float = 1.0


class ExploreGenerateBody(BaseModel):
    config: dict                  # {"layers": {"0": dosis...}, "other": dosis}
    config2: dict | None = None   # máscara del LoRA B en modo fusión (F4)


class ExploreConfirmBody(BaseModel):
    set_name: str
    gen_id: str                   # generación cuya config se confirma
    label: str = ""


class ExploreRefBody(BaseModel):
    gen_id: str


@fusion_router.get("/explore/session")
async def explore_session_get():
    return {"session": explore.get_session(), "draft": explore.get_draft(),
            "has_base": explore.BASE_FILE.is_file()}


@fusion_router.post("/explore/session")
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
            negative=body.negative, seed=body.seed, sampling=body.sampling,
            models_root=_models_root(),
            mode=body.mode, lora2=body.lora2, strength2=body.strength2)
    except (ExploreError, MergeError) as e:
        raise HTTPException(400, str(e))
    return {"session": session}


@fusion_router.delete("/explore/session")
async def explore_session_close():
    if any(j["status"] == "running" for j in _jobs.values()):
        raise HTTPException(409, "hay un job en curso; espera a que termine")
    explore.clear_session()
    return {"closed": True}


@fusion_router.post("/explore/reference")
async def explore_reference(body: ExploreRefBody):
    try:
        return {"session": explore.set_reference(body.gen_id)}
    except ExploreError as e:
        raise HTTPException(404, str(e))


@fusion_router.delete("/explore/generation/{gen_id}")
async def explore_generation_delete(gen_id: str):
    """Borra un trabajo concreto (imagen + entry) de la sesión."""
    if any(j["status"] == "running" for j in _jobs.values()):
        raise HTTPException(409, "hay un job en curso; espera a que termine")
    try:
        return {"session": explore.delete_generation(gen_id)}
    except ExploreError as e:
        raise HTTPException(404, str(e))


@fusion_router.post("/explore/clear")
async def explore_clear():
    """Vacía el historial de trabajos conservando la sesión y su config."""
    if any(j["status"] == "running" for j in _jobs.values()):
        raise HTTPException(409, "hay un job en curso; espera a que termine")
    try:
        return {"session": explore.clear_generations()}
    except ExploreError as e:
        raise HTTPException(404, str(e))


@fusion_router.get("/explore/image/{gen_id}")
async def explore_image(gen_id: str):
    try:
        return FileResponse(explore.image_path(gen_id))
    except ExploreError as e:
        raise HTTPException(404, str(e))


@fusion_router.get("/explore/base-image")
async def explore_base_image():
    """Imagen del checkpoint puro (sin LoRA) de la sesión activa, usada como
    comparación en el slider de la Referencia."""
    try:
        return FileResponse(explore.base_image_path())
    except ExploreError as e:
        raise HTTPException(404, str(e))


async def _explore_gen_job(job: dict, config: dict, config2: dict | None):
    def on_progress(step, total):
        job.update({"step": step, "steps_total": total})
    try:
        gen = await explore.generate(_comfy, config, on_progress=on_progress,
                                     config2=config2)
        job["status"] = "done"
        job["gen"] = gen
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)


@fusion_router.post("/explore/generate")
async def explore_generate(body: ExploreGenerateBody):
    session = explore.get_session()
    if not session:
        raise HTTPException(404, "no hay sesión de exploración activa")
    _reject_if_job_running()
    if not await _comfy.health_check():
        raise HTTPException(503, "ComfyUI no responde en :8188 — arráncalo desde el Hub")
    fuse = session.get("mode", "derive") == "fuse" and session.get("lora2")
    try:
        config = explore.normalize_config(body.config, session["arch"])
        config2 = (explore.normalize_config(body.config2, session["arch"])
                   if fuse and body.config2 is not None else None)
    except ExploreError as e:
        raise HTTPException(400, str(e))
    job_id = uuid.uuid4().hex[:12]
    job = {"id": job_id, "type": "explore", "status": "running",
           "step": 0, "steps_total": session["sampling"]["steps"],
           "gen": None, "error": None}
    _jobs[job_id] = job
    _spawn(_explore_gen_job(job, config, config2), job)
    return {"job_id": job_id}


async def _explore_draft_job(job: dict, body: ExploreSessionBody, ckpt: dict):
    def on_progress(step, total):
        job.update({"step": step, "steps_total": total})
    try:
        job["draft"] = await explore.generate_draft(
            _comfy, arch=body.arch, checkpoint=ckpt["name"],
            model=ckpt["unet_name"], lora=body.lora, strength=body.strength,
            prompt=body.prompt, negative=body.negative, seed=body.seed,
            sampling=body.sampling, models_root=_models_root(),
            mode=body.mode, lora2=body.lora2, strength2=body.strength2,
            on_progress=on_progress)
        job["status"] = "done"
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)


@fusion_router.post("/explore/draft")
async def explore_draft(body: ExploreSessionBody):
    """Calibración pre-lock: genera con las entradas dadas (LoRA a dosis 1.0
    en todos los bloques) SIN sesión y SIN guardar en el historial — la
    imagen sobrescribe draft.png en cada prueba. Permite afinar prompt y
    KSampler antes de hacer lock."""
    _reject_if_job_running()
    if not await _comfy.health_check():
        raise HTTPException(503, "ComfyUI no responde en :8188 — arráncalo desde el Hub")
    try:
        ckpt = _merger.get_checkpoint(body.checkpoint, body.arch)
        if not ckpt["present"]:
            raise MergeError(f"el checkpoint {body.checkpoint!r} no está en disco")
    except MergeError as e:
        raise HTTPException(400, str(e))
    job_id = uuid.uuid4().hex[:12]
    job = {"id": job_id, "type": "explore-draft", "status": "running",
           "step": 0, "steps_total": int((body.sampling or {}).get("steps") or 0),
           "draft": None, "error": None}
    _jobs[job_id] = job
    _spawn(_explore_draft_job(job, body, ckpt), job)
    return {"job_id": job_id}


@fusion_router.get("/explore/draft-image")
async def explore_draft_image():
    if not explore.DRAFT_FILE.is_file():
        raise HTTPException(404, "sin imagen de calibración")
    return FileResponse(explore.DRAFT_FILE)


async def _explore_confirm_job(job: dict, vs: ValidationSet, session: dict,
                               config: dict, label: str):
    def on_progress(p: dict):
        job.update(p)
    try:
        arch = session["arch"]
        adapter = get_adapter(arch)
        template = load_workflow(adapter.workflow_name("txt2img_lora_selective"),
                                 arch)
        template["10"]["inputs"].update(explore.node_inputs(
            config, arch, session.get("dose_exponent", 2)))
        lora_name = Path(session.get("lora_source") or session["lora"]).name
        desc = (f"{session['checkpoint']} + {lora_name} @ "
                f"{session['strength']} "
                f"[{explore.config_summary(config, arch)}]")
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


@fusion_router.post("/explore/confirm")
async def explore_confirm(body: ExploreConfirmBody):
    """Doble confirmación: regenera el set completo en runtime con la config
    de bloques de una generación de la sesión. El run SÍ se guarda (es
    evidencia de comparación); las imágenes de exploración no."""
    session = explore.get_session()
    if not session:
        raise HTTPException(404, "no hay sesión de exploración activa")
    if session.get("mode", "derive") == "fuse":
        raise HTTPException(400, "sesión en modo fusión de LoRAs: la "
                            "confirmación con set no encadena el LoRA B "
                            "todavía (llega con F3/F4)")
    gen = next((g for g in session["generations"] if g["id"] == body.gen_id), None)
    if gen is None:
        raise HTTPException(404, f"no existe la generación {body.gen_id!r}")
    vs = _vs_or_404(body.set_name)
    _reject_if_job_running()
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
    _spawn(_explore_confirm_job(job, vs, session,
                                gen["config"], body.label), job)
    return {"job_id": job_id}


@fusion_router.post("/explore/merge")
async def explore_merge(body: dict):
    """Bake final con la config de una generación de la sesión. Modo derive →
    checkpoint (base+LoRA, /merge). Modo fuse → LoRA fusionado A+B (F3). Misma
    matemática que el preview runtime (dosis lineal, sqrt en el nodo)."""
    session = explore.get_session()
    if not session:
        raise HTTPException(404, "no hay sesión de exploración activa")
    gen = next((g for g in session["generations"]
                if g["id"] == body.get("gen_id")), None)
    if gen is None:
        raise HTTPException(404, f"no existe la generación {body.get('gen_id')!r}")
    arch = session["arch"]
    name = str(body.get("name", ""))
    label = str(body.get("label", ""))

    if session.get("mode", "derive") == "fuse":
        # F3: producto = fichero LoRA (A⊕B). Config A y B → dosis por bloque.
        try:
            blocks_a = explore.config_to_merge_blocks(gen["config"], arch)
            blocks_b = explore.config_to_merge_blocks(
                gen.get("config2") or explore.full_config(arch), arch)
        except ExploreError as e:
            raise HTTPException(400, str(e))
        return await fuse_start(
            arch=arch,
            # linaje sobre los LoRA originales de la colección (no la copia alias)
            lora_a=session.get("lora_source") or session["lora"],
            lora_b=session.get("lora2_source") or session["lora2"],
            strength_a=session["strength"], strength_b=session["strength2"],
            name=name, label=label, blocks_a=blocks_a, blocks_b=blocks_b)

    try:
        blocks = explore.config_to_merge_blocks(gen["config"], arch)
    except ExploreError as e:
        raise HTTPException(400, str(e))
    return await merge_start(MergeBody(
        arch=arch, base=session["checkpoint"],
        # si la sesión corre sobre una copia alias (diffusers→kohya), el
        # linaje del derivado referencia el LoRA original de la colección
        lora=session.get("lora_source") or session["lora"],
        strength=session["strength"],
        name=name, label=label, blocks=blocks))


async def _fuse_job(job: dict, arch: str, lora_a: str, lora_b: str,
                    strength_a: float, strength_b: float, name: str,
                    label: str, blocks_a, blocks_b):
    def on_progress(p: dict):
        job.update(p)
    try:
        entry = await _merger.fuse_loras(
            arch=arch, lora_a=lora_a, lora_b=lora_b,
            strength_a=strength_a, strength_b=strength_b,
            name=name, label=label, blocks_a=blocks_a, blocks_b=blocks_b,
            on_progress=on_progress)
        job["status"] = "done"
        job["checkpoint"] = entry     # reutiliza el campo del polleo (name/size)
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)


async def fuse_start(*, arch: str, lora_a: str, lora_b: str,
                     strength_a: float, strength_b: float, name: str,
                     label: str, blocks_a, blocks_b):
    """Fusión A+B como job en background (CPU, proceso aparte). type="merge"
    para heredar la serialización y el no-cancelable del bake a fichero."""
    _reject_if_job_running()
    job_id = uuid.uuid4().hex[:12]
    job = {"id": job_id, "type": "merge", "status": "running", "fused": True,
           "name": name, "lora_a": lora_a, "lora_b": lora_b,
           "phase": "", "done": 0, "total": 1, "checkpoint": None, "error": None}
    if len(_jobs) >= _MAX_JOBS:
        for k in [k for k, j in _jobs.items() if j["status"] != "running"]:
            del _jobs[k]
    _jobs[job_id] = job
    _spawn(_fuse_job(job, arch, lora_a, lora_b, strength_a, strength_b,
                     name, label, blocks_a, blocks_b))
    return {"job_id": job_id}


# ── Runs e imágenes ────────────────────────────────────────────────────────

@fusion_router.get("/sets/{name}/runs")
async def runs_list(name: str):
    _vs_or_404(name)
    return {"runs": vsets.list_runs(name)}


@fusion_router.delete("/runs/{set_name}/{run_id}")
async def run_delete(set_name: str, run_id: str):
    if any(j["status"] == "running" and j["set"] == set_name
           for j in _jobs.values()):
        raise HTTPException(409, "hay una regeneración en curso sobre este set")
    try:
        vsets.delete_run(set_name, run_id)
    except ValidationSetError as e:
        raise HTTPException(404, str(e))
    return {"deleted": f"{set_name}/{run_id}"}


@fusion_router.get("/runs/{set_name}/{run_id}/{filename}")
async def run_image(set_name: str, run_id: str, filename: str):
    base = vsets.RUNS_DIR.resolve()
    path = (base / set_name / run_id / filename).resolve()
    if not path.is_relative_to(base) or not path.is_file():
        raise HTTPException(404, "imagen no encontrada")
    return FileResponse(path)


# ═══════════════════════════════════════════════════════════════════════════
# Fase 4 — Batería de prompts (config fija × N prompts sobre la sesión activa)
# ═══════════════════════════════════════════════════════════════════════════

class BatterySaveBody(BaseModel):
    id: str
    label: str = ""
    lora_type: str                # character | style | concept
    style: str                    # prosa | tags
    hint: str = ""
    prompts: list[dict]           # [{id, text, negative, seed}]


class BatteryRunBody(BaseModel):
    battery_id: str
    config: dict                  # config de bloques a estresar (fija)


@fusion_router.get("/batteries")
async def batteries_list():
    return {"batteries": battery.list_batteries()}


@fusion_router.get("/batteries/{bid}")
async def battery_get(bid: str):
    try:
        return battery.get_battery(bid)
    except BatteryError as e:
        raise HTTPException(404, str(e))


@fusion_router.post("/batteries")
async def battery_save(body: BatterySaveBody):
    try:
        return battery.save_battery(body.model_dump())
    except BatteryError as e:
        raise HTTPException(400, str(e))


@fusion_router.delete("/batteries/{bid}")
async def battery_delete(bid: str):
    try:
        battery.delete_battery(bid)
    except BatteryError as e:
        raise HTTPException(400, str(e))
    return {"deleted": bid}


async def _battery_run_job(job: dict, battery_id: str, config: dict):
    def on_progress(p: dict):
        job.update(p)
    try:
        result = await battery.run_battery(_comfy, battery_id, config,
                                           on_progress=on_progress)
        job["status"] = "done"
        job["result"] = result
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)


@fusion_router.post("/explore/battery")
async def explore_battery(body: BatteryRunBody):
    """Corre la batería (config fija × N prompts) contra la sesión activa. Cada
    prompt cae al historial como trabajo de batería. Job en background con
    progreso {item_index, total, prompt_id, step, steps_total}."""
    session = explore.get_session()
    if not session:
        raise HTTPException(404, "no hay sesión de exploración activa")
    _reject_if_job_running()
    if not await _comfy.health_check():
        raise HTTPException(503, "ComfyUI no responde en :8188 — arráncalo desde el Hub")
    try:
        bat = battery.get_battery(body.battery_id)
        config = explore.normalize_config(body.config, session["arch"])
    except BatteryError as e:
        raise HTTPException(404, str(e))
    except ExploreError as e:
        raise HTTPException(400, str(e))
    job_id = uuid.uuid4().hex[:12]
    job = {"id": job_id, "type": "battery", "status": "running",
           "battery": bat["id"], "label": bat["label"],
           "item_index": 0, "total": len(bat["prompts"]), "prompt_id": "",
           "step": 0, "steps_total": session["sampling"]["steps"],
           "result": None, "error": None}
    _jobs[job_id] = job
    _spawn(_battery_run_job(job, body.battery_id, config), job)
    return {"job_id": job_id}
