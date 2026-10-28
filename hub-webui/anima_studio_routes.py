"""Anima Studio API: project libraries, calibration and isolated facial edits."""
import asyncio
import base64
import copy
import io
import json
import os
import secrets
import sys
import time
import uuid
from dataclasses import asdict, fields
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException, Query, WebSocket
from fastapi.responses import FileResponse, JSONResponse
from PIL import Image

import comfy_jobs
from backend_sessions import BackendSessions

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "apps"))
from anima_studio.core.catalog import Catalog
from anima_studio.core.catalog_assets import CatalogAssets
from anima_studio.core.comfy_client import ComfyClient
from anima_studio.core.library import Library, LibraryConflict
from anima_studio.core.storage import StorageLifecycle
from anima_studio.core import pipeline

anima_studio_router = APIRouter(prefix="/api/anima-studio", tags=["anima-studio"])
_library = Library(Path(os.environ.get("ANIMA_STUDIO_DATA", _ROOT / "apps/anima_studio/data")))
_comfy = ComfyClient()


def _backend_bridge():
    from hub_bridge import bridge
    return bridge


_sessions = BackendSessions(_comfy, _backend_bridge)
_jobs: dict[str, dict] = {}
_tasks: set[asyncio.Task] = set()
_active_masters: set[str] = set()
_active_projects: dict[str, int] = {}
_catalog = None
_catalog_path = None
_catalog_result = None
_catalog_assets = None
_catalog_time = 0.0
_catalog_lock = asyncio.Lock()
_KINDS = ("projects", "styles", "pieces", "recipes")
_PIECE_KINDS = ("character", "personality", "outfit", "pose", "expression", "scene", "style")


def _storage() -> StorageLifecycle:
    config_path = Path(os.environ.get("AI_HUB_CONFIG", _ROOT / "hub/hub_config.json"))
    root = _ROOT / "apps/comfyui"
    if config_path.is_file():
        config = json.loads(config_path.read_text(encoding="utf-8"))
        root = Path(config.get("installed_apps", {}).get("comfyui", {}).get("dir") or root)
    return StorageLifecycle(_library, root)


async def _maintain_storage(*, include_orphans=False):
    try:
        storage = _storage()
        if not storage.has_pending():
            return {"cleanup_pending": False, "removed_files": 0, "deleted_images": [], "errors": []}
    except (ValueError, OSError) as exc:
        return {"cleanup_pending": True, "errors": [str(exc)]}
    try:
        queue = await _comfy._get("/queue")
    except Exception:
        return {"cleanup_pending": True, "deferred": True,
                "errors": ["La limpieza se reintentará cuando ComfyUI permita comprobar que está libre."]}
    if (not isinstance(queue, dict) or any(not isinstance(queue.get(key), list)
                                          for key in ("queue_running", "queue_pending"))):
        return {"cleanup_pending": True, "deferred": True,
                "errors": ["ComfyUI no devolvió un estado de cola válido; no se limpiaron archivos."]}
    if queue.get("queue_running") or queue.get("queue_pending"):
        return {"cleanup_pending": True, "deferred": True,
                "errors": ["La limpieza se reintentará al terminar los trabajos de ComfyUI."]}
    try:
        return await asyncio.to_thread(storage.maintain, include_orphans=include_orphans)
    except (ValueError, OSError) as exc:
        return {"cleanup_pending": True, "errors": [str(exc)]}


@comfy_jobs.exclusive("Anima Studio")
async def _automatic_storage_cleanup():
    return await _maintain_storage()


def _models_root() -> Path:
    path = Path(os.environ.get("AI_HUB_CONFIG", _ROOT / "hub/hub_config.json"))
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
        root = Path(config["paths"]["models"])
        if not root.is_dir():
            raise ValueError("La carpeta de modelos configurada no existe.")
        return root
    except (OSError, KeyError, ValueError) as exc:
        raise HTTPException(400, f"Revisa la carpeta de modelos en los ajustes del Hub: {exc}") from exc


