"""
MiniMax H3 API — rutas FastAPI del módulo (video + audio nativo, generación
local vía ComfyUI 0.33+, sin llamadas a la API cloud de MiniMax).

A diferencia de Painter/Ideogram, este módulo NO asume que sus modelos base
(~36 GB: DiT/encoder/VAEs/turbo LoRA) ya están en disco tras instalar el hub:
son demasiado grandes para bajarlos en una instalación fresca sin que el
cliente lo pida. `/models/status` + `/models/download` (SSE) implementan el
patrón "botón para descargar" — ver `core/model_downloader.py` para el porqué.
"""
import sys
import json
import uuid
import asyncio
import comfy_jobs
import threading
import subprocess
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

_HERE = Path(__file__).parent
_ROOT = _HERE.parent
_APPS = _ROOT / "apps"

# Import por paquete (minimax_h3.core.*), nunca módulos sueltos: painter/forge/
# ideogram también definen comfy_client.py y el primero en importarse gana el
# nombre en sys.modules (bug real documentado en forge_routes).
if str(_APPS) not in sys.path:
    sys.path.insert(0, str(_APPS))

from minimax_h3.core.comfy_client import ComfyClient
from minimax_h3.core import pipeline
from minimax_h3.core import model_downloader as dl
from minimax_h3.core import lora_presets
from minimax_h3.core import chains
from minimax_h3.core.pipeline import GenParams, PipelineError
from minimax_h3.core.chains import ChainError
from minimax_h3.core.lmstudio import LMStudio, LMStudioError
from minimax_h3.prompting.schema import (Mode, H3Prompt, H3RefPrompt, BASE_FIELDS,
                                         REF_FIELDS, effective_duration)
from minimax_h3.prompting.validator import Context, validate_text
from minimax_h3.prompting.writer import PromptWriter, WriterConfig, WriterError

minimax_h3_router = APIRouter(prefix="/api/minimax-h3", tags=["minimax-h3"])

_comfy = ComfyClient(port=8188)
_lm = LMStudio()


def _models_root() -> Path:
    """Almacén global de modelos (hub_config paths.models) — mismo contrato
    que forge_fusion/painter/ideogram."""
    cfg = json.loads((_ROOT / "hub" / "hub_config.json").read_text(encoding="utf-8"))
    return Path(cfg["paths"]["models"])


# Nodos nativos que el workflow necesita (diagnóstico de compatibilidad).
# Requieren ComfyUI 0.33+ (H3) y comfyui-tooling-nodes (ETN_LoadImageBase64,
# solo si se usa imagen inicial).
_REQUIRED_NODES = [
    "MiniMaxH3ImageToVideo", "UNETLoader", "CLIPLoader", "VAELoader",
    "VAEDecodeAudio", "CreateVideo", "SaveVideo", "RandomNoise",
    "KSamplerSelect", "BasicScheduler", "BasicGuider", "SamplerCustomAdvanced",
    "LoraLoaderModelOnly",
]


# ═══════════════════════════════════════════════════════════════════════════
# Diagnóstico
# ═══════════════════════════════════════════════════════════════════════════

@minimax_h3_router.get("/status")
async def status():
    comfy_up = await _comfy.health_check()
    nodes = {}
    if comfy_up:
        for n in _REQUIRED_NODES:
            nodes[n] = await _comfy.probe_node(n)
    return {
        "comfyui": comfy_up,
        "nodes": nodes,
        "nodes_ok": comfy_up and all(nodes.values() or [False]),
    }


@minimax_h3_router.get("/models/status")
async def models_status():
    root = _models_root()
    missing = dl.missing_specs(root, dl.GATED_MODELS)
    missing_loras = dl.missing_specs(root, dl.PRESERVED_LORAS)
    return {
        "ready": not missing,
        "missing": [{"key": s.key, "label": s.label, "size_bytes": s.size_bytes}
                    for s in missing],
        "total_missing_bytes": sum(s.size_bytes for s in missing),
        # Informativo: los LoRAs preservados se bajan una vez desde el hub (no
        # gated por el usuario), esto solo avisa si por lo que sea faltan.
        "loras_ready": not missing_loras,
    }


