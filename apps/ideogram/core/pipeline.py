"""
Orquestación de una generación Ideogram 4 de principio a fin.

Fases (se reportan por callback para el seguimiento del job):
  1. caption  — descripción libre → caption JSON (LM Studio, structured output).
                Se omite si el usuario ya pasa un JSON hecho a mano.
  2. unload   — descargar el LLM de la VRAM (lms unload) + pedir a ComfyUI que
                libere memoria, para no solapar LLM e Ideogram en 16 GB.
  3. render   — workflow ComfyUI con DualModelGuider (cond + incond).
  4. check    — BlockGuard: ¿la imagen es el banner de bloqueo?
  5. done     — guardar PNG + metadata en data/history/<id>/.

El LLM se recarga solo (JIT) en la siguiente generación con descripción.
"""
import io
import os
import json
import time
import random
import asyncio
from pathlib import Path
from dataclasses import dataclass, field, asdict

from . import caption as cap
from . import blockguard
from .comfy_client import ComfyClient, load_workflow
from .lmstudio import LMStudio

_DATA = Path(__file__).parent.parent / "data"
HISTORY_DIR = _DATA / "history"
_WORKFLOW = "ideogram4_t2i.json"

MAX_SEED = 0xFFFFFFFFFFFFFFFF


class PipelineError(Exception):
    pass


@dataclass
class GenParams:
    # entrada: una de las dos
    description: str = ""          # texto libre → el LLM hace el JSON
    json_prompt: str = ""         # JSON ya hecho (salta el paso LLM)
    # LLM
    llm_model: str = ""
    temperature: float = 0.6
    # modelos de render (nombres tal como los ve ComfyUI)
    unet_cond: str = ""
    unet_uncond: str = ""
    clip_name: str = ""
    vae_name: str = ""
    # sampling
    width: int = 2048
    height: int = 2048
    steps: int = 20
    mu: float = 0.5
    std: float = 1.75
    cfg: float = 3.0
    sampler: str = "euler"
    seed: int = -1
    # anti-bloqueo (experimental): perturba el arranque del sampleo
    bypass_enabled: bool = False
    bypass_method: str = "ruido"      # "ruido" | "sigma"
    bypass_noise_scale: float = 2.0
    bypass_first_sigma: float = 1.005     # natural ≈0.99988; +0.005 = empujón anti-bloqueo
    bypass_split: bool = False            # split-sigmas: perturbar solo el arranque, reconstruir limpio
    bypass_split_step: int = 2            # corte del schedule (pasos del tramo perturbado); útil 1–4
    # LoRAs: cada dict {name, strength, target} — target: "both" | "cond" | "uncond"
    loras: list = field(default_factory=list)
    # modo turbo: LoRA de destilación → ~2 pasos, sin CFG, sin modelo incondicional
    turbo: bool = False
    # orquestación
    manage_vram: bool = True


def _resolve_seed(seed: int) -> int:
    return random.randint(0, MAX_SEED) if seed is None or seed < 0 else int(seed)


def _apply_loras(wf: dict, params: GenParams, uncond: bool = True) -> tuple[list, list]:
    """Encadena LoraLoaderModelOnly (nodo NATIVO) sobre los UNET y devuelve las
    refs finales (cond, uncond) para que las use el resto del grafo.

    Cada LoRA es {name, strength, target}: target "both" aplica a los dos modelos,
    "cond"/"uncond" solo a uno. El delta del LoRA va en fp16 en el forward, así que
    funciona sobre los UNET cuantizados (int8/nvfp4). Reconecta el DualModelGuider
    "6" a las refs finales; No-op (refs = loaders crudos) si no hay LoRAs.

    En modo turbo (`uncond=False`) NO hay modelo incondicional: todos los LoRAs se
    montan sobre cond y no se toca `model_negative` (el guider se reemplaza luego).

    IDs 30+ para no chocar con el template (1–14) ni con los del anti-bloqueo (20–28).
    """
    cond_ref, uncond_ref = ["1", 0], ["2", 0]
    nid = 30
    for lora in (params.loras or []):
        name = str(lora.get("name") or "").strip()
        if not name:
            continue
        strength = float(lora.get("strength", 1.0))
        target = str(lora.get("target") or "both").lower()
        if target not in ("both", "cond", "uncond"):
            raise PipelineError(f"target de LoRA desconocido: {target!r}")
        # ComfyUI lista los LoRAs con el separador del SO (backslash en Windows);
        # el catálogo los expone en posix, así que se normaliza aquí.
        lora_name = name.replace("/", os.sep)
        # Sin incondicional (turbo) todo va a cond, ignorando el target.
        to_cond = (not uncond) or target in ("both", "cond")
        to_uncond = uncond and target in ("both", "uncond")
        if to_cond:
            wf[str(nid)] = {"class_type": "LoraLoaderModelOnly",
                            "inputs": {"model": cond_ref, "lora_name": lora_name,
                                       "strength_model": strength}}
            cond_ref = [str(nid), 0]; nid += 1
        if to_uncond:
            wf[str(nid)] = {"class_type": "LoraLoaderModelOnly",
                            "inputs": {"model": uncond_ref, "lora_name": lora_name,
                                       "strength_model": strength}}
            uncond_ref = [str(nid), 0]; nid += 1
    wf["6"]["inputs"]["model"] = cond_ref
    if uncond:
        wf["6"]["inputs"]["model_negative"] = uncond_ref
    return cond_ref, uncond_ref