def _read(kind: str, item_id: str, revision=None) -> dict:
    try:
        return _library.get(kind, item_id, revision=revision)
    except (ValueError, OSError, KeyError) as exc:
        raise HTTPException(404, str(exc)) from exc


def _project_access(record: dict, project_id: str):
    if record.get("project_id") not in (None, "", project_id):
        raise HTTPException(400, "El recurso pertenece a otro proyecto.")


def _seed(value) -> str:
    if value is None or str(value).strip() in ("", "-1"):
        return str(secrets.randbits(64))
    try:
        number = int(str(value))
        if str(value).strip() != str(number) or not 0 <= number <= 2**64 - 1:
            raise ValueError()
        return str(number)
    except (ValueError, TypeError):
        raise HTTPException(400, "La seed debe ser un entero entre 0 y 18446744073709551615.")


@anima_studio_router.get("/catalog")
async def catalog(refresh: bool = False):
    global _catalog, _catalog_path, _catalog_result, _catalog_time, _catalog_assets
    root = _models_root()
    async with _catalog_lock:
        if _catalog is None or _catalog_path != root:
            _catalog, _catalog_path = Catalog(root), root
            _catalog_result = None
        if refresh or _catalog_result is None or time.monotonic() - _catalog_time > 60:
            config_path = Path(os.environ.get("AI_HUB_CONFIG", _ROOT / "hub/hub_config.json"))
            assets = CatalogAssets(root, config_path.resolve().parent / ".cache/model_vault.db")
            def scan_display():
                return assets.enrich(_catalog.scan())
            _catalog_result = await asyncio.to_thread(scan_display)
            _catalog_assets = assets
            _catalog_time = time.monotonic()
        return copy.deepcopy(_catalog_result)


@anima_studio_router.get("/catalog/thumbnail/{identifier}")
async def catalog_thumbnail(identifier: str):
    await catalog()
    if _catalog_assets is None:
        raise HTTPException(404, "Miniatura no disponible")
    try:
        path, media_type = await asyncio.to_thread(_catalog_assets.thumbnail, identifier)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return FileResponse(path, media_type=media_type, headers={"Cache-Control": "private, max-age=3600"})


@anima_studio_router.get("/status")
async def status():
    return {"comfyui": await _comfy.health_check(), "backend": "Anima / ComfyUI",
            "reference_adapter": False, "pose_control": False,
            "expressions": "inpaint facial + composición exacta",
            "limitations": ["Referencias IP-Adapter y control de pose 3D aún no integrados.",
                            "Txt2img produce RGB; pedir alfa en un perfil no elimina el fondo."]}


@anima_studio_router.post("/session/open")
async def session_open(payload: dict = Body(...)):
    return await _sessions.open(payload.get("session_id"))


@anima_studio_router.post("/session/heartbeat")
async def session_heartbeat(payload: dict = Body(...)):
    return await _sessions.heartbeat(payload.get("session_id"))


@anima_studio_router.post("/session/close")
async def session_close(payload: dict = Body(...)):
    return await _sessions.close(payload.get("session_id"))


@anima_studio_router.websocket("/session/socket/{session_id}")
async def session_socket(websocket: WebSocket, session_id: str):
    await _sessions.socket(websocket, session_id)


@anima_studio_router.get("/sampling-options")
async def sampling_options():
    try:
        info = await _comfy._get("/object_info/KSampler")
        inputs = info["KSampler"]["input"]["required"]
        return {"samplers": inputs["sampler_name"][0], "schedulers": inputs["scheduler"][0]}
    except Exception:
        return {"samplers": ["er_sde", "euler", "euler_ancestral"], "schedulers": ["simple", "normal"]}


