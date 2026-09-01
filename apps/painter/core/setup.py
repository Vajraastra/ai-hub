"""
Painter — setup de primer uso.
Verifica ComfyUI, instala custom nodes, detecta modelos disponibles.
"""
import sys
import json
import asyncio
from pathlib import Path
from typing import AsyncGenerator

from comfy_client import ComfyClient

# El instalador de custom nodes es transversal (ComfyUI es un proceso
# compartido), así que vive en hub/ y no en este módulo.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "hub"))
from modules.comfy_nodes import install_nodes_async

_HERE       = Path(__file__).parent                        # apps/painter/core
_PAINTER    = _HERE.parent                                 # apps/painter
_ROOT       = _PAINTER.parent.parent                       # ai-hub/
_HUB_CONFIG = _ROOT / "hub" / "hub_config.json"
_REGISTRY   = _PAINTER / "nodes_registry.json"
_STATUS_FILE= _PAINTER / "painter_setup.json"

COMFY_PORT  = 8188


def _load_hub_config() -> dict:
    with open(_HUB_CONFIG) as f:
        return json.load(f)


def _load_registry() -> dict:
    with open(_REGISTRY) as f:
        return json.load(f)


def _load_status() -> dict:
    if _STATUS_FILE.exists():
        with open(_STATUS_FILE) as f:
            return json.load(f)
    return {}


def _save_status(status: dict):
    with open(_STATUS_FILE, "w") as f:
        json.dump(status, f, indent=2)


# ── Checks síncronos ────────────────────────────────────────────────────────

def check_comfyui_installed() -> tuple[bool, Path | None]:
    """Retorna (instalado, comfyui_dir)."""
    try:
        cfg = _load_hub_config()
        entry = cfg.get("installed_apps", {}).get("comfyui", {})
        if entry.get("installed") and entry.get("dir"):
            d = Path(entry["dir"])
            if d.exists():
                return True, d
    except Exception:
        pass
    return False, None


_YOLO_DETECTORS = [
    {
        "filename": "face_yolov8n.pt",
        "label":    "Cara",
        "url":      "https://huggingface.co/Bingsu/adetailer/resolve/main/face_yolov8n.pt",
    },
    {
        "filename": "hand_yolov8n.pt",
        "label":    "Manos",
        "url":      "https://huggingface.co/Bingsu/adetailer/resolve/main/hand_yolov8n.pt",
    },
    {
        "filename": "person_yolov8n-seg.pt",
        "label":    "Personas",
        "url":      "https://huggingface.co/Bingsu/adetailer/resolve/main/person_yolov8n-seg.pt",
    },
]


def _get_bbox_dir() -> Path | None:
    """Retorna models/ultralytics/bbox según hub_config.json, o None si no está configurado."""
    try:
        cfg = _load_hub_config()
        models_root = cfg.get("paths", {}).get("models", "")
        if models_root:
            return Path(models_root) / "ultralytics" / "bbox"
    except Exception:
        pass
    return None


async def _download_file(url: str, dest: Path) -> tuple[bool, str]:
    """Descarga url a dest. Usa archivo .tmp para descarga atómica."""
    import urllib.request as _urlreq

    def _blocking():
        tmp = dest.with_suffix(".tmp")
        try:
            _urlreq.urlretrieve(url, str(tmp))
            tmp.rename(dest)
            return True, ""
        except Exception as e:
            if tmp.exists():
                tmp.unlink(missing_ok=True)
            return False, str(e)

    return await asyncio.to_thread(_blocking)


# ── Orquestador principal ───────────────────────────────────────────────────