@minimax_h3_router.get("/models/status/ref2v")
async def models_status_ref2v():
    """Modelos del modo de inicio Ref2V (opcional — solo hace falta para
    arrancar una cadena con varias imágenes de referencia; el resto del
    módulo funciona sin esto)."""
    root = _models_root()
    missing = dl.missing_specs(root, dl.REF2V_MODELS)
    return {
        "ready": not missing,
        "missing": [{"key": s.key, "label": s.label, "size_bytes": s.size_bytes}
                    for s in missing],
        "total_missing_bytes": sum(s.size_bytes for s in missing),
        # La UI necesita los nombres reales para armar ref_start al crear una
        # cadena (RefStartBody.unet_name/turbo_lora_name) sin hardcodearlos.
        "names": {s.key: s.dest_name for s in dl.REF2V_MODELS},
    }


_download_lock = asyncio.Lock()


@minimax_h3_router.get("/models/download")
async def models_download():
    """SSE — descarga los modelos gated (DiT/encoder/VAEs/turbo LoRA) que
    falten. Disparado por el botón "Descargar modelos" de la UI."""
    return await _run_download(dl.GATED_MODELS)


@minimax_h3_router.get("/models/download/ref2v")
async def models_download_ref2v():
    """SSE — descarga los modelos del modo de inicio Ref2V (DiT + turbo
    propios; encoder/VAEs ya vienen de /models/download)."""
    return await _run_download(dl.REF2V_MODELS)


async def _run_download(specs: list) -> StreamingResponse:
    if _download_lock.locked():
        raise HTTPException(409, "ya hay una descarga en curso")

    loop = asyncio.get_event_loop()
    q: asyncio.Queue = asyncio.Queue()
    await _download_lock.acquire()

    def _worker():
        try:
            for ev in dl.download_specs(_models_root(), specs):
                asyncio.run_coroutine_threadsafe(q.put(ev), loop)
            asyncio.run_coroutine_threadsafe(q.put({"type": "all_done"}), loop)
        except Exception as e:
            asyncio.run_coroutine_threadsafe(q.put({"type": "error", "msg": str(e)}), loop)
        finally:
            loop.call_soon_threadsafe(_download_lock.release)

    threading.Thread(target=_worker, daemon=True).start()

    async def stream():
        try:
            while True:
                ev = await q.get()
                yield f"data: {json.dumps(ev)}\n\n"
                if ev.get("type") in ("all_done", "error"):
                    break
        finally:
            pass

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@minimax_h3_router.get("/catalog/{folder}")
async def catalog(folder: str):
    """Passthrough a ComfyUI /models/{folder} para poblar selectores de la UI
    (diffusion_models, text_encoders, vae), FILTRADO a los archivos de MiniMax
    H3 (petición del usuario, s98): las carpetas son compartidas con el resto
    del hub y listar decenas de checkpoints ajenos en el selector solo
    confunde. Todos los pesos H3 conocidos llevan "minimax_h3" en el nombre
    (incluido el encoder qwen3vl_32b_minimax_h3_*)."""
    if folder not in ("diffusion_models", "text_encoders", "vae"):
        raise HTTPException(404, "carpeta desconocida")
    names = await _comfy.get_models(folder)
    return [n for n in names if "minimax_h3" in n.lower()]


@minimax_h3_router.get("/loras")
async def loras():
    """LoRAs de la subcarpeta loras/h3/ (turbo + decensurado + las que el
    cliente agregue) — nunca hardcodeadas, mismo patrón que forge_fusion."""
    all_loras = await _comfy.get_models("loras")
    return [n for n in all_loras
            if "h3" in Path(n.replace("\\", "/")).parts]


@minimax_h3_router.get("/lora-presets")
async def lora_presets_route():
    """Presets curados de Hearmeman (trigger word, orden de apilado fijo, fuerza
    sugerida) filtrados a los archivos que realmente existen en disco, más los
    archivos de loras/h3/ que no pertenecen a ningún preset (LoRAs nuevas que
    el cliente agregue) para no perder la vía genérica."""
    all_loras = await _comfy.get_models("loras")
    return {
        "presets": lora_presets.presets_for_files(all_loras),
        "extra": lora_presets.preset_extra_files(all_loras),
    }


# ═══════════════════════════════════════════════════════════════════════════
# Generación
# ═══════════════════════════════════════════════════════════════════════════

