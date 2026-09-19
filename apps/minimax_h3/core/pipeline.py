"""
Orquestación de una generación MiniMax H3 de principio a fin.

A diferencia de Ideogram, H3 no orquesta nada fuera de ComfyUI (no hay un LLM
externo que cargar/descargar) — encoder, DiT y VAEs son todos nodos del mismo
grafo, así que no hace falta una fase manual de gestión de VRAM: ComfyUI la
gestiona solo al ejecutar. El pipeline es más simple que el de Ideogram:

  1. build   — arma el workflow (turbo LoRA opcional, imagen inicial opcional).
  2. render  — lo encola en ComfyUI y espera el resultado (WS, con fallback a
               /history si ComfyUI resuelve todo desde caché).
  3. save    — guarda el video + metadata en data/history/<id>.{ext,json}.

v1: un solo clip por corrida (tope nativo de H3, ~15s), sin stitching, sin
Ref2VA (fuera de v1 — ver TASKS.md, "Decisiones cerradas").
"""
import time
import random
from pathlib import Path
from dataclasses import dataclass, field

from .comfy_client import ComfyClient, load_workflow

_DATA = Path(__file__).parent.parent / "data"
HISTORY_DIR = _DATA / "history"
_WORKFLOW = "h3_video.json"
_REF2V_WORKFLOW = "h3_ref2v_t2v.json"

MAX_SEED = 0xFFFFFFFFFFFFFFFF
# Native H3 grid: length = 17n + 5 frames a 24fps ("duration (seconds) ->
# length" del template oficial). max ~15s por corrida, sin stitching (v1).
_FPS = 24
_MAX_SECONDS = 15


class PipelineError(Exception):
    pass


@dataclass
class GenParams:
    prompt: str = ""
    # modelos (nombres tal como los ve ComfyUI)
    unet_name: str = ""
    clip_name: str = ""
    video_vae_name: str = ""
    audio_vae_name: str = ""
    # imagen inicial opcional (I2V) — PNG/JPEG en base64, sin el prefijo data:
    first_frame_b64: str | None = None
    # geometría / duración
    width: int = 1344
    height: int = 768
    duration_s: float = 5.0
    # turbo (LoRA de destilación, 6-8 pasos en vez de 20)
    turbo: bool = False
    turbo_lora_name: str = ""
    turbo_strength: float = 1.0
    turbo_steps: int = 8
    steps: int = 20                 # solo aplica sin turbo
    seed: int = -1
    # LoRAs extra (decensurado, etc.) — [{name, strength}]
    loras: list = field(default_factory=list)


@dataclass
class Ref2VParams:
    """Modo de inicio Ref2V de una cadena (ver core/chains.py): genera un
    clip con el checkpoint ref2va usando 1-9 imágenes de referencia
    (identidad/estilo). NO da continuidad literal de frame (confirmado en
    vivo, s96 — TASKS.md) así que solo tiene sentido para el TRAMO 1; el
    resto de la cadena siempre usa `generate()` con fl2va+I2V."""
    prompt: str = ""
    unet_name: str = ""            # ref2va DiT
    clip_name: str = ""             # compartido con fl2va, no propio de Ref2V
    video_vae_name: str = ""        # ídem
    audio_vae_name: str = ""        # ídem
    ref_images_b64: list = field(default_factory=list)   # 1-9, sin prefijo data:
    # "match" (default del nodo) reduce cada referencia al área de píxeles del
    # canvas de salida; "max" usa hasta 2048px de lado corto -- el propio nodo
    # lo documenta como "best identity fidelity" a costa de más VRAM/tiempo
    # (los tokens de referencia viajan en cada paso de sampling). Nunca se
    # había probado "max" hasta s96 -- ver TASKS.md.
    ref_image_size: str = "max"
    width: int = 1344
    height: int = 768
    duration_s: float = 5.0
    turbo_lora_name: str = ""       # el turbo de Ref2V es propio (4 pasos), no el de fl2va
    turbo_strength: float = 1.0
    turbo_steps: int = 4
    seed: int = -1


def _resolve_seed(seed: int) -> int:
    return random.randint(0, MAX_SEED) if seed is None or seed < 0 else int(seed)