def _save(kind: str, payload: dict):
    if kind == "pieces" and payload.get("kind") not in _PIECE_KINDS:
        raise HTTPException(400, "Tipo de pieza desconocido.")
    if kind == "styles":
        config = copy.deepcopy(payload.get("config", {}))
        if "seed" in config:
            config["seed"] = _seed(config["seed"])
        payload = {**payload, "config": config}
    if kind == "recipes":
        payload = copy.deepcopy(payload)
        payload.setdefault("piece_revisions", {
            item_id: _read("pieces", item_id)["revision"] for item_id in payload.get("piece_ids", [])})
        if payload.get("style_id") and not payload.get("style_revision"):
            payload["style_revision"] = _read("styles", payload["style_id"])["revision"]
        if payload.get("format_id") and not payload.get("format_snapshot"):
            project = _read("projects", payload["project_id"])
            formats = project.get("formats", [])
            snapshot = next((item for item in formats if item["id"] == payload["format_id"]), None)
            if snapshot is None:
                raise HTTPException(400, "El formato no pertenece al proyecto.")
            payload["format_snapshot"] = copy.deepcopy(snapshot)
    try:
        return _library.save(kind, payload)
    except (ValueError, OSError, KeyError) as exc:
        raise HTTPException(400, str(exc)) from exc


def _register(kind):
    async def list_items(project_id: str | None = None):
        return _library.list(kind, project_id=project_id)
    async def save_item(payload: dict = Body(...)):
        return _save(kind, payload)
    async def delete_item(item_id: str):
        try:
            if kind == "projects" and _active_projects.get(item_id):
                raise LibraryConflict("Este proyecto tiene una generación en curso. Espera a que termine.")
            return _library.delete(kind, item_id)
        except LibraryConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except (ValueError, OSError) as exc:
            raise HTTPException(400, str(exc)) from exc
    anima_studio_router.add_api_route(f"/{kind}", list_items, methods=["GET"], name=f"list_{kind}")
    anima_studio_router.add_api_route(f"/{kind}", save_item, methods=["POST"], name=f"save_{kind}")
    anima_studio_router.add_api_route(f"/{kind}/{{item_id}}", delete_item,
                                      methods=["DELETE"], name=f"delete_{kind}")


for _kind in _KINDS:
    _register(_kind)


@anima_studio_router.get("/bootstrap")
async def bootstrap():
    await asyncio.to_thread(_library.recover_pending_metadata_deletions)
    has_project_history = (any(_library._path("revisions", "projects").glob("*"))
                           or any(_library._path(".trash", "projects").glob("*")))
    if not _library.initialized and (_library.list("projects") or has_project_history):
        _library.mark_initialized()
    if not _library.list("projects") and not _library.initialized:
        for path in sorted((_ROOT / "apps/anima_studio/templates").glob("*.json")):
            _save("projects", json.loads(path.read_text(encoding="utf-8")))
    try:
        models, catalog_error = await catalog(), ""
    except HTTPException as exc:
        models, catalog_error = {"models": [], "loras": [], "text_encoders": [], "vaes": []}, exc.detail
    try:
        cleanup = await _automatic_storage_cleanup()
    except HTTPException as exc:
        if exc.status_code != 409:
            raise
        cleanup = {"cleanup_pending": True, "deferred": True, "errors": []}
    return {**{kind: _library.list(kind) for kind in _KINDS},
            "generations": _library.list_generations(), "catalog": models,
            "catalog_error": catalog_error, "status": await status(), "storage_cleanup": cleanup}


@anima_studio_router.get("/projects/{project_id}/export")
async def export_project(project_id: str):
    project = _read("projects", project_id)
    resources = {kind: _library.list(kind, project_id=project_id) for kind in _KINDS[1:]}
    revisions = {}
    for kind, entries in resources.items():
        revisions[kind] = {}
        for entry in entries:
            history = []
            for revision in range(1, entry["revision"] + 1):
                try:
                    history.append(_library.get(kind, entry["id"], revision))
                except ValueError:
                    continue  # an interrupted prior save may leave a revision gap
            revisions[kind][entry["id"]] = history
    return JSONResponse({"schema": "anima-studio-profile-v2", "project": project,
                         **resources, "revisions": revisions},
                        headers={"Content-Disposition": f'attachment; filename="anima-project-{project_id}.json"'})