class LoraItem(BaseModel):
    name: str
    strength: float = 1.0


class GenerateBody(BaseModel):
    prompt: str
    unet_name: str
    clip_name: str
    video_vae_name: str
    audio_vae_name: str
    first_frame_b64: str | None = None
    width: int = 1344
    height: int = 768
    duration_s: float = 5.0
    turbo: bool = False
    turbo_lora_name: str = ""
    turbo_strength: float = 1.0
    turbo_steps: int = 8
    steps: int = 20
    seed: int = -1
    loras: list[LoraItem] = []


_jobs: dict[str, dict] = {}
_MAX_JOBS = 30
_active_job: str | None = None

# asyncio solo guarda referencias débiles a los tasks: sin retenerlos aquí el
# GC puede matar un job en vuelo.
_bg_tasks: set[asyncio.Task] = set()


def _spawn(coro) -> asyncio.Task:
    task = comfy_jobs.spawn(coro)
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)
    return task


async def _free_llm_vram(job: dict):
    """H3 y el LLM no caben juntos en VRAM: antes de encolar se mira qué hay
    montado y se desmonta.

    Se pregunta primero en vez de disparar `lms unload --all` a ciegas: la
    consulta es un GET a :1234 y el unload arranca un proceso hijo, así que
    para quien nunca abrió el enriquecedor esto no cuesta nada. Después se
    comprueba que de verdad se soltó —un unload fallido es silencioso y el
    síntoma llegaría veinte minutos más tarde, como un OOM a mitad del render—
    y se le pide a ComfyUI que recalcule su presupuesto con la VRAM ya libre.

    `unload_models=False` a propósito: vaciar la caché sí, tirar el DiT de H3
    que quedó cargado del render anterior no — son ~36 GB de recarga.

    Todo best-effort: que LM Studio no esté levantado no es un error."""
    job["phase"] = "unload"
    try:
        loaded = await _lm.loaded_llms()
    except Exception:
        loaded = []
    if not loaded:
        return
    try:
        await _lm.unload_all()
    except Exception:
        pass
    try:
        still = await _lm.loaded_llms()
    except Exception:
        still = []
    job["llm_unloaded"] = [m for m in loaded if m not in still]
    if still:
        job["llm_still_loaded"] = still
    try:
        await _comfy.free_memory(unload_models=False, free_memory=True)
    except Exception:
        pass


async def _run_job(job: dict, params: GenParams):
    global _active_job

    def on_phase(name, extra):
        job["phase"] = name
        job.update(extra)

    def on_step(step, total):
        job["step"] = step
        job["steps_total"] = total

    try:
        await _free_llm_vram(job)
        manifest = await pipeline.generate(_comfy, params,
                                           on_phase=on_phase, on_step=on_step)
        job["status"] = "done"
        job["run_id"] = manifest["id"]
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)
    finally:
        _active_job = None


@minimax_h3_router.post("/generate")
@comfy_jobs.exclusive("MiniMax H3")
async def generate(body: GenerateBody):
    global _active_job
    if _active_job:
        raise HTTPException(409, "ya hay una generación en curso")
    if not body.prompt.strip():
        raise HTTPException(400, "hace falta un prompt")
    if body.turbo and not body.turbo_lora_name:
        raise HTTPException(400, "modo turbo activado sin turbo_lora_name")
    root = _models_root()
    missing = dl.missing_specs(root, dl.GATED_MODELS)
    if missing:
        raise HTTPException(409, "faltan modelos por descargar — usa el botón "
                                 "'Descargar modelos' antes de generar")

    params = GenParams(**body.model_dump())
    job_id = uuid.uuid4().hex[:12]
    job = {"id": job_id, "status": "running", "phase": "queued",
          "step": 0, "steps_total": body.turbo_steps if body.turbo else body.steps,
          "run_id": None, "error": None}
    if len(_jobs) >= _MAX_JOBS:
        for k in [k for k, j in _jobs.items() if j["status"] != "running"]:
            del _jobs[k]
    _jobs[job_id] = job
    _active_job = job_id
    _spawn(_run_job(job, params))
    return {"job_id": job_id}


@minimax_h3_router.get("/jobs/{job_id}")
async def job_get(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "job desconocido")
    return job


