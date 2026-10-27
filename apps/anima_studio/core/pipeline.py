"""Anima generation by phases; persistence belongs to the studio store."""
import asyncio
import hashlib
import json
import math
import secrets
import time
from dataclasses import asdict, dataclass, field, replace

from .comfy_client import ComfyClient, load_workflow
from . import expressions


class PipelineError(Exception):
    pass


@dataclass
class GenParams:
    model: str = ""
    text_encoder: str = ""
    vae: str = ""
    prompt: str = ""
    negative: str = ""
    seed: str = "-1"
    width: int = 1024
    height: int = 1024
    steps: int = 30
    cfg: float = 4.0
    sampler: str = "er_sde"
    scheduler: str = "simple"
    denoise: float = 1.0
    loras: list = field(default_factory=list)
    mode: str = "txt2img"
    master_seed: str = ""
    crop_padding: int = 64
    feather: float = 8.0


def resolve_seed(value):
    if isinstance(value, bool):
        raise ValueError("Seed inválida")
    text = str(value).strip()
    if text == "-1":
        return str(secrets.randbits(64))
    if not text.isascii() or not text.isdecimal() or not 0 <= int(text) <= 0xFFFFFFFFFFFFFFFF:
        raise ValueError("La seed debe ser un entero entre 0 y 18446744073709551615")
    return str(int(text))


def validate_params(params):
    if params.mode not in ("txt2img", "expression"):
        raise ValueError("Modo de generación desconocido")
    if not params.prompt.strip():
        raise ValueError("Escribe un prompt antes de generar")
    if len(params.prompt) > 24000 or len(params.negative) > 24000:
        raise ValueError("El prompt excede el límite del PoC")
    if params.mode == "expression":
        if (type(params.width) is not int or type(params.height) is not int
                or min(params.width, params.height) < 1 or max(params.width, params.height) > 4096
                or params.width * params.height > 16_777_216):
            raise ValueError("El maestro debe tener hasta 4096 píxeles por lado y 16 megapíxeles")
    elif (type(params.width) is not int or type(params.height) is not int
          or min(params.width, params.height) < 128 or max(params.width, params.height) > 2048
          or params.width % 16 or params.height % 16
          or params.width * params.height > 3_145_728):
        raise ValueError("Usa dimensiones múltiplo de 16, de 128 a 2048 y hasta 3 megapíxeles")
    if not isinstance(params.steps, int) or not 1 <= params.steps <= 100:
        raise ValueError("Los pasos deben estar entre 1 y 100")
    if not math.isfinite(float(params.cfg)) or not 0 <= float(params.cfg) <= 20:
        raise ValueError("CFG debe estar entre 0 y 20")
    if not math.isfinite(float(params.denoise)) or not 0 < float(params.denoise) <= 1:
        raise ValueError("Denoise debe estar entre 0 y 1")
    if len(params.loras) > 12:
        raise ValueError("El PoC permite hasta 12 LoRAs por toma")
    seen = set()
    for item in params.loras:
        if not isinstance(item, dict) or not isinstance(item.get("file"), str):
            raise ValueError("LoRA inválido")
        path = item["file"].replace("\\", "/")
        if path in seen:
            raise ValueError("No repitas el mismo LoRA en una toma")
        seen.add(path)
        strength = float(item.get("strength", 1))
        if not math.isfinite(strength) or not -3 <= strength <= 3:
            raise ValueError("El peso de LoRA debe estar entre -3 y 3")
    return replace(params, seed=resolve_seed(params.seed),
                   denoise=1.0 if params.mode == "txt2img" else params.denoise)


def build_workflow(params, image_name="", mask_name=""):
    template = load_workflow("expression.json" if params.mode == "expression" else "txt2img.json")
    values = asdict(params)
    values.update(seed=int(params.seed), image_name=image_name, mask_name=mask_name)
    workflow = ComfyClient.build_workflow(template, values)
    model = ["1", 0]
    for index, lora in enumerate(params.loras):
        node = str(100 + index)
        workflow[node] = {"class_type": "LoraLoaderModelOnly", "inputs": {
            "model": model, "lora_name": lora["file"],
            "strength_model": float(lora.get("strength", 1))}}
        model = [node, 0]
    workflow["7"]["inputs"]["model"] = model
    return workflow


