"""
Painter — setup de primer uso.
Verifica ComfyUI, instala custom nodes, detecta modelos disponibles.
"""
import json
import os
import subprocess
import asyncio
from pathlib import Path
from typing import AsyncGenerator

from comfy_client import ComfyClient

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


def _git_clone(repo: str, dest: Path) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            ["git", "clone", "--depth", "1", repo, str(dest)],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode != 0:
            return False, result.stderr.strip()
        return True, ""
    except Exception as e:
        return False, str(e)


def _pip_cmd(comfyui_dir: Path) -> list[str]:
    """Devuelve el comando base de pip para el venv de ComfyUI."""
    uv     = comfyui_dir / "venv" / "bin" / "uv"
    pip    = comfyui_dir / "venv" / "bin" / "pip"
    python = comfyui_dir / "venv" / "bin" / "python"
    if uv.exists():
        # uv necesita --python explícito cuando no hay venv activo en el subprocess
        return [str(uv), "pip", "install", "--python", str(python)]
    return [str(pip), "install"]


def _pip_install(comfyui_dir: Path, node_dir: Path) -> tuple[bool, str]:
    req = node_dir / "requirements.txt"
    if not req.exists():
        return True, ""

    lines = [l.strip() for l in req.read_text(encoding="utf-8").splitlines()
             if l.strip() and not l.startswith("#")]
    normal = [l for l in lines if not l.startswith("git+")]
    git_deps = [l for l in lines if l.startswith("git+")]

    pip = _pip_cmd(comfyui_dir)

    # Instalar deps normales — requeridas para que el nodo cargue
    if normal:
        try:
            result = subprocess.run(pip + normal, capture_output=True, text=True, timeout=300)
            if result.returncode != 0:
                return False, result.stderr.strip()
        except Exception as e:
            return False, str(e)

    # Instalar deps git (ej. sam2) — best-effort, no bloquear el setup si fallan
    for dep in git_deps:
        try:
            subprocess.run(pip + [dep], capture_output=True, text=True, timeout=180)
        except Exception:
            pass

    return True, ""


def _pip_install_packages(comfyui_dir: Path, packages: list[str]) -> tuple[bool, str]:
    """Instala paquetes pip individuales en el venv de ComfyUI."""
    if not packages:
        return True, ""
    cmd = _pip_cmd(comfyui_dir) + packages
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            return False, result.stderr.strip()
        return True, ""
    except Exception as e:
        return False, str(e)