@anima_studio_router.post("/projects/import")
async def import_project(payload: dict = Body(...)):
    import tempfile

    # Validate the entire bundle in an isolated store before publishing any
    # project/resource. Old style/piece revisions retain their meaning after ID
    # remapping; a v1 bundle missing a pinned revision is rejected explicitly.
    try:
        bundle = payload if "project" in payload else {"project": payload}
        if bundle.get("schema") not in (None, "anima-studio-profile-v1", "anima-studio-profile-v2"):
            raise ValueError("Versión de perfil no compatible.")
        if not isinstance(bundle.get("project"), dict):
            raise ValueError("El perfil debe contener un proyecto.")
        origin_project = bundle["project"].get("id")
        histories = bundle.get("revisions", {})
        if not isinstance(histories, dict):
            raise ValueError("Las revisiones del perfil deben ser un objeto.")
        resources, mapping, current_revisions = {}, {}, {}
        total_revisions = 0
        for kind in ("pieces", "styles", "recipes"):
            entries = bundle.get(kind, [])
            if not isinstance(entries, list) or len(entries) > 1000:
                raise ValueError("Cada biblioteca importada admite hasta 1000 recursos.")
            resources[kind], mapping[kind], current_revisions[kind] = [], {}, {}
            kind_history = histories.get(kind, {})
            if not isinstance(kind_history, dict):
                raise ValueError("Formato de revisiones no válido.")
            for entry in entries:
                if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not entry["id"]:
                    raise ValueError("Cada recurso importado necesita un identificador.")
                entry_id = entry["id"]
                if entry_id in mapping[kind]:
                    raise ValueError("Hay identificadores duplicados en el perfil.")
                snapshots = kind_history.get(entry_id, [entry])
                if not isinstance(snapshots, list) or not snapshots:
                    raise ValueError("Faltan las revisiones de un recurso.")
                versions = []
                for snapshot in snapshots:
                    if not isinstance(snapshot, dict) or snapshot.get("id") != entry_id:
                        raise ValueError("Una revisión no coincide con su recurso.")
                    revision = snapshot.get("revision", 1)
                    if type(revision) is not int or revision < 1:
                        raise ValueError("Número de revisión no válido.")
                    if snapshot.get("project_id") not in (None, "", origin_project):
                        raise ValueError("El perfil contiene un recurso de otro proyecto.")
                    versions.append((revision, snapshot))
                versions.sort(key=lambda item: item[0])
                if len({revision for revision, _ in versions}) != len(versions):
                    raise ValueError("Hay revisiones duplicadas.")
                current_revision = entry.get("revision", 1)
                if type(current_revision) is not int or current_revision != versions[-1][0]:
                    raise ValueError("La revisión actual no coincide con el historial exportado.")
                if versions[-1][1] != entry:
                    raise ValueError("El contenido actual no coincide con su última revisión.")
                total_revisions += len(versions)
                if total_revisions > 5000:
                    raise ValueError("El perfil excede 5000 revisiones.")
                resources[kind].append((entry_id, versions))
                mapping[kind][entry_id] = uuid.uuid4().hex
                current_revisions[kind][entry_id] = current_revision
            if set(kind_history) - set(mapping[kind]):
                raise ValueError("Hay revisiones sin un recurso asociado.")

        stage_parent = _library._path(".imports")
        stage_parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="profile-", dir=stage_parent) as temporary:
            stage_root = Path(temporary).resolve()
            if not stage_root.is_relative_to(_library.root.resolve()):
                raise ValueError("Ruta temporal no válida.")
            stage = Library(stage_root)
            project_data = {key: value for key, value in bundle["project"].items()
                            if key not in {"id", "revision", "created_at", "updated_at"}}
            project = stage.save("projects", project_data)
            revision_map = {kind: {} for kind in resources}

            def reference(kind, old_id, old_revision=None):
                if old_id not in mapping[kind]:
                    raise ValueError(f"Falta un recurso requerido de {kind}.")
                revision = current_revisions[kind][old_id] if old_revision is None else old_revision
                if type(revision) is not int or (old_id, revision) not in revision_map[kind]:
                    raise ValueError("Falta una revisión fijada por una receta; vuelve a exportar el perfil completo.")
                return mapping[kind][old_id], revision_map[kind][(old_id, revision)]

            # Dependencies are imported first. All historical revisions are
            # reconstructed in order, including ones no current recipe uses.
            for kind in ("pieces", "styles", "recipes"):
                for old_id, versions in resources[kind]:
                    for old_revision, snapshot in versions:
                        data = copy.deepcopy(snapshot)
                        for key in ("id", "revision", "created_at", "updated_at"):
                            data.pop(key, None)
                        data.update(id=mapping[kind][old_id], project_id=project["id"],
                                    imported_from={"id": old_id, "revision": old_revision})
                        if data.get("sample_ids"):
                            data["source_sample_ids"] = data.pop("sample_ids")
                        if kind == "recipes":
                            if data.get("style_id"):
                                data["style_id"], data["style_revision"] = reference(
                                    "styles", data["style_id"], data.get("style_revision"))
                            piece_ids = data.get("piece_ids", [])
                            if not isinstance(piece_ids, (list, dict)):
                                raise ValueError("Las piezas de una receta deben ser una lista u objeto.")
                            old_pins = data.get("piece_revisions", {})
                            if not isinstance(old_pins, dict):
                                raise ValueError("Las revisiones de piezas deben ser un objeto.")
                            pins = {}

                            def remap_piece(old_piece_id):
                                new_id, revision = reference("pieces", old_piece_id, old_pins.get(old_piece_id))
                                pins[new_id] = revision
                                return new_id

                            data["piece_ids"] = ({key: remap_piece(value) for key, value in piece_ids.items()}
                                                 if isinstance(piece_ids, dict)
                                                 else [remap_piece(value) for value in piece_ids])
                            data["piece_revisions"] = pins
                        saved = stage.save(kind, data)
                        revision_map[kind][(old_id, old_revision)] = saved["revision"]

            # Publish all validated files under one library lock. Newly allocated
            # UUIDs mean nothing existing is replaced; roll back on disk failure.
            moved = []
            with _library._lock:
                try:
                    for source in sorted(stage_root.rglob("*.json")):
                        destination = _library._path(source.relative_to(stage_root).as_posix())
                        if destination.exists():
                            raise ValueError("El identificador importado ya existe.")
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        os.replace(source, destination)
                        moved.append((source, destination))
                except BaseException:
                    for source, destination in reversed(moved):
                        source.parent.mkdir(parents=True, exist_ok=True)
                        os.replace(destination, source)
                    raise
            return project
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise HTTPException(400, f"No se importó el perfil: {exc}") from exc