# ═══════════════════════════════════════════════════════════════════════════
# Cadenas (encadenado de tramos v2 — ver core/chains.py)
# ═══════════════════════════════════════════════════════════════════════════

class RefStartBody(BaseModel):
    """Si se manda, el tramo 1 se genera con Ref2V (varias imágenes de
    referencia) en vez de T2V/I2V — ver `Ref2VParams`/`chains.create_chain`."""
    unet_name: str
    turbo_lora_name: str
    turbo_strength: float = 1.0
    turbo_steps: int = 4
    ref_images_b64: list[str]


class ChainCreateBody(BaseModel):
    name: str = ""
    unet_name: str
    clip_name: str
    video_vae_name: str
    audio_vae_name: str
    width: int = 1344
    height: int = 768
    turbo: bool = False   # s96: sin turbo da calidad muy superior en fl2va INT8 — default off
    turbo_lora_name: str = ""
    turbo_strength: float = 1.0
    turbo_steps: int = 8
    steps: int = 20
    loras: list[LoraItem] = []
    first_frame_b64: str | None = None   # imagen inicial opcional del primer tramo (modo t2v_i2v)
    ref_start: RefStartBody | None = None   # si viene, el tramo 1 usa Ref2V en su lugar


class SegmentCreateBody(BaseModel):
    prompt: str
    duration_s: float = 5.0
    seed: int = -1


class AcceptBody(BaseModel):
    run_id: str


class SegmentGenerateBody(BaseModel):
    """Cambios de último minuto a prompt/duración/seed hechos en la tarjeta del
    tramo antes de pulsar 'Generar' — se persisten en el tramo y LUEGO se
    genera con esos valores (no con los que tenía el tramo al crearse)."""
    prompt: str | None = None
    duration_s: float | None = None
    seed: int | None = None


@minimax_h3_router.post("/chains")
async def chains_create(body: ChainCreateBody):
    d = body.model_dump()
    name = d.pop("name")
    ref_start = d.pop("ref_start")
    start = {"mode": "ref2v", **ref_start} if ref_start else {"mode": "t2v_i2v"}
    return chains.create_chain(name, d, start)


@minimax_h3_router.get("/chains")
async def chains_list():
    return {"chains": chains.list_chains()}


@minimax_h3_router.get("/chains/{chain_id}")
async def chains_get(chain_id: str):
    try:
        return chains.load_chain(chain_id)
    except ChainError as e:
        raise HTTPException(404, str(e))


@minimax_h3_router.delete("/chains/{chain_id}")
async def chains_delete(chain_id: str):
    chains.delete_chain(chain_id)
    return {"deleted": chain_id}


@minimax_h3_router.post("/chains/{chain_id}/segments")
async def segments_add(chain_id: str, body: SegmentCreateBody):
    try:
        return chains.add_segment(chain_id, body.prompt, body.duration_s, body.seed)
    except ChainError as e:
        raise HTTPException(409, str(e))


async def _run_chain_job(job: dict, chain_id: str, segment_id: str):
    global _active_job

    def on_phase(name, extra):
        job["phase"] = name
        job.update(extra)

    def on_step(step, total):
        job["step"] = step
        job["steps_total"] = total

    try:
        await _free_llm_vram(job)
        manifest = await chains.generate_segment_version(
            _comfy, chain_id, segment_id, on_phase=on_phase, on_step=on_step)
        job["status"] = "done"
        job["run_id"] = manifest["id"]
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)
    finally:
        _active_job = None


@minimax_h3_router.post("/chains/{chain_id}/segments/{segment_id}/generate")
@comfy_jobs.exclusive("MiniMax H3")
async def segment_generate(chain_id: str, segment_id: str, body: SegmentGenerateBody):
    global _active_job
    if _active_job:
        raise HTTPException(409, "ya hay una generación en curso")
    root = _models_root()
    missing = dl.missing_specs(root, dl.GATED_MODELS)
    if missing:
        raise HTTPException(409, "faltan modelos por descargar — usa el botón "
                                 "'Descargar modelos' antes de generar")
    try:
        chains.update_segment(chain_id, segment_id, prompt=body.prompt,
                              duration_s=body.duration_s, seed=body.seed)
    except ChainError as e:
        raise HTTPException(404, str(e))

    job_id = uuid.uuid4().hex[:12]
    job = {"id": job_id, "status": "running", "phase": "queued",
          "step": 0, "steps_total": 0, "run_id": None, "error": None}
    if len(_jobs) >= _MAX_JOBS:
        for k in [k for k, j in _jobs.items() if j["status"] != "running"]:
            del _jobs[k]
    _jobs[job_id] = job
    _active_job = job_id
    _spawn(_run_chain_job(job, chain_id, segment_id))
    return {"job_id": job_id}