def _resolve_lora(name: str, available: list[str]) -> str:
    """Resuelve un nombre de LoRA contra el catálogo real de ComfyUI, con o sin
    la subcarpeta (`loras/h3/` en este módulo). ComfyUI lista los LoRAs con el
    separador de carpeta del SO (backslash en Windows); si el nombre pedido no
    trae subcarpeta (p. ej. viene de `model_downloader.ModelSpec.dest_name` o de
    un preset de `lora_presets.py`, que son nombres bare), se resuelve por
    coincidencia de archivo (stem) sin importar la carpeta — mismo patrón que
    `resolve_lora_name` de Painter (`apps/painter/core/image_utils.py`)."""
    if name in available:
        return name
    key = Path(name.replace("\\", "/")).name.lower()
    for cand in available:
        if Path(cand.replace("\\", "/")).name.lower() == key:
            return cand
    return name   # sin match: se deja igual, ComfyUI da el error de validación con el detalle


def _frame_length(seconds: float) -> int:
    """Segundos -> frames válidos para H3 (grid 17n+5 a 24fps), misma fórmula
    que el ComfyMathExpression del template oficial:
    max(5, round(a*24)) + (5 - (max(5, round(a*24)) % 17)) % 17"""
    seconds = max(0.2, min(float(seconds), _MAX_SECONDS))
    base = max(5, round(seconds * _FPS))
    return base + (5 - (base % 17)) % 17


def _apply_turbo(wf: dict, params: GenParams, available_loras: list[str]) -> list:
    """Inyecta el LoRA turbo (LoraLoaderModelOnly nativo) sobre el UNET y
    devuelve la ref de modelo final. Los parches de atención ya están aplicados
    en el nodo 901; cargar el LoRA después conserva su wrapper al clonar el modelo."""
    if not params.turbo:
        return ["901", 0]
    if not params.turbo_lora_name:
        raise PipelineError("modo turbo activado sin turbo_lora_name")
    lora_name = _resolve_lora(params.turbo_lora_name, available_loras)
    wf["5"] = {"class_type": "LoraLoaderModelOnly",
               "inputs": {"model": ["901", 0], "lora_name": lora_name,
                          "strength_model": float(params.turbo_strength)}}
    model_ref = ["5", 0]
    wf["9"]["inputs"]["model"] = model_ref
    wf["10"]["inputs"]["model"] = model_ref
    wf["9"]["inputs"]["steps"] = int(params.turbo_steps)
    return model_ref


def _apply_extra_loras(wf: dict, params: GenParams, model_ref: list,
                       available_loras: list[str]) -> list:
    """Encadena LoRAs extra (decensurado, etc.) sobre la ref de modelo que deje
    _apply_turbo. IDs 40+ para no chocar con el resto del grafo (1-15)."""
    nid = 40
    for lora in (params.loras or []):
        name = str(lora.get("name") or "").strip()
        if not name:
            continue
        strength = float(lora.get("strength", 1.0))
        lora_name = _resolve_lora(name, available_loras)
        wf[str(nid)] = {"class_type": "LoraLoaderModelOnly",
                        "inputs": {"model": model_ref, "lora_name": lora_name,
                                   "strength_model": strength}}
        model_ref = [str(nid), 0]
        nid += 1
    if params.loras:
        wf["9"]["inputs"]["model"] = model_ref
        wf["10"]["inputs"]["model"] = model_ref
    return model_ref


def _apply_first_frame(wf: dict, params: GenParams):
    """I2V: inyecta la imagen inicial vía ETN_LoadImageBase64 (comfyui-tooling-
    nodes, mismo nodo que usa Painter para sus imágenes de entrada). Sin
    imagen, el nodo MiniMaxH3ImageToVideo la trata como None (input opcional,
    T2V puro)."""
    if not params.first_frame_b64:
        return
    wf["20"] = {"class_type": "ETN_LoadImageBase64",
               "inputs": {"image": params.first_frame_b64}}
    wf["6"]["inputs"]["first_frame"] = ["20", 0]


