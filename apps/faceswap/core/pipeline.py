"""
Pipeline de face swap: escena real (driving) → LivePortrait reanima al donante
con la pose/expresión de la escena → ReActor (HyperSwap) compone el rostro
sobre la escena original.

El workflow se construye programáticamente (dict API de ComfyUI) porque la
estructura cambia según las opciones (con/sin reenactment), a diferencia de
Ideogram donde el grafo es fijo y se templatea.

Historial: apps/faceswap/data/history/<run_id>/ con las entradas (scene/donor),
el resultado, el intermedio de LivePortrait si existe y meta.json.
"""
import json
import shutil
import datetime
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Callable

from .comfy_client import ComfyClient, ComfyError
from . import nsfw

DATA_DIR = Path(__file__).parent.parent / "data"
HISTORY_DIR = DATA_DIR / "history"

# Piezas elegidas por licencia (sesión 46): HyperSwap MIT — nunca inswapper.
DEFAULT_SWAP_MODEL = "hyperswap_1c_256.onnx"
DEFAULT_RESTORE = "GFPGANv1.4.pth"

FACE_DETECTORS = ["retinaface_resnet50", "retinaface_mobile0.25", "YOLOv5l", "YOLOv5n"]


class PipelineError(Exception):
    pass


@dataclass
class SwapParams:
    # Reenactment OFF por defecto: en shots en vivo (caso principal) el swapper
    # ya adopta la pose/expresión de la cara de la escena; LivePortrait solo
    # sirve si la pose del donante no casa NI de lejos (retrato formal, caso A).
    use_reenact: bool = False
    retargeting_eyes: float = 0.0
    retargeting_mouth: float = 0.0
    crop_factor: float = 1.7          # rango del nodo: 1.5–2.5
    command: str = ""                 # comandos avanzados de expresión del nodo ALP
    input_faces_index: str = "0"      # qué cara(s) de la escena se reemplazan
    source_faces_index: str = "0"     # qué cara del donante se usa
    facedetection: str = "retinaface_resnet50"
    swap_model: str = DEFAULT_SWAP_MODEL
    restore_model: str = DEFAULT_RESTORE   # "none" para desactivar
    restore_visibility: float = 0.8   # <1 conserva algo de textura real (menos plástico)
    codeformer_weight: float = 0.5
    # Compositing fino: recompone el swap sobre la escena con máscara SAM
    # (respeta oclusiones y bordes de pelo — el "pegote" del nodo básico
    # era la brecha de calidad contra FaceFusion).
    use_mask_helper: bool = True
    mask_blur: int = 12        # blur_radius: ancho de la zona difuminada del borde
    mask_dilation: int = 0     # sam_dilation: agranda(+)/achica(-) el área reemplazada
    mask_sigma: float = 1.0    # sigma_factor: suavidad del difuminado dentro del borde
    # Modelos del MaskHelper. Vacío = los resuelve la ruta desde lo que ComfyUI
    # tiene registrado (E:/Models/ultralytics/bbox y /sams vía Impact Pack), así
    # no se hardcodea un nombre que puede no existir en este equipo.
    mask_bbox_model: str = ""
    mask_sam_model: str = ""


# ═══════════════════════════════════════════════════════════════════════════
# Workflow
# ═══════════════════════════════════════════════════════════════════════════

RESULT_NODE = "20"
REENACT_NODE = "21"

# Fallbacks si la ruta no resolvió modelos desde ComfyUI (last resort).
MASK_BBOX_MODEL = "bbox/face_yolov8s.pt"
MASK_SAM_MODEL = "sam_vit_b_01ec64.pth"