@minimax_h3_router.post("/chains/{chain_id}/segments/{segment_id}/accept")
async def segment_accept(chain_id: str, segment_id: str, body: AcceptBody):
    try:
        chains.accept_version(chain_id, segment_id, body.run_id)
    except ChainError as e:
        raise HTTPException(409, str(e))
    return {"accepted": body.run_id}


@minimax_h3_router.delete("/chains/{chain_id}/segments/{segment_id}")
async def segment_delete(chain_id: str, segment_id: str):
    try:
        chains.delete_segment_cascade(chain_id, segment_id)
    except ChainError as e:
        raise HTTPException(404, str(e))
    return {"deleted": segment_id}


@minimax_h3_router.delete("/chains/{chain_id}/segments/{segment_id}/versions/{run_id}")
async def version_delete(chain_id: str, segment_id: str, run_id: str):
    try:
        chains.delete_version(chain_id, segment_id, run_id)
    except ChainError as e:
        raise HTTPException(409, str(e))
    return {"deleted": run_id}


@minimax_h3_router.post("/chains/{chain_id}/export")
async def chain_export(chain_id: str):
    try:
        run_id = chains.export_final(chain_id)
    except ChainError as e:
        raise HTTPException(409, str(e))
    return {"run_id": run_id}


# ═══════════════════════════════════════════════════════════════════════════
# Historial
# ═══════════════════════════════════════════════════════════════════════════
# Enriquecedor de prompts — repone H3-Context-IR, que MiniMax no liberó
#
# El prompt no se pide de una vez: una llamada por campo, cada una con solo su
# sección de la guía oficial (ver apps/minimax_h3/prompting/). Estas rutas son
# la piel de eso — las tres cajas de la UI mapean 1:1 con los tres campos, así
# que "enriquecer todo" y "reescribir solo la música" comparten motor.
#
# Enriquecer y renderizar NO pueden solaparse: el LLM y el DiT de H3 no caben
# juntos en VRAM. Por eso /prompt/* devuelve 409 con un render en curso, y el
# render descarga el LLM antes de encolar.
# ═══════════════════════════════════════════════════════════════════════════

# Orden de preferencia medido en tests/bench_prompting.py (15/15 prompts
# válidos, 11 al primer intento, 13.1 s/prompt el primero). Es una sugerencia
# para preseleccionar en la UI, nunca una imposición: el modelo lo elige el
# usuario y viaja en cada petición.
_LLM_PREFERENCE = ("gemma4-26b-a4b-uncensored", "gemma-4-12b", "gemma-4", "qwen3")


class PromptBody(BaseModel):
    idea: str = ""
    llm_model: str = ""
    mode: str = "T2VA"
    duration_s: float = 5.0
    dialogue: list[str] = []          # líneas del usuario, se reproducen verbatim
    silence: bool = False
    n_videos: int = 0
    n_audios: int = 0
    images_b64: list[str] = []        # referencias conectadas, para describirlas
    temperature: float = 0.4
    max_repairs: int = 2
    reasoning: str = "none"


class PromptRewriteBody(PromptBody):
    field: str = ""
    note: str = ""
    fields: dict[str, str] = {}


class PromptAssembleBody(BaseModel):
    mode: str = "T2VA"
    duration_s: float = 5.0
    dialogue: list[str] = []
    silence: bool = False
    n_videos: int = 0
    n_audios: int = 0
    n_pictures: int = 0
    fields: dict[str, str] = {}


def _prompt_mode(name: str) -> Mode:
    try:
        return Mode[name.strip().upper()]
    except KeyError:
        raise HTTPException(400, f"modo desconocido: {name!r}")