async def resolve_backend_names(client, params):
    """Use the exact dropdown spelling expected by this ComfyUI instance.

    The disk catalogue intentionally exposes portable paths. On Windows the
    backend's enum values contain backslashes, and ComfyUI validates those
    strings literally before executing any node.
    """
    folders = ("diffusion_models", "text_encoders", "vae", "loras")
    catalogues = dict(zip(folders, await asyncio.gather(
        *(client.get_models(folder) for folder in folders))))

    def exact_name(value, folder):
        offered = catalogues[folder]
        if not isinstance(offered, list):
            raise PipelineError(f"Catálogo de ComfyUI inválido: {folder}")
        normalized = value.replace("\\", "/")
        for candidate in offered:
            if isinstance(candidate, str) and candidate.replace("\\", "/") == normalized:
                return candidate
        raise PipelineError(f"ComfyUI no ofrece este archivo en {folder}: {value}")

    return replace(params,
                   model=exact_name(params.model, "diffusion_models"),
                   text_encoder=exact_name(params.text_encoder, "text_encoders"),
                   vae=exact_name(params.vae, "vae"),
                   loras=[{**item, "file": exact_name(item["file"], "loras")}
                          for item in params.loras])


async def generate(client, params, on_progress=None, master_bytes=None, mask_bytes=None):
    params = validate_params(params)
    started = time.monotonic()

    def report(phase, **details):
        if on_progress:
            on_progress(phase, details)

    report("prepare", seed=params.seed)
    params = await resolve_backend_names(client, params)
    metadata = {"schema_version": 1, "params": asdict(params), "seed": params.seed,
                "mode": params.mode}
    crop = None
    effective = params
    image_name = mask_name = ""
    if params.mode == "expression":
        if not master_bytes or not mask_bytes:
            raise ValueError("Selecciona un maestro y dibuja una máscara facial")
        crop = expressions.prepare(master_bytes, mask_bytes, params.crop_padding, params.feather)
        if max(crop.master.size) > 4096:
            raise ValueError("El maestro debe tener hasta 4096 píxeles por lado y 16 megapíxeles")
        # The final canvas belongs to the immutable master, regardless of a
        # project's selected generation format. Only the rendered crop changes.
        params = replace(params, width=crop.master.width, height=crop.master.height)
        metadata["params"] = asdict(params)
        effective = replace(params, width=crop.render_size[0], height=crop.render_size[1])
        image_name = await client.upload_image(crop.image)
        mask_name = await client.upload_image(crop.mask)
        metadata.update(master_sha256=hashlib.sha256(master_bytes).hexdigest(),
                        master_seed=params.master_seed, edit_seed=params.seed,
                        input_mask_sha256=hashlib.sha256(mask_bytes).hexdigest())
    workflow = build_workflow(effective, image_name, mask_name)
    serialized = json.dumps(workflow, sort_keys=True, ensure_ascii=False).encode("utf-8")
    metadata["workflow_sha256"] = hashlib.sha256(serialized).hexdigest()
    metadata["workflow"] = workflow
    report("render", step=0, total=params.steps)
    image = await client.run_workflow(
        workflow, {}, lambda step, total: report("render", step=step, total=total))
    result = {"image": image}
    if crop:
        report("compose")
        composed = expressions.compose(crop, image)
        metadata["expression"] = composed.pop("details")
        result.update(composed)
    else:
        rendered = expressions.open_image(image)
        if rendered.size != (params.width, params.height):
            raise PipelineError("ComfyUI devolvió dimensiones inesperadas")
    metadata["elapsed_seconds"] = round(time.monotonic() - started, 3)
    result["metadata"] = metadata
    report("done", elapsed_seconds=metadata["elapsed_seconds"])
    return result