def build_workflow(p: SwapParams, scene_ref: str, donor_refs: list[str]) -> dict:
    """Grafo API de ComfyUI. Nodos: 1=escena, 2/5/6=donantes, 7/8=batch,
    9=BuildFaceModel (multi-donante), 3=LivePortrait (opcional), 4=ReActor,
    10=MaskHelper (opcional), 20=SaveImage resultado, 21=SaveImage intermedio."""
    if not donor_refs:
        raise PipelineError("hace falta al menos una foto del donante")
    wf = {
        "1": {"class_type": "LoadImage", "inputs": {"image": scene_ref}},
        "2": {"class_type": "LoadImage", "inputs": {"image": donor_refs[0]}},
    }

    # Con varias fotos del donante: mezclar embeddings en un FACE_MODEL
    # (media) — identidad más estable que una foto suelta.
    face_model = None
    if len(donor_refs) > 1 and not p.use_reenact:
        prev = ["2", 0]
        for i, ref in enumerate(donor_refs[1:3]):
            load_id, batch_id = str(5 + i), str(7 + i)
            wf[load_id] = {"class_type": "LoadImage", "inputs": {"image": ref}}
            wf[batch_id] = {"class_type": "ImageBatch", "inputs": {
                "image1": prev, "image2": [load_id, 0]}}
            prev = [batch_id, 0]
        wf["9"] = {"class_type": "ReActorBuildFaceModel", "inputs": {
            "save_mode": False, "send_only": False,
            "face_model_name": "faceswap_tmp", "compute_method": "Mean",
            "images": prev}}
        face_model = ["9", 0]

    if p.use_reenact:
        # reenactment usa solo la primera foto del donante
        wf["3"] = {"class_type": "AdvancedLivePortrait", "inputs": {
            "retargeting_eyes": p.retargeting_eyes,
            "retargeting_mouth": p.retargeting_mouth,
            "crop_factor": p.crop_factor,
            "turn_on": True,
            "tracking_src_vid": False,
            "animate_without_vid": False,
            "command": p.command,
            "src_images": ["2", 0],
            "driving_images": ["1", 0],
        }}
        source = ["3", 0]
        wf[REENACT_NODE] = {"class_type": "SaveImage", "inputs": {
            "images": ["3", 0], "filename_prefix": "faceswap_reenact"}}
    else:
        source = ["2", 0]

    reactor_inputs = {
        "enabled": True,
        "input_image": ["1", 0],
        "swap_model": p.swap_model,
        "facedetection": p.facedetection,
        "face_restore_model": p.restore_model,
        "face_restore_visibility": p.restore_visibility,
        "codeformer_weight": p.codeformer_weight,
        "detect_gender_input": "no",
        "detect_gender_source": "no",
        "input_faces_index": p.input_faces_index,
        "source_faces_index": p.source_faces_index,
        "console_log_level": 1,
    }
    if face_model is not None:
        reactor_inputs["face_model"] = face_model
    else:
        reactor_inputs["source_image"] = source
    wf["4"] = {"class_type": "ReActorFaceSwap", "inputs": reactor_inputs}

    final = ["4", 0]
    if p.use_mask_helper:
        wf["10"] = {"class_type": "ReActorMaskHelper", "inputs": {
            "image": ["1", 0],
            "swapped_image": ["4", 0],
            "bbox_model_name": p.mask_bbox_model or MASK_BBOX_MODEL,
            "bbox_threshold": 0.5,
            "bbox_dilation": 10,
            "bbox_crop_factor": 3.0,
            "bbox_drop_size": 10,
            "sam_model_name": p.mask_sam_model or MASK_SAM_MODEL,
            "sam_dilation": p.mask_dilation,
            "sam_threshold": 0.93,
            "bbox_expansion": 0,
            "mask_hint_threshold": 0.7,
            "mask_hint_use_negative": "False",
            "morphology_operation": "dilate",
            "morphology_distance": 0,
            "blur_radius": p.mask_blur,
            "sigma_factor": p.mask_sigma,
        }}
        final = ["10", 0]

    wf[RESULT_NODE] = {"class_type": "SaveImage", "inputs": {
        "images": final, "filename_prefix": "faceswap_result"}}
    return wf


# ═══════════════════════════════════════════════════════════════════════════
# Ejecución
# ═══════════════════════════════════════════════════════════════════════════