def _prompt_ctx(body, n_pictures: int) -> Context:
    return Context(duration_s=body.duration_s,
                   n_pictures=n_pictures,
                   n_videos=body.n_videos,
                   n_audios=body.n_audios,
                   dialogue=[d for d in body.dialogue if d.strip()],
                   silence_requested=body.silence)


def _prompt_writer(body: PromptBody) -> PromptWriter:
    if not body.llm_model.strip():
        raise HTTPException(400, "hace falta elegir un modelo de LM Studio")
    return PromptWriter(_lm, WriterConfig(model=body.llm_model,
                                          temperature=body.temperature,
                                          max_repairs=body.max_repairs,
                                          reasoning=body.reasoning))


def _prompt_build(mode: Mode, duration_s: float, fields: dict):
    """Campos sueltos (los que el usuario editó a mano) -> dataclass del modo."""
    if mode is Mode.REF2VA:
        return H3RefPrompt(duration_s=duration_s,
                           **{k: fields.get(k, "") for k in REF_FIELDS})
    return H3Prompt(mode=mode, duration_s=duration_s,
                    **{k: fields.get(k, "") for k in BASE_FIELDS})


def _prompt_payload(prompt, issues, steps=(), note: str = "") -> dict:
    names = REF_FIELDS if prompt.mode is Mode.REF2VA else BASE_FIELDS
    return {
        "mode": prompt.mode.name,
        "duration_s": prompt.duration_s,
        "effective_duration": round(effective_duration(prompt.duration_s), 2),
        "fields": {n: getattr(prompt, n) for n in names},
        "prompt": prompt.assemble(),
        "ok": not any(i.severity == "error" for i in issues),
        "issues": [{"code": i.code, "severity": i.severity, "field": i.field,
                    "message": i.message} for i in issues],
        "steps": [{"name": s.name, "seconds": round(s.seconds, 1), "chars": s.chars,
                   "repaired": s.repaired, "note": s.note} for s in steps],
        "seconds": round(sum(s.seconds for s in steps), 1),
        "repairs": sum(s.repaired for s in steps),
        "note": note,
    }


def _prompt_guard():
    """Con un render en curso no se enriquece: el LLM le quitaría la VRAM."""
    if _active_job:
        raise HTTPException(409, "hay una generación en curso — el LLM y H3 no caben "
                                 "juntos en VRAM; espera a que termine")


async def _free_comfy_vram():
    """Lo simétrico de `_free_llm_vram`: soltar lo que ComfyUI retiene antes de
    montar el escritor.

    No es cosmético. Medido en `tests/e2e_pipeline.py`: con el DiT de H3 todavía
    residente, el mismo enriquecido pasó de 20 s a 78 s porque el LLM no cupo en
    VRAM y se fue a RAM. Aquí sí se descargan los modelos (`unload_models=True`)
    —al revés que antes de renderizar—: recargar el DiT del NVMe cuesta bastante
    menos que escribir el prompt con el LLM a medio volcar."""
    try:
        await _comfy.free_memory(unload_models=True, free_memory=True)
    except Exception:
        pass


async def _is_vlm(model: str) -> bool:
    try:
        return any(m.get("id") == model and m.get("type") == "vlm"
                   for m in await _lm.list_models())
    except LMStudioError:
        return False


@minimax_h3_router.get("/prompt/models")
async def prompt_models():
    """LLMs cargables en LM Studio + cuál conviene preseleccionar."""
    llms, err = [], ""
    try:
        llms = [m for m in await _lm.list_models() if m.get("type") in ("llm", "vlm")]
    except LMStudioError as e:
        err = str(e)
    ids = [m.get("id", "") for m in llms]
    suggested = next((i for pat in _LLM_PREFERENCE for i in ids if pat in i.lower()), "")
    return {"llms": llms, "lm_error": err, "suggested": suggested}


@minimax_h3_router.post("/prompt/unload-llm")
@comfy_jobs.exclusive("MiniMax H3")
async def prompt_unload_llm():
    """Descarga manual del LLM de la VRAM (botón de la UI)."""
    try:
        return await _lm.unload_all()
    except Exception as e:
        raise HTTPException(500, str(e))