def _apply_ref_images(wf: dict, ref_images_b64: list):
    """Inyecta 1-9 imágenes de referencia (ETN_LoadImageBase64, IDs 20+, no
    chocan con el resto del grafo) en MiniMaxH3ReferenceToVideo."""
    if not ref_images_b64:
        raise PipelineError("Ref2V necesita al menos una imagen de referencia")
    # Los inputs Autogrow del nodo van como claves planas punteadas en el
    # formato API ("ref_images.ref_image_0"); un dict anidado bajo
    # "ref_images" es descartado en silencio por el ejecutor y el nodo corre
    # sin referencias (causa del "cero parecido" de s96).
    for i, b64 in enumerate(ref_images_b64[:9]):
        nid = 20 + i
        wf[str(nid)] = {"class_type": "ETN_LoadImageBase64", "inputs": {"image": b64}}
        wf["6"]["inputs"][f"ref_images.ref_image_{i}"] = [str(nid), 0]


async def generate_ref2v(comfy: ComfyClient, params: Ref2VParams,
                         on_phase=None, on_step=None) -> dict:
    """Genera el tramo 1 de una cadena con el checkpoint Ref2V (varias
    imágenes de referencia). Ver docstring de `Ref2VParams` — nunca se usa
    para continuar una cadena, solo para arrancarla."""
    def phase(name, **extra):
        if on_phase:
            on_phase(name, extra)

    if not params.prompt.strip():
        raise PipelineError("hace falta un prompt")
    for field_name in ("unet_name", "clip_name", "video_vae_name", "audio_vae_name",
                       "turbo_lora_name"):
        if not getattr(params, field_name):
            raise PipelineError(f"falta {field_name} — ¿modelos Ref2V descargados?")

    seed = _resolve_seed(params.seed)
    length = _frame_length(params.duration_s)

    phase("build")
    if not await comfy.health_check():
        raise PipelineError("ComfyUI no responde en :8188 — arráncalo desde el Hub")
    available_loras = await comfy.get_models("loras")
    turbo_lora_name = _resolve_lora(params.turbo_lora_name, available_loras)
    wf = load_workflow(_REF2V_WORKFLOW)
    _apply_ref_images(wf, params.ref_images_b64)

    phase("render", seed=seed, length=length)
    render_params = {
        "unet_name": params.unet_name,
        "clip_name": params.clip_name,
        "clip_device": "cuda:0",
        "video_vae_name": params.video_vae_name,
        "audio_vae_name": params.audio_vae_name,
        "turbo_lora_name": turbo_lora_name,
        "turbo_strength": float(params.turbo_strength),
        "prompt": params.prompt,
        "width": int(params.width),
        "height": int(params.height),
        "length": length,
        "seed": seed,
        "steps": int(params.turbo_steps),
        "ref_image_size": params.ref_image_size,
    }
    data, filename = await comfy.run_workflow(wf, render_params, on_progress=on_step)
    ext = Path(filename).suffix.lstrip(".") or "mp4"
    render_params["ref2v"] = True
    render_params["ref_image_count"] = len(params.ref_images_b64)

    phase("save")
    manifest = _save(data, ext, params, seed, length, render_params)
    phase("done", run_id=manifest["id"])
    return manifest