@anima_studio_router.post("/compose")
async def compose(payload: dict = Body(...)):
    project_id = payload.get("project_id", "")
    project = _read("projects", project_id)
    style = None
    if payload.get("style_id"):
        style = _read("styles", payload["style_id"], payload.get("style_revision"))
        _project_access(style, project_id)
    revisions = payload.get("piece_revisions", {})
    pieces = [_read("pieces", value, revisions.get(value)) for value in payload.get("piece_ids", [])]
    if len(pieces) > 24:
        raise HTTPException(400, "Selecciona hasta 24 piezas por receta.")
    for piece in pieces:
        _project_access(piece, project_id)
    order = {name: index for index, name in enumerate(_PIECE_KINDS)}
    pieces.sort(key=lambda item: (order.get(item.get("kind"), 99), item["id"]))
    blocks = ([style] if style else []) + pieces
    prompt = "\n".join(str(item.get("prompt", "")).strip() for item in blocks if item.get("prompt"))
    negative = "\n".join(dict.fromkeys(str(item.get("negative", "")).strip() for item in blocks
                                      if item.get("negative")))
    prompt = "\n".join(value for value in (prompt, str(payload.get("prompt", "")).strip()) if value)
    negative = "\n".join(value for value in (negative, str(payload.get("negative", "")).strip()) if value)
    config = copy.deepcopy(style.get("config", {}) if style else {})
    config.update(copy.deepcopy(payload.get("config", {})))
    if payload.get("format_id"):
        fmt = payload.get("format_snapshot")
        if fmt is not None and (not isinstance(fmt, dict) or fmt.get("id") != payload["format_id"]):
            raise HTTPException(400, "La copia guardada del formato no coincide con su identificador.")
        if fmt is None:
            fmt = next((item for item in project.get("formats", []) if item["id"] == payload["format_id"]), None)
        if fmt is None:
            raise HTTPException(400, "El formato no pertenece al proyecto.")
        config.update(width=fmt["width"], height=fmt["height"])
    return {"prompt": prompt, "negative": negative, "config": config,
            "blocks": [{"kind": item.get("kind", "style"), "name": item["name"],
                        "prompt": item.get("prompt", "")} for item in blocks],
            "snapshot": {"project": project, "style": style, "pieces": pieces}}