def _apply_turbo(wf: dict, cond_ref: list) -> dict:
    """Modo turbo (LoRA de destilación, p.ej. Ideogram 4 TurboTime): el LoRA
    internaliza el guidance, así que se genera en ~2 pasos SIN CFG y SIN modelo
    incondicional (confirmado por el autor). Reemplaza el DualModelGuider por
    BasicGuider (solo cond, sin negative) y descarta el UNET incondicional ("2")
    y su ConditioningZeroOut ("5"). El turbo LoRA se monta como cualquier otro,
    sobre cond, vía _apply_loras(uncond=False)."""
    wf["6"] = {"class_type": "BasicGuider",
               "inputs": {"model": cond_ref, "conditioning": ["4", 0]}}
    wf.pop("2", None)   # UNETLoader incondicional (gemelo) — ya no se necesita
    wf.pop("5", None)   # ConditioningZeroOut (negative del guidance)
    return {"turbo": True}


def _apply_bypass(wf: dict, params: GenParams,
                  cond_ref: list, uncond_ref: list) -> dict:
    """Parchea el workflow (nodos NATIVOS, sin customs) para perturbar el arranque
    del sampleo y escapar del cuadro gris del filtro. `cond_ref`/`uncond_ref` son
    las refs de modelo que salen de `_apply_loras` (loaders crudos si no hay LoRAs),
    para que la perturbación se aplique DESPUÉS del LoRA sin pisarlo. Dos técnicas:

    - "ruido": envuelve ambos UNET con ModelNoiseScale(noise_scale) antes del
      DualModelGuider → amplifica el ruido (Method 2 del research, ×2 por defecto).
    - "sigma": inserta SetFirstSigma(sigma) entre el Ideogram4Scheduler y el
      SamplerCustomAdvanced → fija el primer sigma (Method 1; es absoluto, no un
      +delta, porque ComfyUI nativo no tiene un nodo de suma sobre sigmas).

    Con `bypass_split` (opcional, OFF por defecto) la perturbación se aplica SOLO
    al tramo de arranque: SplitSigmas corta el schedule en el paso `bypass_split_step`,
    un primer SamplerCustomAdvanced corre ese tramo perturbado (escapa el filtro) y
    un segundo sampler corre el resto con el guider LIMPIO y los sigmas naturales
    (reconstruye la anatomía que la perturbación rompía). Sin split se mantiene el
    comportamiento clásico: la perturbación afecta a todo el sampleo.

    Devuelve la info aplicada (para el manifest). No-op si bypass_enabled=False.
    """
    if not params.bypass_enabled:
        return {}

    method = params.bypass_method
    if method not in ("ruido", "sigma"):
        raise PipelineError(f"método de anti-bloqueo desconocido: {method!r}")

    split = bool(params.bypass_split)
    info: dict = {"method": method, "split": split}

    # Sigmas de arranque: todo el schedule ("7") o solo el tramo alto tras cortar.
    if split:
        step = max(1, int(params.bypass_split_step))
        wf["22"] = {"class_type": "SplitSigmas",
                    "inputs": {"sigmas": ["7", 0], "step": step}}
        high_sigmas, low_sigmas = ["22", 0], ["22", 1]
        info["split_step"] = step
    else:
        high_sigmas = ["7", 0]

    # Construye guider y sigmas del tramo perturbado según el método.
    if method == "ruido":
        scale = float(params.bypass_noise_scale)
        wf["20"] = {"class_type": "ModelNoiseScale",
                    "inputs": {"model": cond_ref, "noise_scale": scale}}
        wf["21"] = {"class_type": "ModelNoiseScale",
                    "inputs": {"model": uncond_ref, "noise_scale": scale}}
        info["noise_scale"] = scale
        pert_sigmas = high_sigmas
        if split:
            # Guider perturbado aparte, para no contaminar el limpio "6" del 2º tramo.
            wf["25"] = {"class_type": "DualModelGuider",
                        "inputs": {"model": ["20", 0], "model_negative": ["21", 0],
                                   "positive": ["4", 0], "negative": ["5", 0],
                                   "cfg": wf["6"]["inputs"]["cfg"]}}
            pert_guider = ["25", 0]
        else:
            wf["6"]["inputs"]["model"] = ["20", 0]
            wf["6"]["inputs"]["model_negative"] = ["21", 0]
            pert_guider = ["6", 0]
    else:  # sigma
        val = float(params.bypass_first_sigma)
        wf["23"] = {"class_type": "SetFirstSigma",
                    "inputs": {"sigmas": high_sigmas, "sigma": val}}
        pert_sigmas, pert_guider = ["23", 0], ["6", 0]
        info["first_sigma"] = val

    if not split:
        # Clásico: un solo sampler con la perturbación en todo el sampleo.
        wf["11"]["inputs"]["guider"] = pert_guider
        wf["11"]["inputs"]["sigmas"] = pert_sigmas
        return info

    # Split: dos samplers encadenados.
    # Tramo 1 — arranque perturbado (escapa el filtro), añade el ruido inicial real.
    wf["26"] = {"class_type": "SamplerCustomAdvanced",
                "inputs": {"noise": ["9", 0], "guider": pert_guider,
                           "sampler": ["8", 0], "sigmas": pert_sigmas,
                           "latent_image": ["10", 0]}}
    # Tramo 2 — reconstrucción LIMPIA: guider "6" sin perturbar, sigmas bajos, sin
    # re-inyectar ruido (continúa desde el latente del tramo 1).
    wf["27"] = {"class_type": "DisableNoise", "inputs": {}}
    wf["28"] = {"class_type": "SamplerCustomAdvanced",
                "inputs": {"noise": ["27", 0], "guider": ["6", 0],
                           "sampler": ["8", 0], "sigmas": low_sigmas,
                           "latent_image": ["26", 0]}}
    wf["13"]["inputs"]["samples"] = ["28", 0]
    return info