@minimax_h3_router.post("/prompt/enrich")
@comfy_jobs.exclusive("MiniMax H3")
async def prompt_enrich(body: PromptBody):
    """Idea suelta -> prompt H3 completo y validado."""
    _prompt_guard()
    if not body.idea.strip():
        raise HTTPException(400, "hace falta una idea")
    if not await _lm.health_check():
        raise HTTPException(503, "LM Studio no responde en :1234")

    mode = _prompt_mode(body.mode)
    ctx = _prompt_ctx(body, n_pictures=len(body.images_b64))
    writer = _prompt_writer(body)
    await _free_comfy_vram()

    # Describir las referencias exige un modelo multimodal. Si el elegido es de
    # solo texto no se aborta: se escribe sin captions y se avisa, porque el
    # prompt sale igual y la alternativa sería obligar a cargar dos modelos.
    note = ""
    images = body.images_b64
    if images and not await _is_vlm(body.llm_model):
        note = (f"'{body.llm_model}' no es multimodal: las referencias no se "
                f"describieron y el prompt se escribió solo desde la idea")
        images = []

    try:
        if mode is Mode.REF2VA:
            res = await writer.write_ref(body.idea, ctx, images_b64=images or None)
        else:
            captions = await writer.describe_references(images) if images else None
            res = await writer.write(body.idea, ctx, mode, captions=captions)
    except (WriterError, LMStudioError) as e:
        raise HTTPException(502, str(e))
    return _prompt_payload(res.prompt, res.issues, res.steps, note)


@minimax_h3_router.post("/prompt/rewrite")
@comfy_jobs.exclusive("MiniMax H3")
async def prompt_rewrite(body: PromptRewriteBody):
    """Reescribe UNA caja con la indicación del usuario, sin tocar las demás."""
    _prompt_guard()
    if not await _lm.health_check():
        raise HTTPException(503, "LM Studio no responde en :1234")

    mode = _prompt_mode(body.mode)
    ctx = _prompt_ctx(body, n_pictures=len(body.images_b64))
    prompt = _prompt_build(mode, body.duration_s, body.fields)
    writer = _prompt_writer(body)
    await _free_comfy_vram()
    try:
        res = await writer.rewrite_field(prompt, body.field, ctx,
                                         note=body.note, idea=body.idea)
    except WriterError as e:
        raise HTTPException(400, str(e))
    except LMStudioError as e:
        raise HTTPException(502, str(e))
    return _prompt_payload(res.prompt, res.issues, res.steps)


@minimax_h3_router.post("/prompt/assemble")
async def prompt_assemble(body: PromptAssembleBody):
    """Valida y ensambla lo que el usuario editó a mano. Sin LLM de por medio:
    es el mismo contrato que verifica el enriquecedor, para que editar una caja
    no pueda colar un prompt inválido."""
    mode = _prompt_mode(body.mode)
    ctx = _prompt_ctx(body, n_pictures=body.n_pictures)
    prompt = _prompt_build(mode, body.duration_s, body.fields)
    _, issues = validate_text(prompt.assemble(), mode, ctx)
    return _prompt_payload(prompt, issues)


# ═══════════════════════════════════════════════════════════════════════════

@minimax_h3_router.get("/history")
async def history():
    return {"runs": pipeline.list_history()}


@minimax_h3_router.get("/history/{run_id}/meta")
async def history_meta(run_id: str):
    try:
        return pipeline.history_meta(run_id)
    except PipelineError as e:
        raise HTTPException(404, str(e))


@minimax_h3_router.get("/history/{run_id}/video")
async def history_video(run_id: str):
    try:
        return FileResponse(pipeline.history_video(run_id))
    except PipelineError as e:
        raise HTTPException(404, str(e))


@minimax_h3_router.post("/history/{run_id}/reveal")
async def history_reveal(run_id: str):
    """Abre la carpeta contenedora del run en el explorador (hub local)."""
    try:
        d, video = pipeline.history_dir(run_id)
    except PipelineError as e:
        raise HTTPException(404, str(e))
    try:
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", str(video)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(video)])
        else:
            subprocess.Popen(["xdg-open", str(d)])
    except Exception as e:
        raise HTTPException(500, f"no se pudo abrir la carpeta: {e}")
    return {"opened": str(d)}


@minimax_h3_router.delete("/history/{run_id}")
async def history_delete(run_id: str):
    try:
        pipeline.delete_run(run_id)
    except PipelineError as e:
        raise HTTPException(404, str(e))