async def generate(comfy: ComfyClient, params: GenParams,
                   on_phase=None, on_step=None) -> dict:
    """Ejecuta el pipeline completo. Devuelve el manifest de la generación.

    on_phase(name, extra_dict) — cambio de fase.
    on_step(step, total)       — progreso del sampler.
    """
    def phase(name, **extra):
        if on_phase:
            on_phase(name, extra)

    if not params.prompt.strip():
        raise PipelineError("hace falta un prompt")
    for field_name in ("unet_name", "clip_name", "video_vae_name", "audio_vae_name"):
        if not getattr(params, field_name):
            raise PipelineError(f"falta {field_name} — ¿modelos descargados?")

    seed = _resolve_seed(params.seed)
    length = _frame_length(params.duration_s)

    phase("build")
    if not await comfy.health_check():
        raise PipelineError("ComfyUI no responde en :8188 — arráncalo desde el Hub")
    available_loras = await comfy.get_models("loras") if (params.turbo or params.loras) else []
    wf = load_workflow(_WORKFLOW)
    model_ref = _apply_turbo(wf, params, available_loras)
    _apply_extra_loras(wf, params, model_ref, available_loras)
    _apply_first_frame(wf, params)

    phase("render", seed=seed, length=length)

    render_params = {
        "unet_name": params.unet_name,
        "clip_name": params.clip_name,
        # Repartir el encoder a la GPU secundaria (cuda:1, la 3060) suena bien
        # en teoría pero CRASHEA de verdad en este hardware: la 3060 (Ampere)
        # y la 5060 Ti (Blackwell) no soportan P2P directo, así que cada capa
        # del encoder hace CPU-staging cruzando de GPU y tras varias
        # iteraciones aborta a nivel nativo (Fatal Python error: Aborted,
        # dentro de comfy/text_encoders/llama.py::forward). Confirmado en
        # vivo, s94 — ver TASKS.md. Fijado en cuda:0 (misma GPU que el DiT)
        # hasta investigar más a fondo; NO cambiar a cuda:1 sin retomar esa
        # investigación primero.
        "clip_device": "cuda:0",
        "video_vae_name": params.video_vae_name,
        "audio_vae_name": params.audio_vae_name,
        "prompt": params.prompt,
        "width": int(params.width),
        "height": int(params.height),
        "length": length,
        "seed": seed,
        "steps": int(params.turbo_steps if params.turbo else params.steps),
    }
    data, filename = await comfy.run_workflow(wf, render_params, on_progress=on_step)
    ext = Path(filename).suffix.lstrip(".") or "mp4"
    if params.turbo:
        render_params["turbo"] = True
    if params.loras:
        render_params["loras"] = params.loras

    phase("save")
    manifest = _save(data, ext, params, seed, length, render_params)
    phase("done", run_id=manifest["id"])
    return manifest


def _save(data: bytes, ext: str, params: GenParams, seed: int, length: int,
          render_params: dict) -> dict:
    run_id = time.strftime("%Y%m%d-%H%M%S") + f"-{seed & 0xFFFF:04x}"
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    (HISTORY_DIR / f"{run_id}.{ext}").write_bytes(data)
    manifest = {
        "id": run_id,
        "ext": ext,
        "created": time.time(),
        "prompt": params.prompt,
        "seed": seed,
        "length_frames": length,
        "duration_s": round(length / _FPS, 2),
        "render": render_params,
    }
    (HISTORY_DIR / f"{run_id}.json").write_text(
        __import__("json").dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8")
    return manifest


def list_history(limit: int = 60) -> list[dict]:
    if not HISTORY_DIR.exists():
        return []
    runs = []
    for f in sorted(HISTORY_DIR.glob("*.json"), reverse=True):
        try:
            import json
            m = json.loads(f.read_text(encoding="utf-8"))
            runs.append({"id": m["id"], "ext": m.get("ext", "mp4"),
                         "created": m.get("created"), "prompt": m.get("prompt", ""),
                         "seed": m.get("seed"), "duration_s": m.get("duration_s")})
        except Exception:
            continue
        if len(runs) >= limit:
            break
    return runs


def _run_file(run_id: str, ext: str) -> Path:
    """Ruta validada de <run_id>.<ext> dentro de HISTORY_DIR (anti path traversal)."""
    p = (HISTORY_DIR / f"{run_id}.{ext}").resolve()
    if not p.is_relative_to(HISTORY_DIR.resolve()):
        raise PipelineError("run inválido")
    return p


def history_meta(run_id: str) -> dict:
    p = _run_file(run_id, "json")
    if not p.is_file():
        raise PipelineError("run no encontrado")
    import json
    return json.loads(p.read_text(encoding="utf-8"))


def history_video(run_id: str) -> Path:
    meta = history_meta(run_id)
    p = _run_file(run_id, meta.get("ext", "mp4"))
    if not p.is_file():
        raise PipelineError("video no encontrado")
    return p


def history_dir(run_id: str) -> tuple[Path, Path]:
    video = history_video(run_id)
    return HISTORY_DIR.resolve(), video


def delete_run(run_id: str):
    meta_path = _run_file(run_id, "json")
    if not meta_path.is_file():
        raise PipelineError("run no encontrado")
    ext = history_meta(run_id).get("ext", "mp4")
    _run_file(run_id, ext).unlink(missing_ok=True)
    meta_path.unlink(missing_ok=True)