async def build_caption(lm: LMStudio, params: GenParams,
                        on_progress=None) -> tuple[dict, str]:
    """Devuelve (caption_dict, prompt_string). Usa el JSON del usuario si lo hay;
    si no, se lo pide al LLM con structured output y lo valida."""
    if params.json_prompt.strip():
        obj = cap.parse_llm_output(params.json_prompt)
        caption = cap.validate_and_clean(obj)
        return caption, cap.to_prompt_string(caption)

    if not params.description.strip():
        raise PipelineError("hace falta una descripción o un JSON de prompt")
    if not params.llm_model:
        raise PipelineError("no se indicó modelo LLM para generar el JSON")

    messages = cap.build_messages(params.description, params.width, params.height)
    raw = await lm.chat_json(params.llm_model, messages, cap.IDEOGRAM_JSON_SCHEMA,
                             temperature=params.temperature)
    if "__raw__" in raw:
        raw = cap.parse_llm_output(raw["__raw__"])
    caption = cap.validate_and_clean(raw)
    return caption, cap.to_prompt_string(caption)


async def _free_vram(lm: LMStudio, comfy: ComfyClient) -> dict:
    """Descarga el LLM y pide a ComfyUI liberar memoria. Best-effort."""
    unload = await lm.unload_all()
    await comfy.free_memory()
    return unload


