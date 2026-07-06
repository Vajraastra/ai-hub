"""
Ideogram 4 API — rutas FastAPI del módulo.

Flujo: descripción libre → caption JSON (LM Studio, structured output) →
descarga del LLM de la VRAM → render con DualModelGuider (cond + incond) →
BlockGuard. Vía principal: JSON + bounding boxes (evita el filtro y usa el
modelo como fue diseñado). Los nodos son nativos de ComfyUI 0.27+ (sin customs).
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
_APPS = _ROOT / "apps"

# Import por paquete (ideogram.core.*), nunca módulos sueltos: painter/forge
# también definen comfy_client.py y el primero en importarse gana el nombre en
# sys.modules (bug real documentado en forge_routes).
if str(_APPS) not in sys.path:
    sys.path.insert(0, str(_APPS))

from ideogram.core.comfy_client import ComfyClient
from ideogram.core.lmstudio import LMStudio, LMStudioError
from ideogram.core import caption as cap
from ideogram.core import pipeline
from ideogram.core import loras as lora_cat
from ideogram.core.pipeline import GenParams, PipelineError

ideogram_router = APIRouter(prefix="/api/ideogram", tags=["ideogram"])

_comfy = ComfyClient(port=8188)
_lm = LMStudio()


def _models_root() -> Path:
    """Almacén global de modelos (hub_config paths.models). Los LoRAs viven
    bajo <models>/loras (mismo contrato que forge_lab/painter)."""
    cfg = json.loads((_ROOT / "hub" / "hub_config.json").read_text(encoding="utf-8"))
    return Path(cfg["paths"]["models"])

# Nodos nativos que el workflow necesita (diagnóstico de compatibilidad).
_REQUIRED_NODES = [
    "DualModelGuider", "Ideogram4Scheduler", "CLIPLoader", "CLIPTextEncode",
    "UNETLoader", "ConditioningZeroOut", "EmptyFlux2LatentImage",
    "SamplerCustomAdvanced", "VAELoader", "LoraLoaderModelOnly", "BasicGuider",
]

_SAMPLERS = ["euler", "euler_ancestral", "dpmpp_2m", "dpmpp_2m_sde", "dpmpp_sde",
             "ddim", "lcm", "res_multistep"]

# Ideogram 4 exige un encoder Qwen3-VL (el CLIPLoader usa type:"ideogram4") y el
# VAE de Flux2 (16 canales). Cualquier otro encoder/VAE hace que un nodo de
# ComfyUI reviente al desempaquetar shapes distintos ("expected 4, got 1" en
# CLIPTextEncode con gemma4/t5, o mismatch de canales en el VAE con ae/flux1).
# Filtramos los catálogos a lo compatible para que la UI no pueda ofrecer trampas.
_CLIP_OK = ("qwen3vl",)
_VAE_OK = ("flux2",)


def _compat(items: list[str], needles: tuple[str, ...]) -> list[str]:
    """Filtra por subcadena (case-insensitive). Si nada casa, devuelve la lista
    completa como fallback para no dejar el desplegable vacío en instalaciones
    con nombres inesperados."""
    hit = [m for m in items if any(n in m.lower() for n in needles)]
    return hit or items


# ═══════════════════════════════════════════════════════════════════════════
# Diagnóstico / catálogos
# ═══════════════════════════════════════════════════════════════════════════

@ideogram_router.get("/status")
async def status():
    comfy_up = await _comfy.health_check()
    nodes = {}
    if comfy_up:
        for n in _REQUIRED_NODES:
            nodes[n] = await _comfy.probe_node(n)

    lm_up = await _lm.health_check()
    return {
        "comfyui": comfy_up,
        "lmstudio": lm_up,
        "nodes": nodes,
        "nodes_ok": comfy_up and all(nodes.values() or [False]),
    }


@ideogram_router.get("/models")
async def models():
    """Catálogos para poblar los selectores de la UI."""
    diffusion = await _comfy.get_models("diffusion_models")
    vae = await _comfy.get_models("vae")

    # Los text encoders pueden estar mapeados bajo 'text_encoders' o 'clip'
    # (extra_model_paths mapea la carpeta física text_encoders bajo clip).
    # Se combinan ambas listas y se priorizan los encoders de Ideogram.
    te_a = await _comfy.get_models("text_encoders")
    te_b = await _comfy.get_models("clip")
    seen, text_encoders = set(), []
    for m in (te_a or []) + (te_b or []):
        if m not in seen:
            seen.add(m); text_encoders.append(m)
    # Solo encoders compatibles con Ideogram 4 (Qwen3-VL). Antes se listaban los
    # 15 encoders del sistema (t5, clip_l, gemma4…) y el 1º del orden alfabético
    # era gemma4 → CLIPTextEncode reventaba con "expected 4, got 1".
    text_encoders = _compat(text_encoders, _CLIP_OK)
    _te_pat = ("qwen3vl", "ideogram")
    text_encoders.sort(key=lambda m: (not any(p in m.lower() for p in _te_pat), m.lower()))

    # Solo VAEs de Flux2 (16 canales). Un ae/flux1 (4 canales) daría un mismatch
    # de shape análogo dentro del VAELoader/decode.
    vae = _compat(vae, _VAE_OK)

    # partir difusión en condicional / incondicional por el nombre
    cond = [m for m in diffusion if "unconditional" not in m.lower() and "ideogram" in m.lower()]
    uncond = [m for m in diffusion if "unconditional" in m.lower()]

    llms = []
    lm_error = ""
    try:
        llms = [m for m in await _lm.list_models() if m.get("type") in ("llm", "vlm")]
    except LMStudioError as e:
        lm_error = str(e)

    return {
        "diffusion_all": diffusion,
        "cond": cond or diffusion,
        "uncond": uncond,
        "text_encoders": text_encoders,
        "vae": vae,
        "samplers": _SAMPLERS,
        "llms": llms,
        "lm_error": lm_error,
    }


@ideogram_router.get("/loras")
async def loras(show_all: bool = False):
    """Catálogo de LoRAs del almacén global con detección de arquitectura
    Ideogram 4 (header del safetensors). Por defecto solo compatibles;
    show_all=true muestra todos (escape si la detección no reconoce alguno)."""
    try:
        return {"loras": lora_cat.list_loras(_models_root(), show_all=show_all)}
    except Exception as e:
        raise HTTPException(500, f"no se pudo leer el almacén de LoRAs: {e}")


@ideogram_router.get("/lora-preview")
async def lora_preview(file: str):
    """Imagen .preview.* junto al safetensors (convención del Model Vault)."""
    try:
        return FileResponse(lora_cat.preview_path(_models_root(), file))
    except FileNotFoundError:
        raise HTTPException(404, "sin preview")


@ideogram_router.post("/unload")
async def unload_llm():
    """Descarga manual del LLM de la VRAM (botón de la UI)."""
    try:
        return await _lm.unload_all()
    except Exception as e:
        raise HTTPException(500, str(e))


# ═══════════════════════════════════════════════════════════════════════════
# Caption (solo el JSON, para previsualizar/editar antes de renderizar)
# ═══════════════════════════════════════════════════════════════════════════

class CaptionBody(BaseModel):
    description: str
    llm_model: str
    width: int = 2048
    height: int = 2048
    temperature: float = 0.6


@ideogram_router.post("/caption")
async def make_caption(body: CaptionBody):
    if not body.description.strip():
        raise HTTPException(400, "descripción vacía")
    if not await _lm.health_check():
        raise HTTPException(503, "LM Studio no responde en :1234")
    try:
        messages = cap.build_messages(body.description, body.width, body.height)
        raw = await _lm.chat_json(body.llm_model, messages, cap.IDEOGRAM_JSON_SCHEMA,
                                  temperature=body.temperature)
        if "__raw__" in raw:
            raw = cap.parse_llm_output(raw["__raw__"])
        caption = cap.validate_and_clean(raw)
    except (LMStudioError, cap.CaptionError) as e:
        raise HTTPException(502, f"fallo generando el caption: {e}")
    return {"caption": caption, "prompt": cap.to_prompt_string(caption)}


class AssembleBody(BaseModel):
    caption: dict            # borrador manual del usuario
    general: str = ""        # prompt general → high_level_description (fallback)


@ideogram_router.post("/assemble")
async def assemble_caption(body: AssembleBody):
    """MODO MANUAL SIN LLM: organiza/valida el borrador en el schema de Ideogram
    respetando los textos del usuario al pie de la letra. Solo estructura: recorta
    bboxes al rango, degrada 'text' vacío a 'obj', limpia paletas. No toca el LLM."""
    try:
        caption = cap.validate_and_clean(body.caption)
    except cap.CaptionError as e:
        raise HTTPException(400, f"borrador inválido (¿dibujaste alguna caja?): {e}")
    if body.general.strip() and not str(caption.get("high_level_description", "")).strip():
        caption["high_level_description"] = body.general.strip()
    return {"caption": caption, "prompt": cap.to_prompt_string(caption)}


class RefineBody(BaseModel):
    caption: dict            # borrador manual (cajas ya colocadas por el usuario)
    llm_model: str
    general: str = ""        # prompt general opcional (refuerza high_level_description)
    width: int = 2048
    height: int = 2048
    temperature: float = 0.6


@ideogram_router.post("/refine")
async def refine_caption(body: RefineBody):
    """MODO MANUAL: el usuario arma las cajas a mano y el LLM completa el resto
    (estilo/fondo/paletas/resumen) SIN tocar su composición."""
    try:
        manual = cap.validate_and_clean(body.caption)   # limpia/valida la geometría del usuario
    except cap.CaptionError as e:
        raise HTTPException(400, f"borrador manual inválido (¿dibujaste alguna caja?): {e}")
    if not await _lm.health_check():
        raise HTTPException(503, "LM Studio no responde en :1234")
    try:
        messages = cap.build_refine_messages(manual, body.general, body.width, body.height)
        raw = await _lm.chat_json(body.llm_model, messages, cap.IDEOGRAM_JSON_SCHEMA,
                                  temperature=body.temperature)
        if "__raw__" in raw:
            raw = cap.parse_llm_output(raw["__raw__"])
        refined = cap.validate_and_clean(raw)
        merged = cap.preserve_geometry(manual, refined, body.general)
    except (LMStudioError, cap.CaptionError) as e:
        raise HTTPException(502, f"fallo configurando el caption: {e}")
    return {"caption": merged, "prompt": cap.to_prompt_string(merged)}


class TranslateBody(BaseModel):
    caption: dict            # borrador manual (cajas ya colocadas por el usuario)
    llm_model: str
    general: str = ""        # prompt general opcional (se traduce hacia high_level)
    width: int = 2048
    height: int = 2048
    temperature: float = 0.3   # baja: traducción fiel, no creativa


@ideogram_router.post("/translate")
async def translate_caption(body: TranslateBody):
    """MODO MANUAL: traduce a inglés y corrige el texto del borrador (desc de cada
    caja, estilo, fondo, resumen y rótulos) SIN tocar la composición ni inflar. El
    resultado cae en el editor y el usuario puede ajustarlo antes de generar."""
    try:
        manual = cap.validate_and_clean(body.caption)   # limpia/valida la geometría del usuario
    except cap.CaptionError as e:
        raise HTTPException(400, f"borrador manual inválido (¿dibujaste alguna caja?): {e}")
    if not await _lm.health_check():
        raise HTTPException(503, "LM Studio no responde en :1234")
    try:
        messages = cap.build_translate_messages(manual, body.general, body.width, body.height)
        raw = await _lm.chat_json(body.llm_model, messages, cap.IDEOGRAM_JSON_SCHEMA,
                                  temperature=body.temperature)
        if "__raw__" in raw:
            raw = cap.parse_llm_output(raw["__raw__"])
        translated = cap.validate_and_clean(raw)
        merged = cap.preserve_geometry(manual, translated, body.general, translate_text=True)
    except (LMStudioError, cap.CaptionError) as e:
        raise HTTPException(502, f"fallo traduciendo el caption: {e}")
    return {"caption": merged, "prompt": cap.to_prompt_string(merged)}


# ═══════════════════════════════════════════════════════════════════════════
# Generación (pipeline completo como job en background)
# ═══════════════════════════════════════════════════════════════════════════

class LoraItem(BaseModel):
    name: str
    strength: float = 1.0
    target: str = "both"      # "both" | "cond" | "uncond"


class GenerateBody(BaseModel):
    description: str = ""
    json_prompt: str = ""
    llm_model: str = ""
    temperature: float = 0.6
    unet_cond: str
    unet_uncond: str = ""
    clip_name: str
    vae_name: str
    width: int = 2048
    height: int = 2048
    steps: int = 20
    mu: float = 0.5
    std: float = 1.75
    cfg: float = 3.0
    sampler: str = "euler"
    seed: int = -1
    manage_vram: bool = True
    # Anti-bloqueo (experimental): perturba el arranque del sampleo para escapar
    # del cuadro gris del filtro. Off por defecto — no altera el flujo normal.
    bypass_enabled: bool = False
    bypass_method: str = "ruido"          # "ruido" (ModelNoiseScale) | "sigma" (SetFirstSigma)
    bypass_noise_scale: float = 2.0
    bypass_first_sigma: float = 1.005      # natural ≈0.99988; +0.005 = empujón anti-bloqueo
    bypass_split: bool = False             # split-sigmas: perturbar solo el arranque, reconstruir limpio
    bypass_split_step: int = 2             # corte del schedule (pasos del tramo perturbado); útil 1–4
    loras: list[LoraItem] = []             # LoRAs a montar sobre los UNET (orden = orden de aplicación)
    turbo: bool = False                    # modo turbo: ~2 pasos, sin CFG, sin modelo incondicional


_jobs: dict[str, dict] = {}
_MAX_JOBS = 30


async def _run_job(job: dict, params: GenParams):
    def on_phase(name, extra):
        job["phase"] = name
        job.update(extra)

    def on_step(step, total):
        job["step"] = step
        job["steps_total"] = total

    try:
        manifest = await pipeline.generate(_comfy, _lm, params,
                                           on_phase=on_phase, on_step=on_step)
        job["status"] = "done"
        job["run_id"] = manifest["id"]
        job["blocked"] = manifest["blocked"]
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)


@ideogram_router.post("/generate")
async def generate(body: GenerateBody):
    if any(j["status"] == "running" for j in _jobs.values()):
        raise HTTPException(409, "ya hay una generación en curso")
    if not body.description.strip() and not body.json_prompt.strip():
        raise HTTPException(400, "hace falta una descripción o un JSON de prompt")
    # El modo turbo bypasea el modelo incondicional; en el resto es obligatorio.
    if not body.turbo and not body.unet_uncond:
        raise HTTPException(400, "falta el modelo incondicional (unet_uncond)")
    # Guard de compatibilidad: rechazar encoder/VAE incompatibles con un mensaje
    # claro en vez de dejar que un nodo de ComfyUI reviente a mitad de render.
    if not any(n in body.clip_name.lower() for n in _CLIP_OK):
        raise HTTPException(400, f"CLIP '{body.clip_name}' incompatible con Ideogram 4; "
                                 f"usa un encoder Qwen3-VL (type:ideogram4).")
    if not any(n in body.vae_name.lower() for n in _VAE_OK):
        raise HTTPException(400, f"VAE '{body.vae_name}' incompatible con Ideogram 4; "
                                 f"usa el VAE de Flux2 (16 canales).")

    params = GenParams(**body.model_dump())
    job_id = uuid.uuid4().hex[:12]
    job = {"id": job_id, "status": "running", "phase": "queued",
           "step": 0, "steps_total": body.steps, "run_id": None,
           "blocked": False, "error": None}
    if len(_jobs) >= _MAX_JOBS:
        for k in [k for k, j in _jobs.items() if j["status"] != "running"]:
            del _jobs[k]
    _jobs[job_id] = job
    asyncio.create_task(_run_job(job, params))
    return {"job_id": job_id}


@ideogram_router.get("/jobs/{job_id}")
async def job_get(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "job desconocido")
    return job


# ═══════════════════════════════════════════════════════════════════════════
# Historial
# ═══════════════════════════════════════════════════════════════════════════

@ideogram_router.get("/history")
async def history():
    return {"runs": pipeline.list_history()}


@ideogram_router.get("/history/{run_id}/meta")
async def history_meta(run_id: str):
    try:
        return pipeline.history_meta(run_id)
    except PipelineError as e:
        raise HTTPException(404, str(e))


@ideogram_router.get("/history/{run_id}/image")
async def history_image(run_id: str):
    try:
        return FileResponse(pipeline.history_image(run_id))
    except PipelineError as e:
        raise HTTPException(404, str(e))


@ideogram_router.delete("/history/{run_id}")
async def history_delete(run_id: str):
    try:
        pipeline.delete_run(run_id)
    except PipelineError as e:
        raise HTTPException(404, str(e))
    return {"deleted": run_id}