async def run_setup() -> AsyncGenerator[dict, None]:
    """
    Generador asíncrono que emite eventos de progreso:
      {"step": str, "status": "ok"|"error"|"warning"|"info", "msg": str}
    El consumidor (SSE endpoint) serializa cada evento como data: JSON\n\n
    """

    def event(step: str, status: str, msg: str) -> dict:
        return {"step": step, "status": status, "msg": msg}

    registry    = _load_registry()
    all_node_ids = [e["id"] for e in registry.get("required", []) + registry.get("enhanced", [])]
    status_data = {"ready": False, "needs_restart": False, "warnings": [], "fallbacks": {},
                   "models": {}, "installed_node_ids": all_node_ids}

    # ── Paso 1: ComfyUI instalado ──────────────────────────────────────────
    yield event("comfyui_installed", "info", "Verificando instalación de ComfyUI…")
    installed, comfyui_dir = check_comfyui_installed()
    if not installed:
        yield event("comfyui_installed", "error",
                    "ComfyUI no está instalado. Instálalo desde el hub principal.")
        return
    yield event("comfyui_installed", "ok", f"ComfyUI encontrado en {comfyui_dir}")

    # ── Paso 2: ComfyUI corriendo ──────────────────────────────────────────
    yield event("comfyui_running", "info", "Verificando que ComfyUI esté activo…")
    client = ComfyClient(port=COMFY_PORT)
    alive  = await client.health_check()
    if not alive:
        yield event("comfyui_running", "error",
                    f"ComfyUI no responde en el puerto {COMFY_PORT}. "
                    "Inícialo desde el hub antes de usar Painter.")
        return
    yield event("comfyui_running", "ok", "ComfyUI activo")

    # ── Paso 3: Custom nodes ───────────────────────────────────────────────
    # La instalación la hace hub/modules/comfy_nodes.py, compartido con el
    # bootstrap y con los demás módulos; acá solo se traduce a eventos SSE.
    # ComfyUI está vivo a esta altura, así que se le pregunta qué nodos tiene
    # cargados UNA vez: `probe_node` bajaba /object_info entero por cada nodo.
    try:
        loaded = set(await client.get_object_info())
    except Exception:
        loaded = set()

    async for ev in install_nodes_async(comfyui_dir, _ROOT / "apps", _ROOT,
                                        loaded_nodes=loaded, only_modules=["painter"]):
        data = ev.get("data")
        if data:
            status_data["needs_restart"] = data["needs_restart"]
            status_data["warnings"].extend(data["warnings"])
            status_data["fallbacks"].update(data["fallbacks"])
            if data["failed"]:
                yield event("nodes", "error", ev["msg"])
                return
            continue
        yield event(ev["step"], ev["status"], ev["msg"])

    # ── Paso 4: Modelos ────────────────────────────────────────────────────
    yield event("models", "info", "Verificando modelos disponibles…")
    models_status = {}

    checkpoints = await client.get_models("checkpoints")
    models_status["checkpoints"] = checkpoints
    if not checkpoints:
        yield event("models_checkpoints", "warning",
                    "No hay checkpoints. No se podrá generar imágenes hasta instalar uno.")
    else:
        yield event("models_checkpoints", "ok",
                    f"{len(checkpoints)} checkpoint(s) encontrado(s)")

    controlnet_models = await client.get_models("controlnet")
    models_status["controlnet"] = controlnet_models
    if not controlnet_models:
        yield event("models_controlnet", "warning",
                    "Sin modelos ControlNet — tab ControlNet deshabilitada")
    else:
        yield event("models_controlnet", "ok",
                    f"{len(controlnet_models)} modelo(s) ControlNet encontrado(s)")

    upscalers = await client.get_models("upscale_models")
    models_status["upscale_models"] = upscalers
    if not upscalers:
        yield event("models_upscale", "warning",
                    "Sin upscalers — tab Upscaler deshabilitada")
    else:
        yield event("models_upscale", "ok",
                    f"{len(upscalers)} upscaler(s) encontrado(s)")

    status_data["models"] = models_status

    # ── Paso 5: Modelos YOLO (ADetailer) ──────────────────────────────────
    yield event("adetailer_models", "info", "Verificando modelos de detector ADetailer…")

    bbox_dir = _get_bbox_dir()
    if bbox_dir is None:
        yield event("adetailer_models", "warning",
                    "Path de modelos no configurado en el hub — "
                    "los detectores YOLO se pueden descargar desde el tab ADetailer.")
    else:
        bbox_dir.mkdir(parents=True, exist_ok=True)
        for det in _YOLO_DETECTORS:
            dest = bbox_dir / det["filename"]
            if dest.exists():
                yield event(f"yolo_{det['filename']}", "ok",
                            f"Detector {det['label']}: {det['filename']}")
            else:
                yield event(f"yolo_{det['filename']}", "info",
                            f"Descargando detector {det['label']} ({det['filename']})…")
                ok, err = await _download_file(det["url"], dest)
                if ok:
                    yield event(f"yolo_{det['filename']}", "ok",
                                f"Detector {det['label']}: descargado")
                else:
                    msg = (f"Detector {det['label']}: error descargando — {err}. "
                           "Puedes descargarlo desde el tab ADetailer.")
                    yield event(f"yolo_{det['filename']}", "warning", msg)
                    status_data["warnings"].append(msg)

    # ── Paso 6: Guardar estado ─────────────────────────────────────────────
    if not status_data["needs_restart"]:
        status_data["ready"] = True

    _save_status(status_data)
    yield event("done", "ok" if status_data["ready"] else "warning",
                "Setup completo" if status_data["ready"]
                else "Setup completo — reinicia ComfyUI para terminar")


def get_status() -> dict:
    """Estado actual del setup (lee painter_setup.json).
    Si ComfyUI no está instalado, retorna flag especial.
    Si el registry tiene nodos nuevos que no estaban en el cache, fuerza re-setup.
    """
    installed, _ = check_comfyui_installed()
    if not installed:
        return {"ready": False, "comfyui_not_installed": True}

    s = _load_status()
    if not s:
        return {"ready": False, "first_run": True}
    # Detectar nodos nuevos en el registry vs. lo que se instaló
    try:
        registry   = _load_registry()
        all_ids    = {e["id"] for e in registry.get("required", []) + registry.get("enhanced", [])}
        cached_ids = set(s.get("installed_node_ids", []))
        if all_ids - cached_ids:
            s = dict(s)
            s["ready"] = False
            s["new_nodes"] = list(all_ids - cached_ids)
    except Exception:
        pass
    return s


async def quick_check() -> bool:
    """Verifica rápido si ComfyUI sigue corriendo (para usos subsiguientes)."""
    client = ComfyClient(port=COMFY_PORT)
    return await client.health_check()