def _image(value: str) -> bytes:
    try:
        encoded = value.split(",", 1)[-1]
        if len(encoded) > 32 * 1024 * 1024:
            raise ValueError("La imagen supera 24 MiB.")
        raw = base64.b64decode(encoded, validate=True)
        with Image.open(io.BytesIO(raw)) as image:
            if image.width * image.height > 16_777_216:
                raise ValueError("La imagen supera 16 megapíxeles.")
            image.verify()
        return raw
    except Exception as exc:
        raise HTTPException(400, f"Imagen o máscara no válida: {exc}") from exc


@anima_studio_router.post("/generate")
@comfy_jobs.exclusive("Anima Studio")
@_sessions.job
async def generate(payload: dict = Body(...)):
    release = _pin_project(payload.get("project_id", ""))
    try:
        return await _generate(payload)
    finally:
        release()


def _pin_project(project_id: str):
    project_id = project_id if isinstance(project_id, str) else ""
    _active_projects[project_id] = _active_projects.get(project_id, 0) + 1
    released = False

    def release():
        nonlocal released
        if not released:
            released = True
            remaining = _active_projects[project_id] - 1
            if remaining:
                _active_projects[project_id] = remaining
            else:
                _active_projects.pop(project_id, None)
    return release


async def _generate(payload: dict):
    assembled = await compose(payload)
    if not assembled["prompt"].strip():
        raise HTTPException(400, "Escribe un prompt o selecciona piezas.")
    if payload.get("mode") == "expression" and not str(payload.get("prompt", "")).strip():
        if not any(item.get("kind") == "expression" and item.get("prompt", "").strip()
                   for item in assembled["snapshot"]["pieces"]):
            raise HTTPException(400, "Selecciona una expresión guardada o descríbela en las instrucciones.")
    config = assembled["config"]
    config["seed"] = _seed(config.get("seed", "42"))
    available = await catalog()
    for key, name in (("text_encoder", "text_encoders"), ("vae", "vaes")):
        if not config.get(key) and available[name]:
            config[key] = available[name][0]["file"]
    try:
        supported = {item.name for item in fields(pipeline.GenParams)}
        params = pipeline.GenParams(**{key: val for key, val in {
            **config, "prompt": assembled["prompt"], "negative": assembled["negative"],
            "mode": payload.get("mode", "txt2img")}.items() if key in supported})
        params = pipeline.validate_params(params)
        selected_files = await asyncio.to_thread(_catalog.validate, params)
        config = asdict(params)
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    if params.mode not in ("txt2img", "expression"):
        raise HTTPException(400, "Modo de generación desconocido.")
    categories = assembled["snapshot"]["project"].get("categories", ["characters"])
    category_ids = [item.get("id") if isinstance(item, dict) else item for item in categories]
    category = payload.get("category") or category_ids[0]
    if category == "calibration":
        category = category_ids[0]
    if category not in category_ids:
        raise HTTPException(400, "La categoría no pertenece al proyecto.")
    if not await _comfy.health_check():
        raise HTTPException(503, "ComfyUI aún no está disponible. Espera al inicio o reintenta desde el taller.")
    master = mask = None
    master_record = None
    if params.mode == "expression":
        if payload.get("master_asset_id"):
            try:
                master_record = _library.get_generation(payload["master_asset_id"])
                _project_access(master_record, payload["project_id"])
                master = _library.image_path(master_record["id"]).read_bytes()
                params.master_seed = str(master_record.get("seed", master_record.get("config", {}).get("seed", "")))
            except (ValueError, OSError) as exc:
                raise HTTPException(400, str(exc)) from exc
        elif payload.get("master_base64"):
            master = _image(payload["master_base64"])
        if master is None or not payload.get("mask_base64"):
            raise HTTPException(400, "Selecciona un maestro y pinta una máscara facial.")
        mask = _image(payload["mask_base64"])
    job_id = uuid.uuid4().hex
    _jobs[job_id] = {"job_id": job_id, "status": "running", "phase": "Preparando", "progress": 0}
    # Completed jobs are small records; retain only the most recent 100.
    for key in list(_jobs)[:-100]:
        if _jobs[key]["status"] != "running":
            del _jobs[key]

    def progress(phase, detail=None):
        _jobs[job_id].update(phase=phase)
        if detail:
            _jobs[job_id]["progress"] = {"value": detail.get("step", 0),
                                         "max": detail.get("total", 1)}

    async def worker():
        run_id = uuid.uuid4().hex
        try:
            storage = _storage()
            storage.artifacts.start_run(run_id)
            result = await pipeline.generate(_comfy, params, progress, master_bytes=master, mask_bytes=mask,
                artifact_run_id=run_id,
                on_artifact=lambda item: storage.artifacts.record_artifact(run_id, item))
            project = assembled["snapshot"]["project"]
            pieces = assembled["snapshot"]["pieces"]
            names = {piece["kind"]: piece["name"] for piece in pieces}
            metadata = {**result["metadata"], "project_id": project["id"],
                        "project_name": project["name"], "snapshot": assembled["snapshot"],
                        "selected_files": selected_files,
                        "config": {**config, **result["metadata"].get("params", {}),
                                   "seed": str(params.seed)}, "seed": str(params.seed),
                        "prompt": assembled["prompt"], "negative": assembled["negative"],
                        "category": category,
                        "character_name": payload.get("character") or names.get("character", ""),
                        "outfit_name": payload.get("outfit") or names.get("outfit", ""),
                        "expression_name": payload.get("expression") or names.get("expression", ""),
                        "master_id": payload.get("master_asset_id"),
                        "master_asset_id": payload.get("master_asset_id"),
                        "purpose": payload.get("purpose", "production")}
            if isinstance(payload.get("calibration"), dict):
                metadata["calibration"] = copy.deepcopy(payload["calibration"])
            extras = {key: result[key] for key in ("patch", "mask", "generation_mask") if result.get(key)}
            if master is not None:
                extras["master"] = master
            record = await asyncio.to_thread(_library.save_generation, metadata, result["image"], extras)
            try:
                cleaned = await asyncio.to_thread(storage.complete_run, run_id,
                                                  result["metadata"].get("backend_artifacts", []))
                if cleaned["errors"]:
                    _jobs[job_id]["warning"] = "El resultado está guardado; queda una limpieza pendiente que se reintentará al actualizar."
            except (ValueError, OSError):
                _jobs[job_id]["warning"] = "El resultado está guardado; queda una limpieza pendiente que se reintentará al actualizar."
            _jobs[job_id].update(status="done", progress=100, phase="Terminado", result=record)
        except asyncio.CancelledError:
            _jobs[job_id].update(status="error", error="Tarea cancelada; comprueba el estado de ComfyUI.")
            raise
        except Exception as exc:
            _jobs[job_id].update(status="error", phase="Error", error=str(exc))

    if master_record:
        _active_masters.add(master_record["id"])
    release_project = _pin_project(payload["project_id"])
    try:
        task = comfy_jobs.spawn(worker())
    except BaseException:
        release_project()
        if master_record:
            _active_masters.discard(master_record["id"])
        raise
    _tasks.add(task)
    _sessions.track_worker(task)
    task.add_done_callback(_tasks.discard)
    task.add_done_callback(lambda _: release_project())
    if master_record:
        task.add_done_callback(lambda _: _active_masters.discard(master_record["id"]))
    return {"job_id": job_id}