async def generate(
    comfy: ComfyClient,
    params: SwapParams,
    scene_bytes: bytes,
    donor_list: list[bytes],
    on_phase: Callable[[str, dict], None] | None = None,
    on_step: Callable[[int, int], None] | None = None,
) -> dict:
    """Flujo completo: subir entradas → render → guardar historial.
    donor_list: 1..3 fotos del donante (varias = identidad promediada).
    Devuelve el manifest del run."""
    def phase(name, **extra):
        if on_phase:
            on_phase(name, extra)

    if not donor_list:
        raise PipelineError("hace falta al menos una foto del donante")
    if not await comfy.health_check():
        raise PipelineError("ComfyUI no responde en :8188 — levantalo desde el hub")

    run_id = datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + \
        __import__("uuid").uuid4().hex[:4]

    phase("upload")
    scene_ref = await comfy.upload_image(f"fs_{run_id}_scene.png", scene_bytes)
    donor_refs = []
    for i, d in enumerate(donor_list[:3]):
        donor_refs.append(await comfy.upload_image(f"fs_{run_id}_donor{i}.png", d))

    phase("render")
    wf = build_workflow(params, scene_ref, donor_refs)
    try:
        pid, cid = await comfy.queue_prompt(wf)
        outputs = await comfy.wait_for_completion(pid, cid, on_progress=on_step)
    except ComfyError:
        raise
    except Exception as e:
        raise PipelineError(f"fallo hablando con ComfyUI: {e}")

    # Si el filtro NSFW bloqueó, ReActor devuelve un cuadro negro mudo. Lo
    # detectamos por el reporte del nodo y damos un error accionable (subí el
    # umbral / apagá el filtro) en vez de guardar el rectángulo negro.
    last = nsfw.last_score()
    if last.get("blocked"):
        raise PipelineError(
            f"El filtro NSFW bloqueó la foto (score {last.get('score')}, "
            f"umbral {nsfw.get_config()['threshold']:.3f}). Subí el umbral con el "
            f"slider — o apagá el filtro — y reintentá.")

    if RESULT_NODE not in outputs:
        raise PipelineError("ComfyUI terminó sin producir el resultado "
                            f"(outputs: {list(outputs)})")
    result = await comfy.get_output_image(outputs[RESULT_NODE])
    reenact = None
    if REENACT_NODE in outputs:
        reenact = await comfy.get_output_image(outputs[REENACT_NODE])

    phase("save")
    run_dir = HISTORY_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "scene.png").write_bytes(scene_bytes)
    (run_dir / "donor.png").write_bytes(donor_list[0])
    (run_dir / "result.png").write_bytes(result)
    if reenact:
        (run_dir / "reenact.png").write_bytes(reenact)
    meta = {
        "id": run_id,
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
        "params": asdict(params),
        "has_reenact": reenact is not None,
        "nsfw_score": last.get("score"),
        "donor_count": len(donor_refs),
    }
    (run_dir / "meta.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    return meta


# ═══════════════════════════════════════════════════════════════════════════
# Historial
# ═══════════════════════════════════════════════════════════════════════════

IMAGE_KINDS = ("result", "scene", "donor", "reenact")


def list_history() -> list[dict]:
    if not HISTORY_DIR.exists():
        return []
    runs = []
    for d in sorted(HISTORY_DIR.iterdir(), reverse=True):
        meta_f = d / "meta.json"
        if d.is_dir() and meta_f.exists():
            try:
                runs.append(json.loads(meta_f.read_text(encoding="utf-8")))
            except Exception:
                continue
    return runs


def _run_dir(run_id: str) -> Path:
    # el run_id viene de la URL: nunca dejar que escape del historial
    if not run_id or "/" in run_id or "\\" in run_id or ".." in run_id:
        raise PipelineError(f"run_id inválido: {run_id!r}")
    d = HISTORY_DIR / run_id
    if not d.is_dir():
        raise PipelineError(f"run {run_id} no existe")
    return d


def history_meta(run_id: str) -> dict:
    return json.loads((_run_dir(run_id) / "meta.json").read_text(encoding="utf-8"))


def history_image(run_id: str, kind: str = "result") -> Path:
    if kind not in IMAGE_KINDS:
        raise PipelineError(f"tipo de imagen inválido: {kind}")
    p = _run_dir(run_id) / f"{kind}.png"
    if not p.exists():
        raise PipelineError(f"{kind} no existe en el run {run_id}")
    return p


def history_dir(run_id: str) -> tuple[Path, Path]:
    d = _run_dir(run_id)
    return d, d / "result.png"


def delete_run(run_id: str):
    shutil.rmtree(_run_dir(run_id))