async def generate(comfy: ComfyClient, lm: LMStudio, params: GenParams,
                   on_phase=None, on_step=None) -> dict:
    """Ejecuta el pipeline completo. Devuelve el manifest de la generación.

    on_phase(name, extra_dict) — cambio de fase.
    on_step(step, total)       — progreso del sampler.
    """
    def phase(name, **extra):
        if on_phase:
            on_phase(name, extra)

    seed = _resolve_seed(params.seed)

    # 1. caption
    phase("caption")
    caption, prompt_str = await build_caption(lm, params)

    # 2. unload LLM + free ComfyUI
    unload_info = {}
    if params.manage_vram and not params.json_prompt.strip():
        phase("unload")
        unload_info = await _free_vram(lm, comfy)

    # 3. render
    phase("render", seed=seed)
    if not await comfy.health_check():
        raise PipelineError("ComfyUI no responde en :8188 — arráncalo desde el Hub")

    wf = load_workflow(_WORKFLOW)
    if params.turbo:
        # Turbo y anti-bloqueo son excluyentes (el turbo destila el guidance).
        cond_ref, _ = _apply_loras(wf, params, uncond=False)
        bypass_info = _apply_turbo(wf, cond_ref)
    else:
        cond_ref, uncond_ref = _apply_loras(wf, params)
        bypass_info = _apply_bypass(wf, params, cond_ref, uncond_ref)
    render_params = {
        "unet_cond": params.unet_cond,
        "unet_uncond": params.unet_uncond,
        "clip_name": params.clip_name,
        "vae_name": params.vae_name,
        "json_prompt": prompt_str,
        "width": int(params.width),
        "height": int(params.height),
        "steps": int(params.steps),
        "mu": float(params.mu),
        "std": float(params.std),
        "cfg": float(params.cfg),
        "sampler": params.sampler,
        "seed": seed,
    }
    png = await comfy.run_workflow(wf, render_params, on_progress=on_step)
    if params.turbo:
        render_params["turbo"] = True            # solo para el manifest
    elif bypass_info:
        render_params["bypass"] = bypass_info    # solo para el manifest
    if params.loras:
        render_params["loras"] = params.loras    # solo para el manifest

    # 4. blockguard
    phase("check")
    blocked, metrics = blockguard.is_safety_block(png)

    # 5. guardar
    phase("save")
    manifest = _save(png, params, caption, prompt_str, seed, blocked, metrics,
                     unload_info, render_params)
    phase("done", run_id=manifest["id"], blocked=blocked)
    return manifest


def _save(png: bytes, params: GenParams, caption: dict, prompt_str: str,
          seed: int, blocked: bool, metrics: dict, unload_info: dict,
          render_params: dict) -> dict:
    run_id = time.strftime("%Y%m%d-%H%M%S") + f"-{seed & 0xFFFF:04x}"
    out = HISTORY_DIR / run_id
    out.mkdir(parents=True, exist_ok=True)
    (out / "image.png").write_bytes(png)
    manifest = {
        "id": run_id,
        "created": time.time(),
        "description": params.description,
        "caption": caption,
        "prompt": prompt_str,
        "seed": seed,
        "blocked": blocked,
        "block_metrics": metrics,
        "unload": unload_info,
        "render": {k: v for k, v in render_params.items() if k != "json_prompt"},
    }
    (out / "meta.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
    return manifest


def list_history(limit: int = 60) -> list[dict]:
    if not HISTORY_DIR.exists():
        return []
    runs = []
    for d in sorted(HISTORY_DIR.iterdir(), reverse=True):
        meta = d / "meta.json"
        if meta.is_file():
            try:
                m = json.loads(meta.read_text(encoding="utf-8"))
                runs.append({"id": m["id"], "created": m.get("created"),
                             "description": m.get("description", ""),
                             "seed": m.get("seed"), "blocked": m.get("blocked", False),
                             "render": m.get("render", {})})
            except Exception:
                continue
        if len(runs) >= limit:
            break
    return runs


def history_image(run_id: str) -> Path:
    p = (HISTORY_DIR / run_id / "image.png").resolve()
    if not p.is_relative_to(HISTORY_DIR.resolve()) or not p.is_file():
        raise PipelineError("imagen no encontrada")
    return p


def history_meta(run_id: str) -> dict:
    p = (HISTORY_DIR / run_id / "meta.json").resolve()
    if not p.is_relative_to(HISTORY_DIR.resolve()) or not p.is_file():
        raise PipelineError("run no encontrado")
    return json.loads(p.read_text(encoding="utf-8"))


def delete_run(run_id: str):
    import shutil
    p = (HISTORY_DIR / run_id).resolve()
    if not p.is_relative_to(HISTORY_DIR.resolve()) or not p.is_dir():
        raise PipelineError("run no encontrado")
    shutil.rmtree(p)