@anima_studio_router.get("/jobs/{job_id}")
async def job(job_id: str):
    if job_id not in _jobs:
        raise HTTPException(404, "Trabajo no encontrado.")
    return _jobs[job_id]


@anima_studio_router.post("/interrupt")
@comfy_jobs.exclusive("Anima Studio", control=True)
async def interrupt():
    await _comfy.interrupt()
    return {"ok": True}


@anima_studio_router.get("/generations")
async def generations(project_id: str | None = None, status: str | None = None):
    return _library.list_generations(project_id=project_id, status=status)


@anima_studio_router.get("/generations/{asset_id}")
async def generation(asset_id: str):
    try:
        return _library.get_generation(asset_id)
    except (ValueError, OSError) as exc:
        raise HTTPException(404, str(exc)) from exc


@anima_studio_router.get("/images/{asset_id}")
async def image(asset_id: str, variant: str = "image", download: bool = False):
    try:
        path = _library.image_path(asset_id, variant=variant)
        return FileResponse(path, media_type="image/png", filename=path.name if download else None)
    except (ValueError, OSError) as exc:
        raise HTTPException(404, str(exc)) from exc


@anima_studio_router.post("/generations/{asset_id}/review")
async def review(asset_id: str, payload: dict = Body(...)):
    try:
        return _library.review(asset_id, payload.get("status", ""), payload.get("reason", ""))
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc)) from exc