def _install_local_node(comfyui_dir: Path, local_src: str) -> tuple[bool, str]:
    """
    Copia el nodo local al directorio custom_nodes de ComfyUI.
    local_src es relativo a _ROOT (ej: 'apps/painter/comfyui_nodes/my_node.py').
    """
    src  = _ROOT / local_src
    if not src.exists():
        return False, f"Archivo local no encontrado: {src}"
    dest_dir = comfyui_dir / "custom_nodes"
    dest     = dest_dir / src.name
    try:
        import shutil
        shutil.copy2(src, dest)
        return True, ""
    except Exception as e:
        return False, str(e)



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
    custom_nodes_dir = comfyui_dir / "custom_nodes"
    new_nodes_installed = False

    all_nodes = [
        (entry, "required")  for entry in registry.get("required", [])
    ] + [
        (entry, "enhanced")  for entry in registry.get("enhanced", [])
    ]

    for entry, kind in all_nodes:
        node_id    = entry["id"]
        probe_node = entry["probe_node"]
        reason     = entry["reason"]
        local_src  = entry.get("local_src")   # nodo local (no git)
        repo       = entry.get("repo")        # nodo externo (git)

        # Verificar si ya está cargado en ComfyUI
        already_loaded = await client.probe_node(probe_node)
        if already_loaded:
            yield event(f"node_{node_id}", "ok", f"{node_id} — ya cargado")
            continue

        # ── Nodo local (painter_sam.py, etc.) ────────────────────────────
        if local_src:
            dest_file = custom_nodes_dir / Path(local_src).name
            file_already_existed = dest_file.exists()

            if not file_already_existed:
                yield event(f"node_{node_id}", "info", f"Instalando {node_id} ({reason})…")
                ok, err = _install_local_node(comfyui_dir, local_src)
                if not ok:
                    msg = f"Error copiando {node_id}: {err}"
                    if kind == "required":
                        yield event(f"node_{node_id}", "error", msg)
                        return
                    else:
                        yield event(f"node_{node_id}", "warning", msg)
                        status_data["warnings"].append(msg)
                        continue

            # Instalar pip_packages aunque el archivo ya existiera
            # (puede que un run anterior copiara el archivo pero fallara el pip install)
            pip_pkgs = entry.get("pip_packages", [])
            if pip_pkgs:
                yield event(f"node_{node_id}", "info",
                            f"Verificando dependencias pip de {node_id}…")
                ok, err = _pip_install_packages(comfyui_dir, pip_pkgs)
                if not ok:
                    msg = f"Error instalando deps de {node_id}: {err}"
                    if kind == "required":
                        yield event(f"node_{node_id}", "error", msg)
                        return
                    else:
                        yield event(f"node_{node_id}", "warning", msg)
                        status_data["warnings"].append(msg)
                        continue

            if file_already_existed:
                # Archivo ya estaba; ComfyUI necesita reiniciarse para cargarlo
                status_data["needs_restart"] = True
                yield event(f"node_{node_id}", "info",
                            f"{node_id} — instalado, ComfyUI necesita reiniciarse")
            else:
                new_nodes_installed = True
                yield event(f"node_{node_id}", "ok", f"{node_id} instalado correctamente")
            continue

        # ── Nodo externo (git clone) ──────────────────────────────────────
        dest = custom_nodes_dir / node_id
        if dest.exists():
            # El directorio ya existe pero probe_node falló.
            # Si ComfyUI está corriendo, es un error de carga (dep faltante) — re-instalar.
            # Si ComfyUI está offline, simplemente necesita reiniciarse.
            comfy_alive = await client.health_check()
            if comfy_alive:
                yield event(f"node_{node_id}", "info",
                            f"{node_id} instalado pero no cargado — reinstalando dependencias…")
                ok, err = _pip_install(comfyui_dir, dest)
                if not ok:
                    msg = f"Error reinstalando deps de {node_id}: {err}"
                    if kind == "required":
                        yield event(f"node_{node_id}", "error", msg)
                        return
                    else:
                        yield event(f"node_{node_id}", "warning", msg)
                        status_data["warnings"].append(msg)
                        continue
                else:
                    yield event(f"node_{node_id}", "info",
                                f"{node_id} — dependencias reinstaladas, reinicia ComfyUI")
            status_data["needs_restart"] = True
            yield event(f"node_{node_id}", "info",
                        f"{node_id} — instalado, ComfyUI necesita reiniciarse")
            continue

        yield event(f"node_{node_id}", "info", f"Instalando {node_id} ({reason})…")
        ok, err = _git_clone(repo, dest)
        if not ok:
            msg = f"Error clonando {node_id} desde {repo}: {err}"
            if kind == "required":
                yield event(f"node_{node_id}", "error", msg)
                return
            else:
                yield event(f"node_{node_id}", "warning", msg)
                fallback = entry.get("fallback_workflow")
                if fallback:
                    status_data["fallbacks"][node_id] = fallback
                    status_data["warnings"].append(msg)
                continue

        yield event(f"node_{node_id}", "info", f"Instalando dependencias de {node_id}…")
        ok, err = _pip_install(comfyui_dir, dest)
        if not ok:
            msg = f"Error instalando deps de {node_id}: {err}"
            if kind == "required":
                yield event(f"node_{node_id}", "error", msg)
                return
            else:
                yield event(f"node_{node_id}", "warning", msg)
                fallback = entry.get("fallback_workflow")
                if fallback:
                    status_data["fallbacks"][node_id] = fallback
                    status_data["warnings"].append(msg)
                continue

        new_nodes_installed = True
        yield event(f"node_{node_id}", "ok", f"{node_id} instalado correctamente")

    if new_nodes_installed:
        status_data["needs_restart"] = True
        yield event("restart_required", "warning",
                    "Se instalaron nuevos nodos. ComfyUI necesita reiniciarse para cargarlos.")

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