@anima_studio_router.delete("/generations/{asset_id}")
@comfy_jobs.exclusive("Anima Studio")
async def delete_generation(asset_id: str):
    try:
        if asset_id in _active_masters:
            raise LibraryConflict("Esta imagen se está utilizando como maestro en una generación en curso.")
        result = _library.delete_generation(asset_id)
        cleanup = await _maintain_storage()
        return {**result, "cleanup_pending": cleanup.get("cleanup_pending", False),
                "cleanup_errors": cleanup.get("errors", [])}
    except LibraryConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc)) from exc


@anima_studio_router.post("/rejected/purge")
@comfy_jobs.exclusive("Anima Studio")
async def purge(payload: dict = Body(...)):
    try:
        ids = payload.get("ids", [])
        if not isinstance(ids, list) or len(ids) > 1000:
            raise ValueError("Selecciona hasta 1000 rechazados.")
        pinned = list(dict.fromkeys(item for item in ids if item in _active_masters))
        result = _library.purge_rejected([item for item in ids if item not in _active_masters],
                                        int(payload.get("min_age_days", 0)))
        result["protected"] = list(dict.fromkeys([*result["protected"], *pinned]))
        cleanup = await _maintain_storage()
        return {**result, "cleanup_pending": cleanup.get("cleanup_pending", False),
                "cleanup_errors": cleanup.get("errors", [])}
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc)) from exc


@anima_studio_router.post("/storage/cleanup")
@comfy_jobs.exclusive("Anima Studio")
async def cleanup_storage(payload: dict = Body(default={})):
    return await _maintain_storage(include_orphans=payload.get("include_orphans") is True)
